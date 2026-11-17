"""Prediction and result logs (spec chapter 8, plan decision 2)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pipeline import config


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


def log_row(game_obj: dict, ts: str) -> dict:
    prob = game_obj["prob"]
    goalies = game_obj["goalies"]
    ml = game_obj["score"]["most_likely"]
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
        "lam_home": game_obj["xg"]["home"],
        "lam_away": game_obj["xg"]["away"],
        "total_mean": game_obj["score"]["total"]["mean"],
        "most_likely": [ml["home"], ml["away"]],
        "confidence": prob["confidence"],
        "goalie_home": goalies["home"]["id"] if goalies["home"] else None,
        "goalie_away": goalies["away"]["id"] if goalies["away"] else None,
        "market": None,
        "picks": None,
    }


def append_predictions(game_objs: list[dict], ts: str) -> int:
    by_season: dict[str, list[dict]] = {}
    for g in game_objs:
        if g["state"] == "upcoming":
            by_season.setdefault(season_of(g["id"]), []).append(log_row(g, ts))
    for season, rows in by_season.items():
        _append("predictions", season, rows)
    return sum(len(rows) for rows in by_season.values())


def read_log(kind: str, season: str) -> list[dict]:
    if kind not in ("predictions", "results"):
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
