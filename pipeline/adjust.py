"""Goalie ratings, starter projection and goalie multiplier (spec 6.5, 6.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from pipeline import config, ratings, xg
from pipeline.config import ModelParams

GG_COLUMNS = [
    "game_id", "season", "game_type", "date", "team", "goalie", "name",
    "started", "toi", "xg_faced", "ga",
]


@dataclass
class GoalieRatings:
    delta: dict[int, float] = field(default_factory=dict)
    team_delta: dict[str, float] = field(default_factory=dict)
    gsax_per60: dict[int, float] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)


def goalie_games(records: list[dict], coefs: dict) -> pd.DataFrame:
    """One row per goalie per game; xG faced and goals against from his shot rows."""
    shots = xg.shots_frame(records)
    shots = shots[shots["goalie"].notna()].copy()
    shots["xg"] = xg.score(shots, coefs) if len(shots) else np.array([])
    shots["goal"] = (shots["ev"] == "goal").astype(int)
    shots["goalie"] = shots["goalie"].astype(int)
    faced = shots.groupby(["game_id", "goalie"]).agg(xg_faced=("xg", "sum"), ga=("goal", "sum"))
    rows = []
    for rec in records:
        for g in rec["goalies"]:
            key = (rec["id"], g["id"])
            if key in faced.index:
                xg_faced, ga = faced.loc[key, "xg_faced"], faced.loc[key, "ga"]
            else:
                xg_faced, ga = 0.0, 0
            rows.append({
                "game_id": rec["id"], "season": rec["season"], "game_type": rec["game_type"],
                "date": pd.Timestamp(rec["date"]), "team": g["team"], "goalie": g["id"],
                "name": g["name"], "started": bool(g["started"]), "toi": g["toi"],
                "xg_faced": float(xg_faced), "ga": int(ga),
            })
    return pd.DataFrame(rows, columns=GG_COLUMNS)


def goalie_ratings(
    gg: pd.DataFrame, asof: date, p: ModelParams, season: str | None = None
) -> GoalieRatings:
    w = ratings.weights(gg, asof, p, season)
    keep = w > 0
    d = gg[keep].assign(
        w=w[keep], wx=lambda f: f["w"] * f["xg_faced"], wga=lambda f: f["w"] * f["ga"],
        wtoi=lambda f: f["w"] * f["toi"],
    )
    gr = GoalieRatings()
    if d.empty:
        return gr
    per_g = d.groupby("goalie")[["wx", "wga", "wtoi"]].sum()
    for g, r in per_g.iterrows():
        gr.delta[int(g)] = float((r["wx"] - r["wga"]) / (r["wx"] + p.goalie_k))
        gr.gsax_per60[int(g)] = (
            round(float((r["wx"] - r["wga"]) / r["wtoi"] * 3600), 2) if r["wtoi"] > 0 else 0.0
        )
    latest = d.sort_values("date").groupby("goalie")["name"].last()
    gr.names = {int(g): n for g, n in latest.items()}
    per_tg = d.groupby(["team", "goalie"])["wx"].sum().reset_index()
    for team, f in per_tg.groupby("team"):
        total = f["wx"].sum()
        if total > 0:
            gr.team_delta[str(team)] = float(
                sum(gr.delta[int(g)] * x for g, x in zip(f["goalie"], f["wx"], strict=True))
                / total
            )
    return gr


def project_starters(
    gg: pd.DataFrame, team: str, nhl_date: date, season: str | None = None
) -> list[tuple[int, float]]:
    """Likely starters, most probable first (spec 6.5, inferred starter)."""
    day = pd.Timestamp(nhl_date)
    before = gg[gg["date"] < day]
    if before.empty:
        return []
    cur = season if season is not None else before["season"].max()
    mine = before[(before["team"] == team) & (before["season"] == cur)]
    starts = mine[mine["started"]]
    if starts.empty:
        return []
    last_games = (
        mine[["game_id", "date"]].drop_duplicates().sort_values(["date", "game_id"])
        .tail(config.STARTER_LOOKBACK)["game_id"]
    )
    recent = starts[starts["game_id"].isin(last_games)].sort_values(["date", "game_id"])
    counts: dict[int, tuple[int, pd.Timestamp]] = {}
    for g, dt in zip(recent["goalie"], recent["date"], strict=True):
        n, _ = counts.get(int(g), (0, dt))
        counts[int(g)] = (n + 1, dt)
    order = sorted(counts, key=lambda g: counts[g], reverse=True)
    primary = order[0]
    if len(order) == 1:
        return [(primary, 1.0)]
    backup = order[1]
    p_hi, p_lo = config.STARTER_PROB, round(1.0 - config.STARTER_PROB, 10)
    yesterday = before[
        (before["team"] == team) & (before["date"] == day - timedelta(days=1))
        & before["started"]
    ]
    if (yesterday["goalie"] == primary).any():
        return [(backup, p_hi), (primary, p_lo)]
    return [(primary, p_hi), (backup, p_lo)]


def goalie_mult(
    starters: list[tuple[int, float]], team: str, gr: GoalieRatings, p: ModelParams
) -> float:
    if not starters or not p.use_goalie:
        return 1.0
    lo, hi = config.GOALIE_MULT_CLAMP
    base = gr.team_delta.get(team, 0.0)
    return float(sum(
        prob * min(max(1.0 - p.goalie_weight * (gr.delta.get(g, 0.0) - base), lo), hi)
        for g, prob in starters
    ))


def goalie_block(
    starters: list[tuple[int, float]], team: str, gr: GoalieRatings, p: ModelParams,
    status: str = "projected",
) -> dict | None:
    """The G object for the most probable starter; `mult` is the applied multiplier,
    i.e. the expectation over all `starters` (plan decision 9)."""
    if not starters:
        return None
    g, prob = starters[0]
    return {
        "id": g,
        "name": gr.names.get(g),
        "status": status,
        "gsax_per60": gr.gsax_per60.get(g),
        "p_start": prob,
        "mult": goalie_mult(starters, team, gr, p),
    }
