"""Leak-free player backtest, tuning and gate (spec 8, 15.1, 15.3; plan decisions 22, 24)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, replace

import numpy as np
import pandas as pd

from pipeline import backtest, cache, config, metrics, players, xg
from pipeline.config import ModelParams, PlayerParams

logger = logging.getLogger(__name__)

PB_COLUMNS = [
    "game_id", "date", "team", "player", "pos", "p_pts1", "p_pts2", "p_pts3",
    "p_g1", "p_g2", "p_g3", "b_pts1", "b_g1", "y_pts", "y_g",
]
UNASSIGNED_STEP = 0.005
PROGRESS_EVERY = 20


def _prior_baseline(slot: dict) -> tuple[float, float]:
    """Position prior for a player without a baseline."""
    g60, p60 = config.PLAYER_PRIOR_FALLBACK[players._grp(slot["pos"])]
    hours = float(slot["toi_proj"]) / 60.0
    return float(1.0 - np.exp(-p60 * hours)), float(1.0 - np.exp(-g60 * hours))


def _metrics(rows: pd.DataFrame) -> dict:
    def m(p: str, y: pd.Series, base: str | None) -> dict:
        return metrics.binary_vs_base(
            rows[p].to_numpy(float), y.to_numpy(float),
            None if base is None else rows[base].to_numpy(float),
        )

    return {
        "points": m("p_pts1", rows["y_pts"] >= 1, "b_pts1"),
        "goals": m("p_g1", rows["y_g"] >= 1, "b_g1"),
        "points2": m("p_pts2", rows["y_pts"] >= 2, None),
    }


def player_backtest(
    pg: pd.DataFrame, team_rows: pd.DataFrame, season: str, p: ModelParams, pp: PlayerParams
) -> dict:
    """Player probabilities for every game in `team_rows`, using only `pg` rows before its date."""
    out: list[dict] = []
    n_dressed = n_hit = 0
    goals_un = goals_all = 0.0
    dates = sorted(team_rows["date"].unique())
    for i, day in enumerate(dates):
        ts = pd.Timestamp(day)
        asof = ts.date()
        hist = pg[pg["date"] < ts]
        if hist.empty:
            continue
        day_rows = team_rows[team_rows["date"] == ts]
        rates = players.player_rates(hist, asof, p, pp, season)
        teams = sorted(set(day_rows["home"]) | set(day_rows["away"]))
        lineups = {t: players.project_lineup(hist, t, asof, season) for t in teams}
        slots_all = [s for t in teams for s in lineups[t]]
        base = players.baseline_probs(hist, asof, season, slots_all) if slots_all else {}
        dressed = {
            (int(g), str(t)): f.set_index("player")
            for (g, t), f in pg[pg["date"] == ts].groupby(["game_id", "team"])
        }
        for g in day_rows.itertuples():
            for team, dist in ((g.home, g.gd_home), (g.away, g.gd_away)):
                slots = lineups[team]
                actual = dressed.get((int(g.game_id), team))
                if not slots or actual is None:
                    continue
                projected = {int(s["id"]) for s in slots}
                hit = [pid for pid in actual.index if int(pid) in projected]
                n_dressed += len(actual)
                n_hit += len(hit)
                goals_all += float(actual["g"].sum())
                goals_un += float(actual.loc[~actual.index.isin(list(projected)), "g"].sum())
                by_id = {int(s["id"]): s for s in slots}
                for x in players.player_probs(slots, rates, dist, pp):
                    pid = x["id"]
                    if pid not in actual.index:
                        continue
                    b = base.get(pid)
                    if b is None or b[0] is None or b[1] is None:
                        b = _prior_baseline(by_id[pid])
                    out.append({
                        "game_id": int(g.game_id), "date": ts, "team": team, "player": pid,
                        "pos": x["pos"],
                        "p_pts1": x["p_points"][0], "p_pts2": x["p_points"][1],
                        "p_pts3": x["p_points"][2],
                        "p_g1": x["p_goals"][0], "p_g2": x["p_goals"][1],
                        "p_g3": x["p_goals"][2],
                        "b_pts1": float(b[0]), "b_g1": float(b[1]),
                        "y_pts": int(actual.at[pid, "pts"]), "y_g": int(actual.at[pid, "g"]),
                    })
        if (i + 1) % PROGRESS_EVERY == 0:
            logger.info("player backtest %s: %d/%d dates", season, i + 1, len(dates))
    rows = pd.DataFrame(out, columns=PB_COLUMNS)
    return {
        "rows": rows,
        "coverage": n_hit / n_dressed if n_dressed else 0.0,
        "unassigned": goals_un / goals_all if goals_all else 0.0,
        "metrics": _metrics(rows),
    }


def _objective(res: dict) -> float:
    m = res["metrics"]
    if m["points"]["n"] == 0:
        return float("inf")
    return (m["points"]["logloss"] + m["goals"]["logloss"]) / 2


def tune_players(
    pg: pd.DataFrame, team_rows: pd.DataFrame, season: str, p: ModelParams, base: PlayerParams
) -> PlayerParams:
    """Grid half-life x prior hours on mean(points, goals) log loss, then measure unassigned."""
    best: tuple[float, PlayerParams, dict] | None = None
    for hl in config.PLAYER_HALF_LIFE_GRID:
        for prior in config.PLAYER_PRIOR_GRID:
            cand = replace(base, half_life_days=float(hl), prior_toi_h=float(prior))
            res = player_backtest(pg, team_rows, season, p, cand)
            obj = _objective(res)
            logger.info("tune players: half_life=%s prior=%s objective=%.5f", hl, prior, obj)
            if best is None or obj < best[0]:
                best = (obj, cand, res)
    assert best is not None
    lo, hi = config.PLAYER_UNASSIGNED_RANGE
    u = round(best[2]["unassigned"] / UNASSIGNED_STEP) * UNASSIGNED_STEP
    u = min(hi, max(lo, u))
    return replace(best[1], unassigned=round(float(u), 3))


def player_gate(m: dict) -> bool:
    for k in ("points", "goals"):
        x = m[k]
        if x["brier"] is None or x["brier_base"] is None:
            return False
        if not (x["brier"] < x["brier_base"] and x["logloss"] < x["logloss_base"]):
            return False
    return True


def _strip(m: dict) -> dict:
    return {k: v for k, v in m.items() if k != "calibration"}


def _cal_table(title: str, cal: list[dict]) -> list[str]:
    lines = [f"### {title}", "", "| lo–hi | n | malli | toteutunut |", "|---|---|---|---|"]
    for b in cal:
        lines.append(
            f"| {b['lo']:.2f}–{b['hi']:.2f} | {b['n']} | {b['p_mean']:.3f} | {b['y_mean']:.3f} |"
        )
    return lines + [""]


def _fmt(x: float | None) -> str:
    return "–" if x is None else f"{x:.4f}"


def _report(args, pp: PlayerParams, res: dict, passed: bool) -> str:
    m = res["metrics"]
    lines = [
        f"# Pelaajamalli: backtest {args.gate_season}", "",
        f"Viritys: {args.tune_season}. Parametrit: puoliintumisaika {pp.half_life_days:g} pv, "
        f"prior {pp.prior_toi_h:g} h, xG-osuus {pp.xg_blend:g}, kohdistamaton osuus "
        f"{pp.unassigned:.3f}.", "",
        "| Mittari | Malli | Perustaso |", "|---|---|---|",
    ]
    for label, key in (("P(≥1 piste)", "points"), ("P(≥1 maali)", "goals")):
        x = m[key]
        lines.append(
            f"| {label} Brier (n={x['n']}) | {_fmt(x['brier'])} | {_fmt(x['brier_base'])} |"
        )
        lines.append(f"| {label} log loss | {_fmt(x['logloss'])} | {_fmt(x['logloss_base'])} |")
    x = m["points2"]
    lines.append(f"| P(≥2 pistettä) Brier (n={x['n']}) | {_fmt(x['brier'])} | – |")
    lines.append(f"| P(≥2 pistettä) log loss | {_fmt(x['logloss'])} | – |")
    lines += [
        "", f"Kattavuus (ennustetut ∩ pelanneet / pelanneet): {res['coverage']:.3f}",
        f"Kohdistamaton maaliosuus: {res['unassigned']:.3f}", "",
    ]
    lines += _cal_table("Kalibrointi P(≥1 piste)", m["points"]["calibration"])
    lines += _cal_table("Kalibrointi P(≥1 maali)", m["goals"]["calibration"])
    lines.append(f"Tulos: {'HYVÄKSYTTY' if passed else 'HYLÄTTY'} (pelaajaportti).")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.player_backtest")
    ap.add_argument("--tune-season", required=True)
    ap.add_argument("--gate-season", required=True)
    ap.add_argument("--sims", type=int, default=2000)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    p = config.load_params()
    gate_info = config.load_gate() or {}
    mode = gate_info.get("goalie_mode", "projected" if p.use_goalie else "none")
    seasons = sorted({s for r in (args.tune_season, args.gate_season)
                      for s in (backtest._season_before(r), r)})
    coefs_path = config.path(*backtest.BT_COEFS)
    tg = backtest.load_team_games(seasons, coefs_path, p.xg_weight)
    gg = backtest.load_goalie_games(seasons, coefs_path)
    pg = players.player_games(cache.load_seasons(seasons), xg.load_coefs(coefs_path))

    def run(season: str) -> pd.DataFrame:
        res = backtest.run_backtest(
            tg, season, p, n_sims=args.sims, gg=gg, goalie_mode=mode, keep_goal_dist=True
        )
        return res["rows"]

    tune_rows = run(args.tune_season)
    pp = tune_players(pg, tune_rows, args.tune_season, p, config.load_player_params())
    gate_rows = run(args.gate_season)
    res = player_backtest(pg, gate_rows, args.gate_season, p, pp)
    passed = player_gate(res["metrics"])

    report = config.path("reports", "player_backtest.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_report(args, pp, res, passed), encoding="utf-8")
    if passed:
        gate = {
            "passed": True, "season": args.gate_season, "tune_season": args.tune_season,
            "goalie_mode": mode, "points": _strip(res["metrics"]["points"]),
            "goals": _strip(res["metrics"]["goals"]), "coverage": res["coverage"],
            "unassigned": res["unassigned"],
        }
        f = config.path("model", "player_params.json")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            json.dumps({**asdict(pp), "gate": gate}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    logger.info("player gate %s", "passed" if passed else "FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
