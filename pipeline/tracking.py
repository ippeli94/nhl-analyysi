"""Prediction and result logs (spec chapter 8, plan decision 2)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from pipeline import config, value


def season_of(game_id: int) -> str:
    start = int(str(game_id)[:4])
    return f"{start}{start + 1}"


def _file(kind: str, season: str) -> Path:
    return config.path("data", "history", kind, f"{season}.jsonl")


def _append(kind: str, season: str, rows: list[dict]) -> None:
    f = _file(kind, season)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def log_row(game_obj: dict, ts: str) -> dict:
    prob = game_obj["prob"]
    goalies = game_obj["goalies"]
    ml = game_obj["score"]["most_likely"]
    odds = game_obj.get("odds")
    return {
        "ts": ts,
        "model_version": config.MODEL_VERSION,
        "game_id": game_obj["id"],
        "nhl_date": game_obj["nhl_date"],
        "start_utc": game_obj["start_utc"],
        "home": game_obj["home"]["abbr"],
        "away": game_obj["away"]["abbr"],
        "p_home_win": prob["home_win"],
        "p_reg": [prob["reg_home"], prob["reg_draw"], prob["reg_away"]],
        "lam_home": game_obj["_internal"]["lam_home"],
        "lam_away": game_obj["_internal"]["lam_away"],
        "total_mean": game_obj["score"]["total"]["mean"],
        "most_likely": [ml["home"], ml["away"]],
        "confidence": prob["confidence"],
        "goalie_home": goalies["home"]["id"] if goalies["home"] else None,
        "goalie_away": goalies["away"]["id"] if goalies["away"] else None,
        "market": value.market_summary(odds),
        "picks": (odds or {}).get("picks"),
    }


def append_predictions(game_objs: list[dict], ts: str) -> int:
    by_season: dict[str, list[dict]] = {}
    for g in game_objs:
        if g["state"] == "upcoming":
            by_season.setdefault(season_of(g["id"]), []).append(log_row(g, ts))
    for season, rows in by_season.items():
        _append("predictions", season, rows)
    return sum(len(rows) for rows in by_season.values())


def odds_rows(snapshot: dict, games: list[dict]) -> list[dict]:
    """One odds history row per snapshot game that is also among the exported games."""
    by_id = {str(g["id"]): g for g in games}
    rows = []
    for gid, books in snapshot["games"].items():
        game = by_id.get(gid)
        if game is None:
            continue
        rows.append({
            "ts": snapshot["fetched_at"],
            "game_id": game["id"],
            "start_utc": game["start_utc"],
            "home": game["home"]["abbr"],
            "away": game["away"]["abbr"],
            "books": books,
            "ref": value.ref_summary(books),
        })
    return rows


def append_odds(rows: list[dict]) -> int:
    by_season: dict[str, list[dict]] = {}
    for row in rows:
        by_season.setdefault(season_of(row["game_id"]), []).append(row)
    for season, season_rows in by_season.items():
        _append("odds", season, season_rows)
    return len(rows)


def read_log(kind: str, season: str) -> list[dict]:
    if kind not in ("predictions", "results", "odds"):
        raise ValueError(f"unknown log kind: {kind}")
    f = _file(kind, season)
    if not f.exists():
        return []
    lines = f.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def record_results(records: list[dict]) -> int:
    by_season: dict[str, list[dict]] = {}
    for rec in records:
        by_season.setdefault(season_of(rec["game_id"]), []).append(rec)
    added = 0
    for season, recs in by_season.items():
        seen = {row["game_id"] for row in read_log("results", season)}
        new = []
        for rec in recs:
            if rec["game_id"] not in seen:
                seen.add(rec["game_id"])
                new.append(rec)
        if new:
            _append("results", season, new)
            added += len(new)
    return added


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def official(rows: list[dict]) -> dict[int, dict]:
    """Per game the row with the greatest ts strictly before start_utc."""
    best: dict[int, dict] = {}
    for row in rows:
        ts = _parse(row["ts"])
        if ts >= _parse(row["start_utc"]):
            continue
        cur = best.get(row["game_id"])
        if cur is None or ts > _parse(cur["ts"]):
            best[row["game_id"]] = row
    return best


def draw_gap(pred_rows: list[dict], result_rows: list[dict], season: str) -> dict | None:
    """Model regulation-draw probability vs actual draw rate (plan decision 26)."""
    results = {}
    for r in result_rows:
        results.setdefault(r["game_id"], r)
    rows = [
        (row, results[gid])
        for gid, row in official(pred_rows).items()
        if gid in results and row.get("model_version") == config.MODEL_VERSION
        and season_of(gid) == season
    ]
    rows.sort(key=lambda pair: (pair[0]["start_utc"], pair[0]["game_id"]))
    rows = rows[-config.DRAW_MONITOR_WINDOW:]
    if not rows:
        return None
    n = len(rows)
    model = sum(p["p_reg"][1] for p, _ in rows) / n
    actual = sum(r["reg_home_goals"] == r["reg_away_goals"] for _, r in rows) / n
    model, actual = round(model, 4), round(actual, 4)
    gap = round(model - actual, 4)
    return {
        "season": season, "model_version": config.MODEL_VERSION, "n": n,
        "model": model, "actual": actual, "gap": gap,
        "warn": n >= config.DRAW_MONITOR_MIN_GAMES and abs(gap) > config.DRAW_MONITOR_GAP,
    }
