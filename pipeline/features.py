"""Cached games to team_games table and simulation parameters (spec 6.2, 6.8, 6.9)."""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

from pipeline import config, xg

COLUMNS = [
    "game_id", "season", "game_type", "date", "team", "opp", "is_home", "b2b", "opp_b2b",
    "goals_reg", "xg_reg", "target", "target_p1", "target_p2", "target_p3", "en_goals",
    "score_reg", "opp_score_reg", "decided", "won",
]
REG_PERIODS = (1, 2, 3)
SUM_COLUMNS = ["goals_reg", "xg_reg", "target", "target_p1", "target_p2", "target_p3", "en_goals"]


def _shot_sums(records: list[dict], coefs: dict, xg_weight: float) -> pd.DataFrame:
    """Per (game_id, team) regulation sums from shots, indexed by those two keys."""
    shots = xg.shots_frame(records)
    shots = shots[shots["p"] <= max(REG_PERIODS)].copy()
    shots["xg"] = xg.score(shots, coefs) if len(shots) else np.array([], dtype=float)
    shots["goal"] = (shots["ev"] == "goal").astype(float)
    keys = ["game_id", "team"]
    en = shots["empty_net"].astype(bool)
    en_goals = shots[en & (shots["goal"] > 0)].groupby(keys).size().rename("en_goals")
    shots = shots[~en].copy()
    shots["target"] = xg_weight * shots["xg"] + (1 - xg_weight) * shots["goal"]
    out = pd.DataFrame({
        "goals_reg": shots.groupby(keys)["goal"].sum(),
        "xg_reg": shots.groupby(keys)["xg"].sum(),
        "target": shots.groupby(keys)["target"].sum(),
    })
    for p in REG_PERIODS:
        out[f"target_p{p}"] = shots[shots["p"] == p].groupby(keys)["target"].sum()
    return out.join(en_goals, how="outer").fillna(0.0)


def team_games(records: list[dict], coefs: dict, xg_weight: float) -> pd.DataFrame:
    """Two rows per cached game, one per team as attacker (columns in COLUMNS)."""
    if not records:
        return pd.DataFrame(columns=COLUMNS)
    sums = _shot_sums(records, coefs, xg_weight)
    played = {(rec[side]["abbr"], rec["date"]) for rec in records for side in ("home", "away")}

    def b2b(team: str, date: str) -> int:
        prev = (datetime.date.fromisoformat(date) - datetime.timedelta(days=1)).isoformat()
        return int((team, prev) in played)

    rows = []
    for rec in records:
        for side, other in (("home", "away"), ("away", "home")):
            team, opp = rec[side]["abbr"], rec[other]["abbr"]
            if (rec["id"], team) in sums.index:
                s = sums.loc[(rec["id"], team)].to_dict()
            else:
                s = dict.fromkeys(SUM_COLUMNS, 0.0)
            s["en_goals"] = int(s["en_goals"])
            rows.append({
                "game_id": rec["id"], "season": rec["season"], "game_type": rec["game_type"],
                "date": pd.Timestamp(rec["date"]), "team": team, "opp": opp,
                "is_home": side == "home",
                "b2b": b2b(team, rec["date"]), "opp_b2b": b2b(opp, rec["date"]),
                **s,
                "score_reg": rec["score_reg"][side], "opp_score_reg": rec["score_reg"][other],
                "decided": rec["decided"], "won": rec["score"][side] > rec["score"][other],
            })
    return pd.DataFrame(rows, columns=COLUMNS)


def sim_params(tg: pd.DataFrame) -> dict:
    """SimParams: league period shares and empty-net probabilities by regulation margin."""
    totals = np.array([tg[f"target_p{p}"].sum() for p in REG_PERIODS], dtype=float)
    share = (totals / totals.sum()).tolist() if totals.sum() > 0 else [1 / 3] * 3

    cols = ["goals_reg", "en_goals"]
    home = tg[tg["is_home"]].set_index("game_id")[cols]
    away = tg[~tg["is_home"]].set_index("game_id")[cols]
    games = home.join(away, lsuffix="_h", rsuffix="_a", how="inner")
    margin = (games["goals_reg_h"] - games["goals_reg_a"]).abs().to_numpy()
    leader_en = np.where(games["goals_reg_h"] > games["goals_reg_a"],
                         games["en_goals_h"], games["en_goals_a"])
    en_prob = {}
    for m, fallback in config.EN_PROB_FALLBACK.items():
        mask = margin == m
        n = int(mask.sum())
        enough = n >= config.EN_PROB_MIN_GAMES
        en_prob[m] = float((leader_en[mask] >= 1).mean()) if enough else fallback
    ot_share = config.OT_GOAL_SHARE_FALLBACK
    dh = tg[tg["is_home"] & tg["decided"].isin(["OT", "SO"])]
    if len(dh) >= config.EN_PROB_MIN_GAMES:
        ot_share = float((dh["decided"] == "OT").mean())
    return {"league_period_share": share, "en_prob": en_prob, "ot_goal_share": ot_share}


def last_played(tg: pd.DataFrame) -> dict[str, datetime.date]:
    """Most recent game date per team."""
    return {t: d.date() for t, d in tg.groupby("team")["date"].max().items()}
