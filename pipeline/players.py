"""Player table, shrunk per-60 rates and the season baseline (spec 6.10; plan decisions 19, 27)."""

from __future__ import annotations

import math
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


def _norm_pos(pos) -> str:
    pos = str(pos)
    return pos if pos in ("C", "L", "R", "D") else "C"


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


def project_lineup(pg: pd.DataFrame, team: str, asof: date, season: str) -> list[Slot]:
    """Skaters of the team's latest cached game before `asof`, minus players who have since
    dressed for another team (plan decision 20). Sorted by projected TOI, then id."""
    d = pg[pg["date"] < pd.Timestamp(asof)]
    mine = d[d["team"] == team]
    if mine.empty:
        return []
    cur = mine[mine["season"] == season]
    pool = cur if len(cur) else mine
    last = pool.sort_values(["date", "game_id"], kind="stable").iloc[-1]
    last_key = (last["date"], int(last["game_id"]))
    dressed = pool[pool["game_id"] == last["game_id"]]
    dressed = dressed[dressed["team"] == team]
    ids = dressed["player"].unique()

    hist = d[d["player"].isin(ids)].sort_values(["date", "game_id"], kind="stable")
    latest = hist.groupby("player").tail(1).set_index("player")
    moved = [
        pid for pid, r in latest.iterrows()
        if r["team"] != team and (r["date"], int(r["game_id"])) > last_key
    ]
    hist = hist[~hist["player"].isin(moved)]
    if hist.empty:
        return []
    recent = hist.groupby("player").tail(config.PLAYER_TOI_GAMES)
    toi = recent.groupby("player")["toi"].mean() / 60.0
    info = hist.groupby("player").tail(1).set_index("player")
    slots = [
        {"id": int(pid), "name": (None if pd.isna(info.at[pid, "name"]) else info.at[pid, "name"]),
         "pos": _norm_pos(info.at[pid, "pos"]), "toi_proj": round(float(t), 1)}
        for pid, t in toi.items()
    ]
    slots.sort(key=lambda s: (-s["toi_proj"], s["id"]))
    return slots


def binom_tails(dist: list[float], q: float) -> list[float]:
    """[P(X>=1), P(X>=2), P(X>=3)] for X | G ~ Binomial(G, q); bucket index = G (10+ counts 10)."""
    out = []
    for k in (1, 2, 3):
        total = 0.0
        for g, pg_ in enumerate(dist):
            if pg_ <= 0 or g < k:
                continue
            below = sum(math.comb(g, j) * q**j * (1 - q) ** (g - j) for j in range(k))
            total += pg_ * (1.0 - below)
        out.append(min(1.0, max(0.0, total)))
    return out


def player_probs(
    slots: list[Slot], rates: PlayerRates, dist: list[float], pp: PlayerParams
) -> list[dict]:
    if not slots:
        return []
    e_g, e_p = [], []
    for s in slots:
        g60, p60 = rates.rate(int(s["id"]), s["pos"])
        e_g.append(g60 * s["toi_proj"] / 60.0)
        e_p.append(p60 * s["toi_proj"] / 60.0)
    sum_g, sum_p = sum(e_g), sum(e_p)
    mean = sum(g * x for g, x in enumerate(dist))
    n = len(slots)
    out = []
    for s, eg, ep in zip(slots, e_g, e_p, strict=True):
        q = (1 - pp.unassigned) * (eg / sum_g if sum_g > 0 else 1.0 / n)
        share_p = (1 - pp.unassigned) * rates.ppg * (ep / sum_p if sum_p > 0 else 1.0 / n)
        pi = min(config.PLAYER_PI_MAX, max(q, share_p))
        q = min(q, pi)
        out.append({
            "id": int(s["id"]), "name": s["name"], "pos": s["pos"], "toi_proj": s["toi_proj"],
            "q": q, "pi": pi, "p_goals": binom_tails(dist, q), "p_points": binom_tails(dist, pi),
            "exp_goals": q * mean, "exp_points": pi * mean,
        })
    out.sort(key=lambda x: (-x["p_points"][0], x["id"]))
    return out


def _r4(xs: list[float]) -> list[float]:
    return [round(float(x), 4) for x in xs]


def team_block(team: str, probs: list[dict], dist: list[float], pp: PlayerParams) -> dict | None:
    if not probs:
        return None
    n = config.PLAYER_TOP_N
    by_pts = sorted(probs, key=lambda x: (-x["p_points"][0], -x["p_goals"][0], x["id"]))
    by_goals = sorted(probs, key=lambda x: (-x["p_goals"][0], x["id"]))
    keep = {x["id"] for x in by_pts[:n]} | {x["id"] for x in by_goals[:n]}
    top = [
        {
            "id": x["id"], "name": x["name"], "team": team, "pos": x["pos"],
            "toi_proj": round(x["toi_proj"], 1),
            "p_points": _r4(x["p_points"]), "p_goals": _r4(x["p_goals"]),
            "exp_goals": round(x["exp_goals"], 3), "exp_points": round(x["exp_points"], 3),
            "odds": None,
        }
        for x in by_pts if x["id"] in keep
    ]
    return {
        "team": team, "dressed": len(probs),
        "goals_mean": round(sum(g * x for g, x in enumerate(dist)), 3),
        "exp_goals": round(sum(x["exp_goals"] for x in probs), 3),
        "exp_points": round(sum(x["exp_points"] for x in probs), 3),
        "unassigned": pp.unassigned, "top": top,
    }


def game_players(
    home: str, away: str, pin: PlayerInputs, credited: dict[str, list[float]]
) -> tuple[dict | None, dict | None]:
    blocks: dict[str, dict | None] = {}
    teams: dict[str, dict] = {}
    for side, team in (("home", home), ("away", away)):
        dist = credited[side] if side in credited else credited[team]
        probs = player_probs(pin.lineups.get(team, []), pin.rates, dist, pin.pp)
        blocks[side] = team_block(team, probs, dist, pin.pp)
        if not probs:
            continue
        rows = []
        for x in probs:
            b_p, b_g = pin.baseline.get(x["id"], (None, None))
            rows.append([
                x["id"], x["pos"], x["toi_proj"], *_r4(x["p_points"]), *_r4(x["p_goals"]),
                None if b_p is None else round(float(b_p), 4),
                None if b_g is None else round(float(b_g), 4),
            ])
        teams[team] = {
            "goals_mean": round(sum(g * v for g, v in enumerate(dist)), 3),
            "unassigned": pin.pp.unassigned, "players": rows,
        }
    if blocks["home"] is None and blocks["away"] is None:
        return None, None
    return {"lineup": "projected", "home": blocks["home"], "away": blocks["away"]}, {"teams": teams}
