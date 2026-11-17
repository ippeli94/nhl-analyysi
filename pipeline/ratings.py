"""Time-weighted Poisson team ratings and period split (spec 6.2, 6.4, 6.8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

from pipeline import config
from pipeline.config import ModelParams

PERIOD_COLS = ("target_p1", "target_p2", "target_p3")


@dataclass
class Ratings:
    intercept: float
    attack: dict[str, float] = field(default_factory=dict)
    defense: dict[str, float] = field(default_factory=dict)
    home: float = 0.0
    b2b_att: float = 0.0
    b2b_def: float = 0.0
    n_games: dict[str, int] = field(default_factory=dict)


@dataclass
class PeriodShares:
    league: list[float]
    scored: dict[str, list[float]]
    allowed: dict[str, list[float]]


def weights(tg: pd.DataFrame, asof: date, p: ModelParams) -> np.ndarray:
    """Per-row weight for a fit as of `asof`; rows on or after `asof` get 0."""
    asof_ts = pd.Timestamp(asof)
    dates = tg["date"]
    before = (dates < asof_ts).to_numpy()
    w = np.zeros(len(tg))
    if not before.any():
        return w
    seasons = tg["season"].to_numpy()
    asof_season = seasons[before].max()
    cur = seasons == asof_season
    current_start = dates[before & cur].min()
    age = np.full(len(tg), np.nan)
    mult = np.ones(len(tg))

    same = before & cur
    age[same] = (asof_ts - dates[same]).dt.days.to_numpy()

    older = sorted(s for s in set(seasons) if s < asof_season)
    if older:
        prev = seasons == older[-1]
        prev_end = dates[prev].max()
        sel = before & prev
        age[sel] = (
            (asof_ts - current_start).days
            + p.offseason_gap_days
            + (prev_end - dates[sel]).dt.days.to_numpy()
        )
        mult[sel] = p.prev_season_weight

    ok = ~np.isnan(age)
    w[ok] = mult[ok] * 0.5 ** (age[ok] / p.half_life_days)
    return w


def fit(tg: pd.DataFrame, asof: date, p: ModelParams) -> Ratings:
    w = weights(tg, asof, p)
    keep = (w > 0) & tg["game_type"].isin(config.TRAIN_GAME_TYPES).to_numpy()
    rows = tg[keep]
    w = w[keep]
    if len(rows) < config.MIN_FIT_ROWS:
        mean = config.LEAGUE_GOALS_FALLBACK
        if len(rows) and w.sum() > 0:
            mean = float(np.dot(w, rows["target"].to_numpy(dtype=float)) / w.sum())
        if mean <= 0:
            mean = config.LEAGUE_GOALS_FALLBACK
        return Ratings(intercept=float(np.log(mean)))

    teams = sorted(set(rows["team"]) | set(rows["opp"]))
    index = {t: i for i, t in enumerate(teams)}
    n, k = len(rows), len(teams)
    ti = rows["team"].map(index).to_numpy()
    oi = rows["opp"].map(index).to_numpy()
    X = np.zeros((n, 2 * k + 3))
    r = np.arange(n)
    X[r, ti] = 1.0
    X[r, k + oi] = 1.0
    X[:, 2 * k] = rows["is_home"].to_numpy(dtype=float)
    X[:, 2 * k + 1] = rows["b2b"].to_numpy(dtype=float)
    X[:, 2 * k + 2] = rows["opp_b2b"].to_numpy(dtype=float)

    model = PoissonRegressor(alpha=p.alpha, max_iter=300)
    model.fit(X, rows["target"].to_numpy(dtype=float), sample_weight=w)
    c = model.coef_

    cur_rows = rows[rows["season"] == rows["season"].max()]
    counts = cur_rows["team"].value_counts()
    return Ratings(
        intercept=float(model.intercept_),
        attack={t: float(c[i]) for t, i in index.items()},
        defense={t: float(c[k + i]) for t, i in index.items()},
        home=float(c[2 * k]),
        b2b_att=float(c[2 * k + 1]),
        b2b_def=float(c[2 * k + 2]),
        n_games={str(t): int(v) for t, v in counts.items()},
    )


def base_lambda(r: Ratings, team: str, opp: str) -> float:
    return float(np.exp(r.intercept + r.attack.get(team, 0.0) + r.defense.get(opp, 0.0)))


def home_mult(r: Ratings) -> float:
    return float(np.exp(r.home))


def rest_mult(r: Ratings, b2b: bool, opp_b2b: bool) -> float:
    return float(np.exp(r.b2b_att * bool(b2b) + r.b2b_def * bool(opp_b2b)))


def period_shares(tg: pd.DataFrame, asof: date, p: ModelParams) -> PeriodShares:
    """League and shrunk team period shares from current-season rows before `asof`."""
    fallback = PeriodShares(list(config.LEAGUE_PERIOD_SHARE_FALLBACK), {}, {})
    before = tg[(tg["date"] < pd.Timestamp(asof)) & tg["game_type"].isin(config.TRAIN_GAME_TYPES)]
    if before.empty:
        return fallback
    rows = before[before["season"] == before["season"].max()]
    cols = list(PERIOD_COLS)
    period_sums = rows[cols].sum().to_numpy(dtype=float)
    if period_sums.sum() <= 0:
        return fallback
    league = period_sums / period_sums.sum()
    prior = p.period_prior_games * float(rows["target"].mean())

    def shrunk(key: str) -> dict[str, list[float]]:
        g = rows.groupby(key)
        sums = g[cols].sum()
        tot = g["target"].sum()
        shares = (sums.to_numpy(dtype=float) + prior * league) / (tot.to_numpy()[:, None] + prior)
        return {str(t): [float(x) for x in row] for t, row in zip(sums.index, shares, strict=True)}

    return PeriodShares(
        league=[float(x) for x in league],
        scored=shrunk("team"),
        allowed=shrunk("opp"),
    )


def split_lambda(
    lam: float, team: str, opp: str, ps: PeriodShares, use_periods: bool
) -> np.ndarray:
    league = np.asarray(ps.league, dtype=float)
    if not use_periods or team not in ps.scored:
        return lam * league
    allowed = np.asarray(ps.allowed[opp], dtype=float) if opp in ps.allowed else league
    raw = np.asarray(ps.scored[team], dtype=float) * allowed / league
    return lam * raw / raw.sum()
