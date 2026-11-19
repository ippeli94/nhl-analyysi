"""Out-of-sample gate for the lineup and head-to-head factors (spec 6.6-6.7; Task 6).

CLI: python -m pipeline.lineup_backtest [--tune-season S] [--gate-season S] [--sims N]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, replace

import numpy as np
import pandas as pd

from pipeline import backtest, cache, config, context, lineup, players, ratings, xg
from pipeline.config import LineupParams, ModelParams
from pipeline.lineup import Contrib
from pipeline.predict import Mults

logger = logging.getLogger(__name__)

RHO_GRID = (0.30, 0.45, 0.60)
GOALIE_MODE = "projected"
VARIANTS = ("baseline", "lineup", "h2h", "lineup_h2h")
PROGRESS_EVERY = 20

Inputs = dict[tuple[int, str], tuple[dict[int, Contrib], dict[int, float]]]


def actual_expect(contrib: dict[int, Contrib], dressed: set[int]) -> dict[int, float]:
    """Actual mode: 1 for players who dressed in the game, 0 for everyone else."""
    out = {pid: (1.0 if pid in dressed else 0.0) for pid in contrib}
    out.update({int(pid): 1.0 for pid in dressed})
    return out


def _season_games(tg: pd.DataFrame, season: str) -> pd.DataFrame:
    return tg[(tg["season"] == season) & tg["is_home"]
              & tg["game_type"].isin(config.TRAIN_GAME_TYPES)]


def lineup_inputs(
    pg: pd.DataFrame, tg: pd.DataFrame, season: str, p: ModelParams, mode: str
) -> Inputs:
    """(contributions, expectations) per (game_id, team), from rows before the game date.

    mode "actual": the players who dressed in that game; "projected": the lineup of the
    team's latest earlier game (players.project_lineup), no ESPN injuries."""
    if mode not in ("actual", "projected"):
        raise ValueError(f"unknown mode {mode!r}")
    games = _season_games(tg, season)
    out: Inputs = {}
    dates = sorted(games["date"].unique())
    for i, day in enumerate(dates):
        ts = pd.Timestamp(day)
        asof = ts.date()
        pg_hist = pg[pg["date"] < ts]
        tg_hist = tg[tg["date"] < ts]
        today = pg[pg["date"] == ts] if mode == "actual" else None
        for g in games[games["date"] == ts].itertuples():
            for team in (g.team, g.opp):
                if mode == "actual":
                    dressed = {int(x) for x in
                               today[(today["game_id"] == g.game_id)
                                     & (today["team"] == team)]["player"]}
                    contrib = lineup.contributions(pg_hist, tg_hist, team, asof, p, season,
                                                   sorted(dressed))
                    expect = actual_expect(contrib, dressed)
                else:
                    lu = players.project_lineup(pg_hist, team, asof, season)
                    contrib = lineup.contributions(pg_hist, tg_hist, team, asof, p, season,
                                                   [s["id"] for s in lu])
                    expect, _ = lineup.expectations(contrib, lu, [], pg_hist, team, asof, season)
                out[(int(g.game_id), str(team))] = (contrib, expect)
        if (i + 1) % PROGRESS_EVERY == 0:
            logger.info("lineup inputs %s [%s]: %d/%d dates", season, mode, i + 1, len(dates))
    return out


def h2h_table(tg: pd.DataFrame, season: str, p: ModelParams) -> dict[int, tuple[float, float]]:
    """(home, away) head-to-head multipliers per game with ratings fitted as of its date."""
    games = _season_games(tg, season)
    out: dict[int, tuple[float, float]] = {}
    for day in sorted(games["date"].unique()):
        ts = pd.Timestamp(day)
        asof = ts.date()
        hist = tg[tg["date"] < ts]
        r = ratings.fit(hist, asof, p, season=season)
        for g in games[games["date"] == ts].itertuples():
            out[int(g.game_id)] = (context.h2h_mult(hist, r, g.team, g.opp, asof),
                                   context.h2h_mult(hist, r, g.opp, g.team, asof))
    return out


def mults_fn(
    inputs: Inputs | None, rho: float, h2h: dict[int, tuple[float, float]] | None = None
) -> Callable[[pd.Timestamp, int, str, str], Mults]:
    cache_: dict[tuple[int, str], tuple[float, float]] = {}

    def side(gid: int, team: str) -> tuple[float, float]:
        if inputs is None or (gid, team) not in inputs:
            return 1.0, 1.0
        if (gid, team) not in cache_:
            contrib, expect = inputs[(gid, team)]
            m_att, m_def, _ = lineup.multipliers(contrib, expect, rho)
            cache_[(gid, team)] = (m_att, m_def)
        return cache_[(gid, team)]

    def fn(ts: pd.Timestamp, gid: int, home: str, away: str) -> Mults:
        ah, dh = side(gid, home)
        aa, da = side(gid, away)
        hh, ha = h2h.get(gid, (1.0, 1.0)) if h2h is not None else (1.0, 1.0)
        return Mults(att_h=ah, def_h=dh, att_a=aa, def_a=da, h2h_h=hh, h2h_a=ha)

    return fn


def winner_logloss(rows: pd.DataFrame, kappa: float) -> float:
    """Winner log loss with the draw weight re-applied to a draw_kappa = 1 run (exact)."""
    p_home = _winner_probs(rows, kappa)
    y = rows["y_home"].to_numpy(float)
    p = np.clip(np.where(y == 1.0, p_home, 1.0 - p_home), backtest.LOGLOSS_CLIP, None)
    return float(-np.log(p).mean())


def _winner_probs(rows: pd.DataFrame, kappa: float) -> np.ndarray:
    ph = rows["p_home"].to_numpy(float)
    reg_h = rows["p_reg_home"].to_numpy(float)
    draw = rows["p_reg_draw"].to_numpy(float)
    return (reg_h + kappa * (ph - reg_h)) / (1.0 + (kappa - 1.0) * draw)


def _metrics(rows: pd.DataFrame, kappa: float) -> dict:
    p_home = _winner_probs(rows, kappa)
    y = rows["y_home"].to_numpy(float)
    return {
        "n": int(len(rows)),
        "logloss": winner_logloss(rows, kappa),
        "logloss_1x2": backtest.logloss_1x2(rows, kappa),
        "brier": float(np.mean((p_home - y) ** 2)),
    }


def decide(metrics: dict) -> tuple[bool, bool]:
    """(use_lineup, use_h2h). Lineup must beat baseline on winner and 1X2 log loss; h2h must
    improve winner log loss on top of the chosen variant."""
    b, lu = metrics["baseline"], metrics["lineup"]
    use_lineup = bool(lu["logloss"] < b["logloss"] and lu["logloss_1x2"] < b["logloss_1x2"])
    chosen, plus = ("lineup", "lineup_h2h") if use_lineup else ("baseline", "h2h")
    use_h2h = bool(metrics[plus]["logloss"] < metrics[chosen]["logloss"])
    return use_lineup, use_h2h


def _run(tg, gg, season, p1, kappa, sims, fn) -> dict:
    res = backtest.run_backtest(tg, season, p1, n_sims=sims, gg=gg, goalie_mode=GOALIE_MODE,
                                mults_fn=fn)
    return _metrics(res["rows"], kappa)


def _report(args, rho: float, tune: dict, gate: dict, use_lineup: bool, use_h2h: bool) -> str:
    lines = [
        f"# Kokoonpano- ja keskinäiset-kertoimet: portti {args.gate_season}", "",
        f"Viritys {args.tune_season} toteutuneilla kokoonpanoilla (maalivahdit ennustettu, "
        f"{args.tune_sims} simulaatiota): rho {rho:.2f}.", "",
        "| rho | Voittaja log loss | 1X2 log loss |", "|---|---|---|",
    ]
    for k, v in tune.items():
        lines.append(f"| {k} | {v['logloss']:.5f} | {v['logloss_1x2']:.5f} |")
    names = {"baseline": "Perusmalli", "lineup": "Kokoonpano (ennustettu)",
             "h2h": "Keskinäiset", "lineup_h2h": "Kokoonpano + keskinäiset"}
    lines += [
        "", f"Portti {args.gate_season} ennustetuilla kokoonpanoilla ({args.sims} simulaatiota, "
        "samat siemenet kaikille):", "",
        "| Malli | n | Voittaja log loss | 1X2 log loss | Brier |", "|---|---|---|---|---|",
    ]
    for k in VARIANTS:
        m = gate[k]
        lines.append(f"| {names[k]} | {m['n']} | {m['logloss']:.5f} | {m['logloss_1x2']:.5f} "
                     f"| {m['brier']:.5f} |")
    lines += [
        "", f"Kokoonpanokerroin: {'KÄYTÖSSÄ' if use_lineup else 'EI KÄYTÖSSÄ'} "
        "(vaatii paremman voittaja- ja 1X2-log lossin kuin perusmalli).",
        f"Keskinäiset-kerroin: {'KÄYTÖSSÄ' if use_h2h else 'EI KÄYTÖSSÄ'} "
        "(vaatii paremman voittaja-log lossin valitun mallin päällä).",
    ]
    return "\n".join(lines) + "\n"


def _atomic_write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.lineup_backtest")
    ap.add_argument("--tune-season", default="20242025")
    ap.add_argument("--gate-season", default="20252026")
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--tune-sims", type=int, default=backtest.TUNE_SIMS)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    p = config.load_params()
    kappa = p.draw_kappa
    p1 = replace(p, draw_kappa=1.0)  # draw weight re-applied exactly to the rows
    seasons = sorted({s for r in (args.tune_season, args.gate_season)
                      for s in (backtest._season_before(r), r)})
    coefs_path = config.path(*backtest.BT_COEFS)
    tg = backtest.load_team_games(seasons, coefs_path, p.xg_weight)
    gg = backtest.load_goalie_games(seasons, coefs_path)
    pg = players.player_games(cache.load_seasons(seasons), xg.load_coefs(coefs_path))

    actual = lineup_inputs(pg, tg, args.tune_season, p, "actual")
    tune: dict[str, dict] = {
        "baseline": _run(tg, gg, args.tune_season, p1, kappa, args.tune_sims, None)}
    for rho in RHO_GRID:
        tune[f"{rho:.2f}"] = _run(tg, gg, args.tune_season, p1, kappa, args.tune_sims,
                                  mults_fn(actual, rho))
        logger.info("tune rho %.2f: %s", rho, tune[f"{rho:.2f}"])
    rho = min(RHO_GRID, key=lambda x: tune[f"{x:.2f}"]["logloss"])

    projected = lineup_inputs(pg, tg, args.gate_season, p, "projected")
    h2h = h2h_table(tg, args.gate_season, p)
    fns = {
        "baseline": None,
        "lineup": mults_fn(projected, rho),
        "h2h": mults_fn(None, rho, h2h),
        "lineup_h2h": mults_fn(projected, rho, h2h),
    }
    gate = {}
    for k, fn in fns.items():
        gate[k] = _run(tg, gg, args.gate_season, p1, kappa, args.sims, fn)
        logger.info("gate %s: %s", k, gate[k])
    use_lineup, use_h2h = decide(gate)

    lp = LineupParams(rho=rho, use_lineup=use_lineup, use_h2h=use_h2h)
    out = {**asdict(lp), "gate": {
        "season": args.gate_season, "tune_season": args.tune_season, "goalie_mode": GOALIE_MODE,
        "draw_kappa": kappa, "sims": args.sims, "tune_sims": args.tune_sims,
        "metrics": gate, "tune": tune, "passed_lineup": use_lineup, "passed_h2h": use_h2h,
    }}
    _atomic_write(config.path("model", "lineup_params.json"),
                  json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(config.path("reports", "lineup_backtest.md"),
                  _report(args, rho, tune, gate, use_lineup, use_h2h))
    logger.info("lineup gate: rho %.2f use_lineup %s use_h2h %s", rho, use_lineup, use_h2h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
