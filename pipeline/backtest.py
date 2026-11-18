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
    "total_mean", "total_actual", "p_reg_home", "p_reg_away", "y_reg",
]
LOGLOSS_CLIP = 1e-6
DRAW_SECTION = "## Draw calibration"
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
                "p_reg_home": sim["prob"]["reg_home"], "p_reg_away": sim["prob"]["reg_away"],
                "y_reg": 1 if g.score_reg == g.opp_score_reg
                else (0 if g.score_reg > g.opp_score_reg else 2),
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


def reweight_draw(
    p_h: np.ndarray, p_d: np.ndarray, p_a: np.ndarray, kappa: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Re-apply a draw weight to 1X2 probabilities simulated with draw_kappa = 1 (exact)."""
    z = 1 + (kappa - 1) * p_d
    return p_h / z, kappa * p_d / z, p_a / z


def _reweighted(rows: pd.DataFrame, kappa: float) -> np.ndarray:
    h, d, a = reweight_draw(
        rows["p_reg_home"].to_numpy(float), rows["p_reg_draw"].to_numpy(float),
        rows["p_reg_away"].to_numpy(float), kappa,
    )
    return np.column_stack([h, d, a])


def logloss_1x2(rows: pd.DataFrame, kappa: float) -> float:
    """Mean -log of the reweighted probability of the actual regulation result."""
    probs = _reweighted(rows, kappa)
    y = rows["y_reg"].to_numpy(int)
    p = np.clip(probs[np.arange(len(y)), y], LOGLOSS_CLIP, None)
    return float(-np.log(p).mean())


def fit_draw_kappa(rows: pd.DataFrame) -> float:
    """Grid kappa with the lowest 1X2 log loss. Rows must come from a draw_kappa = 1 run."""
    lo, hi, step = config.KAPPA_GRID
    grid = np.arange(lo, hi + 1e-9, step)
    return float(grid[int(np.argmin([logloss_1x2(rows, float(k)) for k in grid]))])


def draw_report(rows: pd.DataFrame, kappa: float) -> dict:
    p_mean = float(_reweighted(rows, kappa)[:, 1].mean())
    y_mean = float((rows["y_reg"] == 1).mean())
    return {
        "n": int(len(rows)), "p_mean": p_mean, "y_mean": y_mean, "gap": p_mean - y_mean,
        "logloss_1x2": logloss_1x2(rows, kappa),
    }


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
    best = replace(best, draw_kappa=fit_draw_kappa(rows))
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


def _gate_result(args, judged: str, modes: dict, m: dict, passed: bool) -> dict:
    return {
        "passed": passed, "variant": args.variant, "season": args.gate_season, "n": m["n"],
        "brier": m["brier"], "brier_base": m["brier_base"],
        "logloss": m["logloss"], "logloss_base": m["logloss_base"],
        "accuracy": m["accuracy"], "goalie_mode": judged,
        "goalie_modes": {
            k: {"brier": v["metrics"]["brier"], "logloss": v["metrics"]["logloss"]}
            for k, v in modes.items()
        },
    }


def _draw_section(reports: dict[str, dict], at_one: dict[str, dict], kappa: float) -> str:
    lines = [
        DRAW_SECTION, "",
        f"Fitted kappa {kappa:.3f}.", "",
        "| Season | n | Model | Actual | Gap | 1X2 log loss (k=1) | 1X2 log loss (fitted) |",
        "|---|---|---|---|---|---|---|",
    ]
    for season, r in reports.items():
        lines.append(
            f"| {season} | {r['n']} | {r['p_mean']:.4f} | {r['y_mean']:.4f} | {r['gap']:+.4f} "
            f"| {at_one[season]['logloss_1x2']:.4f} | {r['logloss_1x2']:.4f} |"
        )
    return "\n".join(lines) + "\n"


def _write_report(main_text: str | None, draw_text: str) -> None:
    """Write main_text (or keep the existing report without its old draw section) + draw_text."""
    f = config.path("reports", "backtest.md")
    f.parent.mkdir(parents=True, exist_ok=True)
    text = main_text
    if text is None:
        text = f.read_text(encoding="utf-8") if f.exists() else ""
        text = text.split(DRAW_SECTION)[0].rstrip("\n")
        text = text + "\n" if text else ""
    f.write_text(text + ("\n" if text else "") + draw_text, encoding="utf-8")


def refit(args) -> int:
    """Refit draw_kappa on the pooled --refit-draw seasons and re-judge the gate (spec 15.3)."""
    p = config.load_params()
    old_gate = config.load_gate() or {}
    mode = old_gate.get("goalie_mode", "projected" if p.use_goalie else "none")
    fit_seasons = sorted(args.refit_draw)
    run_seasons = sorted({*fit_seasons, args.tune_season, args.gate_season})
    seasons = sorted({s for r in run_seasons for s in (_season_before(r), r)})
    coefs = config.path(*BT_COEFS)
    tg = load_team_games(seasons, coefs, p.xg_weight)
    gg = load_goalie_games(seasons, coefs)

    p1 = replace(p, draw_kappa=1.0)  # reweighting is exact only for kappa = 1 runs
    rows = {s: run_backtest(tg, s, p1, gg=gg, goalie_mode=mode)["rows"] for s in run_seasons}
    pooled = pd.concat([rows[s] for s in fit_seasons], ignore_index=True)
    kappa = fit_draw_kappa(pooled)
    rate_match = kappa_from(pooled)
    reports = {s: draw_report(rows[s], kappa) for s in run_seasons}
    at_one = {s: draw_report(rows[s], 1.0) for s in run_seasons}
    gap_gate = reports[args.gate_season]["gap"]
    current = fit_seasons[-1]
    if current != args.gate_season and abs(reports[current]["gap"]) > config.DRAW_GAP_CURRENT_MAX:
        logger.warning("draw gap %+.4f in %s exceeds %.3f", reports[current]["gap"], current,
                       config.DRAW_GAP_CURRENT_MAX)

    tuned = replace(p, draw_kappa=kappa)
    modes = {
        m: run_backtest(tg, args.gate_season, tuned, gg=gg, goalie_mode=m)
        for m in GOALIE_MODES
    }
    use_goalie = modes["projected"]["metrics"]["logloss"] <= modes["none"]["metrics"]["logloss"]
    judged = "projected" if use_goalie else "none"
    tuned = replace(tuned, use_goalie=use_goalie)
    m = modes[judged]["metrics"]
    gate_ok = gate(m)
    passed = gate_ok and abs(gap_gate) < config.DRAW_GAP_MAX
    draw_text = _draw_section(reports, at_one, kappa)

    if passed:
        result = _gate_result(args, judged, modes, m, passed)
        result["draw"] = {
            "kappa": kappa, "fit_seasons": fit_seasons, "kappa_rate_match": rate_match,
            "seasons": reports,
        }
        _write_json(("model", "params.json"), {**asdict(tuned), "gate": result})
        _write_json(
            ("model", f"backtest_{args.gate_season}.json"),
            {"season": args.gate_season, "variant": args.variant, "goalie_mode": judged, **m},
        )
        _write_report(_report(args.gate_season, args.variant, modes, judged, passed), draw_text)
    else:
        _write_report(None, draw_text)
    logger.info("refit kappa %.3f (rate match %.3f): gate %s, draw gap %+.4f -> %s",
                kappa, rate_match, "passed" if gate_ok else "FAILED", gap_gate,
                "written" if passed else "NOT written")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.backtest")
    ap.add_argument("--tune-season", required=True)
    ap.add_argument("--gate-season", required=True)
    ap.add_argument("--variant", choices=VARIANTS, default="full")
    ap.add_argument("--refit-draw", nargs="+", metavar="SEASON", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.refit_draw:
        return refit(args)

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

    result = _gate_result(args, judged, modes, m, passed)
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
