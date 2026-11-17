"""Day-by-day leak-free backtest, tuning and go-live gate (spec 8, 15.1-15.3)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import adjust, cache, config, features, metrics, ratings, xg
from pipeline.config import ModelParams
from pipeline.predict import game_lambdas
from pipeline.ratings import split_lambda
from pipeline.simulate import simulate

logger = logging.getLogger(__name__)

GOALIE_MODES = ("none", "actual", "projected")
ROW_COLUMNS = [
    "game_id", "date", "p_home", "y_home", "p_reg_draw", "y_reg_draw",
    "total_mean", "total_actual",
]
HALF_LIVES = (25, 40, 60)
ALPHAS = (0.005, 0.02, 0.05, 0.2)
SIGMAS = (0.0, 0.08, 0.15)
GOALIE_WEIGHTS = (0.0, 0.3, 0.6, 0.9)
TUNE_SIMS = 1000
PROGRESS_EVERY = 20
BT_COEFS = ("model", "xg_coefs_bt.json")
VARIANTS = ("full", "no_xg", "no_periods")


def _season_before(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}{start + 1}"


def _base_rate(tg: pd.DataFrame, season: str) -> float:
    prev = tg[(tg["season"] == _season_before(season)) & tg["is_home"]
              & tg["game_type"].isin(config.TRAIN_GAME_TYPES)]
    return float(prev["won"].mean()) if len(prev) else 0.5


def _actual_starters(gg_game: pd.DataFrame) -> dict[str, list[tuple[int, float]]]:
    """The boxscore starter of each team in one game, probability 1.0."""
    out: dict[str, list[tuple[int, float]]] = {}
    for team, f in gg_game[gg_game["started"]].groupby("team"):
        out[str(team)] = [(int(f.sort_values("toi", ascending=False)["goalie"].iloc[0]), 1.0)]
    return out


def run_backtest(
    tg: pd.DataFrame,
    season: str,
    p: ModelParams,
    n_sims: int = 2000,
    gg: pd.DataFrame | None = None,
    goalie_mode: str = "none",
) -> dict:
    """Predict every regular-season game of `season` using only earlier rows."""
    if goalie_mode not in GOALIE_MODES:
        raise ValueError(f"unknown goalie_mode {goalie_mode!r}")
    if goalie_mode != "none" and gg is None:
        raise ValueError(f"goalie_mode {goalie_mode!r} needs goalie games")
    games = tg[(tg["season"] == season) & tg["is_home"]
               & tg["game_type"].isin(config.TRAIN_GAME_TYPES)]
    base_rate = _base_rate(tg, season)
    dates = sorted(games["date"].unique())
    rows: list[dict] = []
    lams: list[float] = []
    ice: list[float] = []
    gks: list[float] = []
    lo, hi = config.GOALIE_MULT_CLAMP
    gr_empty = adjust.GoalieRatings()

    for i, day in enumerate(dates):
        ts = pd.Timestamp(day)
        asof: date = ts.date()
        hist = tg[tg["date"] < ts]
        r = ratings.fit(hist, asof, p, season=season)
        ps = ratings.period_shares(hist, asof, p, season=season)
        sp = features.sim_params(hist) if len(hist) else {
            "league_period_share": list(config.LEAGUE_PERIOD_SHARE_FALLBACK),
            "en_prob": dict(config.EN_PROB_FALLBACK),
        }
        use_gg = gg is not None and goalie_mode != "none"
        gg_hist = gg[gg["date"] < ts] if use_gg else None
        gr = adjust.goalie_ratings(gg_hist, asof, p, season=season) if use_gg else gr_empty
        day_gg = gg[gg["date"] == ts] if goalie_mode == "actual" else None
        for g in games[games["date"] == ts].itertuples():
            home, away = g.team, g.opp
            if goalie_mode == "none":
                st_h, st_a = [], []
            elif goalie_mode == "actual":
                actual = _actual_starters(day_gg[day_gg["game_id"] == g.game_id])
                st_h, st_a = actual.get(home, []), actual.get(away, [])
            else:
                st_h = adjust.project_starters(gg_hist, home, asof, season=season)
                st_a = adjust.project_starters(gg_hist, away, asof, season=season)
            lm = game_lambdas(r, home, away, bool(g.b2b), bool(g.opp_b2b), st_h, st_a, gr, p)
            sim = simulate(
                split_lambda(lm["lam_h"], home, away, ps, p.use_periods),
                split_lambda(lm["lam_a"], away, home, ps, p.use_periods),
                sp, p, seed=int(g.game_id), n=n_sims,
            )
            lams += [lm["lam_h"], lm["lam_a"]]
            ice.append(lm["ice_h"])
            if goalie_mode != "none":
                gks += [lm["gk_h"], lm["gk_a"]]
            rows.append({
                "game_id": int(g.game_id), "date": ts,
                "p_home": sim["prob"]["home_win"], "y_home": float(g.won),
                "p_reg_draw": sim["prob"]["reg_draw"],
                "y_reg_draw": float(g.score_reg == g.opp_score_reg),
                "total_mean": sim["score"]["total"]["mean"],
                "total_actual": float(g.score_reg + g.opp_score_reg + (g.decided != "REG")),
            })
        if (i + 1) % PROGRESS_EVERY == 0:
            logger.info("backtest %s [%s]: %d/%d dates", season, goalie_mode, i + 1, len(dates))

    frame = pd.DataFrame(rows, columns=ROW_COLUMNS)
    clamp_share = (
        float(np.mean([g <= lo + 1e-9 or g >= hi - 1e-9 for g in gks])) if gks else 0.0
    )
    return {
        "rows": frame,
        "metrics": metrics.score(frame["p_home"].to_numpy(), frame["y_home"].to_numpy(), base_rate),
        "diag": {
            "home_mult_mean": float(np.mean(ice)) if ice else None,
            "lambda_mean": float(np.mean(lams)) if lams else None,
            "goalie_clamp_share": clamp_share,
        },
    }


def load_team_games(seasons: list[str], coefs_path: Path, xg_weight: float) -> pd.DataFrame:
    return features.team_games(cache.load_seasons(seasons), xg.load_coefs(coefs_path), xg_weight)


def load_goalie_games(seasons: list[str], coefs_path: Path) -> pd.DataFrame:
    return adjust.goalie_games(cache.load_seasons(seasons), xg.load_coefs(coefs_path))


def kappa_from(rows: pd.DataFrame) -> float:
    """Draw weight that matches the simulated regulation draw rate to the actual one."""
    a = float(rows["y_reg_draw"].mean())
    d = float(rows["p_reg_draw"].mean())
    return a * (1 - d) / (d * (1 - a))


def gate(m: dict) -> bool:
    return bool(m["brier"] < m["brier_base"] and m["logloss"] < m["logloss_base"])


def _logloss(tg, season, p, gg=None, mode="none") -> float:
    res = run_backtest(tg, season, p, n_sims=TUNE_SIMS, gg=gg, goalie_mode=mode)
    return res["metrics"]["logloss"]


def tune(
    tg: pd.DataFrame, season: str, base: ModelParams, gg: pd.DataFrame | None = None
) -> ModelParams:
    best = replace(base, draw_kappa=1.0)
    grid = [
        replace(best, half_life_days=float(h), alpha=float(a))
        for h in HALF_LIVES for a in ALPHAS
    ]
    best = min(grid, key=lambda q: _logloss(tg, season, q))
    logger.info("tune: half_life=%s alpha=%s", best.half_life_days, best.alpha)
    sigmas = [replace(best, sigma_pace=s, sigma_team=s) for s in SIGMAS]
    best = min(sigmas, key=lambda q: _logloss(tg, season, q))
    logger.info("tune: sigma=%s", best.sigma_team)
    rows = run_backtest(tg, season, best, n_sims=TUNE_SIMS)["rows"]
    best = replace(best, draw_kappa=kappa_from(rows))
    if gg is not None:
        weights = [replace(best, goalie_weight=w) for w in GOALIE_WEIGHTS]
        best = min(weights, key=lambda q: _logloss(tg, season, q, gg, "actual"))
        logger.info("tune: goalie_weight=%s", best.goalie_weight)
    return best


def _variant_params(variant: str) -> ModelParams:
    p = ModelParams()
    if variant == "no_xg":
        p = replace(p, xg_weight=0.0)
    elif variant == "no_periods":
        p = replace(p, xg_weight=0.0, use_periods=False)
    return p


def _report(season: str, variant: str, modes: dict, judged: str, passed: bool) -> str:
    m = modes[judged]["metrics"]
    diag = modes[judged]["diag"]
    lines = [
        f"# Backtest {season} (variant {variant})",
        "",
        "| Mode | n | Brier | Log loss | Accuracy |",
        "|---|---|---|---|---|",
    ]
    for name, res in modes.items():
        mm = res["metrics"]
        lines.append(
            f"| {name} | {mm['n']} | {mm['brier']:.4f} | {mm['logloss']:.4f} "
            f"| {mm['accuracy']:.3f} |"
        )
    lines += [
        f"| baseline (home win rate) | {m['n']} | {m['brier_base']:.4f} "
        f"| {m['logloss_base']:.4f} | |",
        "",
        f"Judged on: {judged}. Gate: {'PASSED' if passed else 'FAILED'}.",
        f"Mean lambda per team: {diag['lambda_mean']:.2f}; mean home multiplier: "
        f"{diag['home_mult_mean']:.3f}; goalie multipliers at clamp: "
        f"{diag['goalie_clamp_share'] * 100:.1f} %.",
    ]
    return "\n".join(lines) + "\n"


def _write_json(parts: tuple[str, ...], obj) -> None:
    f = config.path(*parts)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.backtest")
    ap.add_argument("--tune-season", required=True)
    ap.add_argument("--gate-season", required=True)
    ap.add_argument("--variant", choices=VARIANTS, default="full")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    base = _variant_params(args.variant)
    seasons = sorted({_season_before(args.tune_season), args.tune_season,
                      _season_before(args.gate_season), args.gate_season})
    coefs = config.path(*BT_COEFS)
    tg = load_team_games(seasons, coefs, base.xg_weight)
    gg = load_goalie_games(seasons, coefs)

    tuned = tune(tg, args.tune_season, base, gg)
    modes = {
        mode: run_backtest(tg, args.gate_season, tuned, gg=gg, goalie_mode=mode)
        for mode in GOALIE_MODES
    }
    use_goalie = modes["projected"]["metrics"]["logloss"] <= modes["none"]["metrics"]["logloss"]
    judged = "projected" if use_goalie else "none"
    tuned = replace(tuned, use_goalie=use_goalie)
    m = modes[judged]["metrics"]
    passed = gate(m)

    result = {
        "passed": passed, "variant": args.variant, "season": args.gate_season, "n": m["n"],
        "brier": m["brier"], "brier_base": m["brier_base"],
        "logloss": m["logloss"], "logloss_base": m["logloss_base"],
        "accuracy": m["accuracy"], "goalie_mode": judged,
        "goalie_modes": {
            k: {"brier": v["metrics"]["brier"], "logloss": v["metrics"]["logloss"]}
            for k, v in modes.items()
        },
    }
    _write_json(("model", "params.json"), {**asdict(tuned), "gate": result})
    _write_json(
        ("model", f"backtest_{args.gate_season}.json"),
        {"season": args.gate_season, "variant": args.variant, "goalie_mode": judged, **m},
    )
    report = config.path("reports", "backtest.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_report(args.gate_season, args.variant, modes, judged, passed),
                      encoding="utf-8")
    logger.info("gate %s on %s", "passed" if passed else "FAILED", judged)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
