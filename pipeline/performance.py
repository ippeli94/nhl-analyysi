"""Performance metrics from the history logs (spec chapter 8, plan decisions 14, 15, 17, 32).

Everything is derived from the append-only logs: the official prediction of a game
(`tracking.official`) is joined to its result by game id.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from pipeline import config, export, metrics, tracking, value

SMALL_SAMPLE = 100
RECENT_MAX = 50
WINDOW_DAYS = 30
BACKTEST_LABEL = "Backtest 2025–26"
VEIKKAUS = "veikkaus"


def settle(pick: dict, result: dict, *, price: float | None = None,
           book: str | None = None) -> float | None:
    """Profit of one pick per 1 unit staked; None when the pick cannot be settled.

    `price`/`book` override the pick's own (used for the Veikkaus price).
    """
    market, outcome, line = pick.get("market"), pick.get("outcome"), pick.get("line")
    price = pick.get("price") if price is None else price
    book = pick.get("book") if book is None else book
    if price is None:
        return None
    if market == "h2h":
        if outcome not in ("home", "away"):
            return None
        won = bool(result["home_won"]) == (outcome == "home")
    elif market == "1x2":
        hg, ag = result.get("reg_home_goals"), result.get("reg_away_goals")
        if hg is None or ag is None or outcome not in ("home", "draw", "away"):
            return None
        actual = "home" if hg > ag else "away" if ag > hg else "draw"
        won = actual == outcome
    elif market == "totals":
        if line is None or outcome not in ("over", "under"):
            return None
        total = result["home_goals"] + result["away_goals"]
        if total == line:
            return 0.0
        won = (total > line) == (outcome == "over")
    elif market == "spreads":
        if line is None or outcome not in ("home", "away"):
            return None
        cover = result["home_goals"] - result["away_goals"] + line
        if cover == 0:
            return 0.0
        won = (cover > 0) == (outcome == "home")
    else:
        return None
    if not won:
        return -1.0
    return value.effective_price(book or "", price) - 1.0


def _p_close(ref: dict | None, pick: dict) -> float | None:
    if not ref:
        return None
    market, outcome, line = pick["market"], pick["outcome"], pick.get("line")
    if market == "h2h":
        p = ref.get("h2h")
        return None if not p else p[0 if outcome == "home" else 1]
    if market == "1x2":
        p = ref.get("1x2")
        return None if not p else p[{"home": 0, "draw": 1, "away": 2}[outcome]]
    if line is None:
        return None
    key = str(float(line))
    if market == "totals":
        p = (ref.get("totals") or {}).get(key)
        return None if p is None else (p if outcome == "over" else 1.0 - p)
    p = (ref.get("spreads") or {}).get(key)
    return None if p is None else (p if outcome == "home" else 1.0 - p)


def _pick_stats(profits: list[float]) -> dict:
    n = len(profits)
    hits = sum(p > 0 for p in profits)
    return {
        "n": n, "hits": hits,
        "hit_rate": _round(hits / n) if n else None,
        "roi": _round(sum(profits) / n) if n else None,
    }


def _market_home(row: dict) -> float | None:
    h2h = (row.get("market") or {}).get("h2h")
    return None if not h2h else float(h2h[0])


def _round(x: float | None) -> float | None:
    return None if x is None else round(float(x), 5)


def _window(pairs: list[tuple[dict, dict]], base_rate: float, closing: dict[int, dict],
            players: pd.DataFrame | None) -> dict:
    n = len(pairs)
    p = np.array([row["p_home_win"] for row, _ in pairs], dtype=float)
    y = np.array([float(res["home_won"]) for _, res in pairs], dtype=float)
    sc = metrics.score(p, y, base_rate)

    with_market = [(m, float(res["home_won"])) for row, res in pairs
                   if (m := _market_home(row)) is not None]
    market = metrics.score(np.array([m for m, _ in with_market], dtype=float),
                           np.array([yy for _, yy in with_market], dtype=float), base_rate)

    total_mae = None
    if n:
        total_mae = float(np.mean([
            abs(row["total_mean"] - (res["home_goals"] + res["away_goals"]))
            for row, res in pairs]))

    groups: dict[str, list[float]] = {"all": [], "no_flag": [], "veikkaus": []}
    clv: list[float] = []
    for row, res in pairs:
        for pick in row.get("picks") or []:
            profit = settle(pick, res)
            if profit is None:
                continue
            groups["all"].append(profit)
            if pick.get("flag") is None:
                groups["no_flag"].append(profit)
            vk = pick.get("veikkaus")
            if vk and vk.get("value"):
                vp = settle(pick, res, price=vk["price"], book=VEIKKAUS)
                if vp is not None:
                    groups["veikkaus"].append(vp)
            close = closing.get(row["game_id"])
            p_close = _p_close(close.get("ref") if close else None, pick)
            if p_close is not None:
                clv.append(value.clv(pick["price"], p_close))

    player_block = None
    if players is not None and len(players):
        ids = {row["game_id"] for row, _ in pairs}
        sub = players[players["game_id"].isin(ids)]
        if len(sub):
            player_block = {
                "n": len(sub),
                "brier": _round(((sub["p_pts1"] - sub["y_pts"]) ** 2).mean()),
                "brier_base": _round(((sub["base_pts1"] - sub["y_pts"]) ** 2).mean()),
            }

    return {
        "n": n,
        "small_sample": n < SMALL_SAMPLE,
        "brier": _round(sc["brier"]), "logloss": _round(sc["logloss"]),
        "brier_base": _round(sc["brier_base"]), "logloss_base": _round(sc["logloss_base"]),
        "n_market": market["n"],
        "brier_market": _round(market["brier"]), "logloss_market": _round(market["logloss"]),
        "accuracy": _round(sc["accuracy"]), "accuracy_market": _round(market["accuracy"]),
        "calibration": [{**b, "p_mean": round(b["p_mean"], 5), "y_mean": round(b["y_mean"], 5)}
                        for b in sc["calibration"]],
        "total_mae": _round(total_mae),
        "picks": {k: _pick_stats(v) for k, v in groups.items()},
        "clv_mean": _round(sum(clv) / len(clv)) if clv else None,
        "clv_n": len(clv),
        "players": player_block,
    }


def _backtest_block(backtest: dict | None) -> dict | None:
    if not backtest:
        return None
    return {
        "label": BACKTEST_LABEL, "n": backtest["n"],
        "brier": _round(backtest["brier"]), "brier_base": _round(backtest["brier_base"]),
        "logloss": _round(backtest["logloss"]), "logloss_base": _round(backtest["logloss_base"]),
    }


def build_performance(pred: list[dict], results: list[dict], odds: list[dict],
                      player_rows: pd.DataFrame | None, now: datetime,
                      backtest: dict | None) -> dict:
    by_result: dict[int, dict] = {}
    for r in results:
        by_result.setdefault(r["game_id"], r)
    # decision 32: base rate = the season's resolved home-win rate
    base_rate = (float(np.mean([float(r["home_won"]) for r in by_result.values()]))
                 if by_result else 0.5)
    pairs = [(row, by_result[gid]) for gid, row in tracking.official(pred).items()
             if gid in by_result]
    pairs.sort(key=lambda pr: (pr[1]["date"], pr[0]["start_utc"], pr[0]["game_id"]))
    closing = tracking.official(odds)

    cutoff = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    recent_pairs = [pr for pr in pairs if pr[1]["date"] >= cutoff]

    recent = []
    for row, res in reversed(pairs[-RECENT_MAX:]):
        won = bool(res["home_won"])
        recent.append({
            "date": res["date"], "home": row["home"], "away": row["away"],
            "p_home": _round(row["p_home_win"]),
            "score": f"{res['home_goals']}-{res['away_goals']}",
            "won_home": won, "hit": (row["p_home_win"] >= 0.5) == won,
        })

    return {
        "generated_at": export._utc_z(now),
        "model_version": config.MODEL_VERSION,
        "windows": {
            "30d": _window(recent_pairs, base_rate, closing, player_rows),
            "season": _window(pairs, base_rate, closing, player_rows),
        },
        "recent": recent,
        "backtest": _backtest_block(backtest),
    }
