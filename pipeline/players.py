"""Player table, shrunk per-60 rates and the season baseline (spec 6.10; plan decisions 19, 27)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import pandas as pd

from pipeline import config, ratings, xg
from pipeline.config import ModelParams, PlayerParams

PG_COLUMNS = [
    "game_id", "season", "game_type", "date", "team", "opp", "player", "name", "pos", "grp",
    "toi", "g", "a", "pts", "ixg", "team_goals",
]
Slot = dict  # {"id": int, "name": str | None, "pos": str, "toi_proj": float}


def _grp(pos: str) -> str:
    return "D" if pos == "D" else "F"


def player_games(records: list[dict], coefs: dict) -> pd.DataFrame:
    """One row per skater per cached game; ixG = non-empty-net shot xG + empty-net goals."""
    shots = xg.shots_frame(records)
    shots["xg"] = xg.score(shots, coefs) if len(shots) else np.array([])
    en_goal = shots["empty_net"].astype(bool) & (shots["ev"] == "goal")
    shots["val"] = np.where(en_goal, 1.0, shots["xg"].fillna(0.0))
    shots = shots[shots["shooter"].notna()]
    ixg = shots.groupby(["game_id", "shooter"])["val"].sum()
    ixg_map = {(int(g), int(s)): float(v) for (g, s), v in ixg.items()}

    rows = []
    for rec in records:
        home, away = rec["home"]["abbr"], rec["away"]["abbr"]
        goals: dict[str, int] = {}
        for sk in rec["skaters"]:
            goals[sk["team"]] = goals.get(sk["team"], 0) + sk["g"]
        for sk in rec["skaters"]:
            team = sk["team"]
            rows.append({
                "game_id": rec["id"], "season": rec["season"], "game_type": rec["game_type"],
                "date": pd.Timestamp(rec["date"]), "team": team,
                "opp": away if team == home else home, "player": sk["id"], "name": sk["name"],
                "pos": sk["pos"], "grp": _grp(sk["pos"]), "toi": sk["toi"], "g": sk["g"],
                "a": sk["a"], "pts": sk["g"] + sk["a"],
                "ixg": ixg_map.get((rec["id"], sk["id"]), 0.0), "team_goals": goals[team],
            })
    return pd.DataFrame(rows, columns=PG_COLUMNS)


@dataclass
class PlayerRates:
    g60: dict[int, float] = field(default_factory=dict)
    p60: dict[int, float] = field(default_factory=dict)
    pos_g60: dict[str, float] = field(default_factory=dict)
    pos_p60: dict[str, float] = field(default_factory=dict)
    ppg: float = config.PLAYER_PPG_FALLBACK

    def rate(self, pid: int, pos: str) -> tuple[float, float]:
        if pid in self.g60:
            return self.g60[pid], self.p60[pid]
        grp = _grp(pos)
        return self.pos_g60[grp], self.pos_p60[grp]


@dataclass
class PlayerInputs:
    rates: PlayerRates
    lineups: dict[str, list[Slot]]
    baseline: dict[int, tuple[float, float]]
    pp: PlayerParams


def _fallback_rates() -> PlayerRates:
    fb = config.PLAYER_PRIOR_FALLBACK
    return PlayerRates(
        pos_g60={k: v[0] for k, v in fb.items()}, pos_p60={k: v[1] for k, v in fb.items()},
    )


def player_rates(
    pg: pd.DataFrame, asof: date, p: ModelParams, pp: PlayerParams, season: str | None = None
) -> PlayerRates:
    w = ratings.weights(pg, asof, replace(p, half_life_days=pp.half_life_days), season)
    keep = (w > 0) & pg["game_type"].isin(config.TRAIN_GAME_TYPES).to_numpy()
    if not keep.any():
        return _fallback_rates()
    d = pg[keep]
    w = w[keep]
    g_hat = pp.xg_blend * d["ixg"].to_numpy(dtype=float) + (1 - pp.xg_blend) * d["g"].to_numpy(
        dtype=float
    )
    d = d.assign(
        wg=w * g_hat,
        wp=w * (g_hat + d["a"].to_numpy(dtype=float)),
        wh=w * d["toi"].to_numpy(dtype=float) / 3600.0,
        wgoals=w * d["g"].to_numpy(dtype=float),
        wpts=w * d["pts"].to_numpy(dtype=float),
    )

    fb = config.PLAYER_PRIOR_FALLBACK
    pos_g60, pos_p60 = {}, {}
    by_grp = d.groupby("grp")[["wg", "wp", "wh"]].sum()
    for grp in ("F", "D"):
        if grp in by_grp.index and by_grp.loc[grp, "wh"] > 0:
            pos_g60[grp] = float(by_grp.loc[grp, "wg"] / by_grp.loc[grp, "wh"])
            pos_p60[grp] = float(by_grp.loc[grp, "wp"] / by_grp.loc[grp, "wh"])
        else:
            pos_g60[grp], pos_p60[grp] = fb[grp]

    per = d.groupby("player")[["wg", "wp", "wh"]].sum()
    last_grp = d.sort_values("date", kind="stable").groupby("player")["grp"].last()
    k = pp.prior_toi_h
    prior_g = last_grp.reindex(per.index).map(pos_g60).to_numpy(dtype=float)
    prior_p = last_grp.reindex(per.index).map(pos_p60).to_numpy(dtype=float)
    denom = per["wh"].to_numpy() + k
    g60 = (per["wg"].to_numpy() + k * prior_g) / denom
    p60 = (per["wp"].to_numpy() + k * prior_p) / denom

    wgoals, wpts = float(d["wgoals"].sum()), float(d["wpts"].sum())
    ppg = min(config.PLAYER_PPG_MAX, wpts / wgoals) if wgoals > 0 else config.PLAYER_PPG_FALLBACK
    return PlayerRates(
        g60={int(i): float(v) for i, v in zip(per.index, g60, strict=True)},
        p60={int(i): float(v) for i, v in zip(per.index, p60, strict=True)},
        pos_g60=pos_g60, pos_p60=pos_p60, ppg=float(ppg),
    )


def baseline_probs(
    pg: pd.DataFrame, asof: date, season: str, slots: list[Slot]
) -> dict[int, tuple[float, float]]:
    """Spec 8 baseline: unweighted, unshrunk current-season rates times average TOI."""
    d = pg[
        (pg["season"] == season)
        & (pg["date"] < pd.Timestamp(asof))
        & pg["game_type"].isin(config.TRAIN_GAME_TYPES)
    ]
    fb = config.PLAYER_PRIOR_FALLBACK
    per = d.groupby("player").agg(
        g=("g", "sum"), pts=("pts", "sum"), toi=("toi", "sum"), n=("g", "size"),
    )
    by_grp = d.groupby("grp")[["g", "pts", "toi"]].sum()

    out: dict[int, tuple[float, float]] = {}
    for slot in slots:
        pid, grp = int(slot["id"]), _grp(slot["pos"])
        if pid in per.index and per.loc[pid, "toi"] > 0:
            r = per.loc[pid]
            h = r["toi"] / 3600.0
            g60, p60 = r["g"] / h, r["pts"] / h
            toi_min = r["toi"] / r["n"] / 60.0
        else:
            if grp in by_grp.index and by_grp.loc[grp, "toi"] > 0:
                h = by_grp.loc[grp, "toi"] / 3600.0
                g60, p60 = by_grp.loc[grp, "g"] / h, by_grp.loc[grp, "pts"] / h
            else:
                g60, p60 = fb[grp]
            toi_min = float(slot["toi_proj"])
        hours = toi_min / 60.0
        out[pid] = (
            float(1.0 - np.exp(-p60 * hours)),
            float(1.0 - np.exp(-g60 * hours)),
        )
    return out
