"""latest.json build, schema validation and atomic write (spec 4.5)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from pipeline import config

SCHEMA_FILE = Path(__file__).resolve().parent / "schema" / "latest.schema.json"


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _utc_z(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _source(src_id: str, state: dict) -> dict:
    return {
        "id": src_id,
        "status": state["status"],
        "fetched_at": state.get("fetched_at"),
        "note": state.get("note"),
    }


def build_latest(
    games: list[dict], sources: dict, next_game_date: str | None, now: datetime,
    calibration: dict | None = None,
) -> dict:
    """Top-level latest.json object. Keys starting with "_" are private and never exported."""
    public = [
        {k: v for k, v in g.items() if not k.startswith("_")}
        for g in games
    ]
    public.sort(key=lambda g: (g["start_utc"], g["id"]))
    return {
        "schema_version": config.SCHEMA_VERSION,
        "generated_at": _utc_z(now),
        "model_version": config.MODEL_VERSION,
        "sources": [_source(k, v) for k, v in sources.items()],
        "review": {"problems": 0, "worst": None},
        "games": public,
        "next_game_date": next_game_date,
        "calibration": calibration or {"draw": None},
    }


def validate(obj: dict) -> list[str]:
    """Schema error messages with their JSON path; empty when valid."""
    errors = sorted(_validator().iter_errors(obj), key=lambda e: e.json_path)
    return [f"{e.json_path}: {e.message}" for e in errors]


def write_latest(obj: dict) -> Path:
    """Validate, then write site/data/latest.json atomically. Raises ValueError if invalid."""
    errors = validate(obj)
    if errors:
        raise ValueError("latest.json is invalid:\n" + "\n".join(errors))
    target = config.path("site", "data", "latest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".latest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target
