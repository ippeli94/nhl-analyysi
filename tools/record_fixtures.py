"""Record NHL API responses as offline test fixtures.

The only code in the project allowed to hit the network. Run by hand:

    .venv/Scripts/python tools/record_fixtures.py --date 2026-11-10

Saves compact JSON to tests/fixtures/, named like pipeline.sources.nhl.fixture_name:
  - schedule week of --date and the week 7 days later (upcoming games)
  - play-by-play and boxscore of a finished REG game and a finished SO game
    (OT as fallback), searched backwards week by week
  - landing of one upcoming game
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config  # noqa: E402

USER_AGENT = "nhl-analyysi/1.0"
SLEEP_S = 0.4
MAX_WEEKS_BACK = 12
FINISHED_STATES = ("OFF", "FINAL")
UPCOMING_STATES = ("FUT", "PRE")

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT
_last_call = 0.0


def fixture_name(api_path: str) -> str:
    return api_path.lstrip("/").replace("/", "__") + ".json"


def get(api_path: str) -> dict:
    global _last_call
    wait = SLEEP_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    response = session.get(config.NHL_BASE + api_path, timeout=config.TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def save(out_dir: Path, api_path: str, payload: dict) -> None:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    (out_dir / fixture_name(api_path)).write_text(text, encoding="utf-8")
    print(f"saved {fixture_name(api_path)} ({len(text) // 1024} KB)")


def week_games(payload: dict) -> list[dict]:
    return [game for day in payload.get("gameWeek", []) for game in day.get("games", [])]


def find_finished(start: date) -> tuple[dict | None, dict | None]:
    """Search finished weeks backwards from `start`. Returns (REG game, SO game or OT fallback)."""
    reg = None
    so = None
    ot = None
    for weeks_back in range(MAX_WEEKS_BACK):
        day = start - timedelta(days=7 * weeks_back)
        for game in week_games(get(f"/schedule/{day.isoformat()}")):
            if game.get("gameState") not in FINISHED_STATES:
                continue
            last = _last_period_type(game)
            if last == "REG" and reg is None:
                reg = game
            elif last == "SO" and so is None:
                so = game
            elif last == "OT" and ot is None:
                ot = game
        if reg is not None and so is not None:
            break
        print(
            f"week of {day.isoformat()}: REG={reg is not None} "
            f"SO={so is not None} OT={ot is not None}"
        )
    if so is None:
        so = ot
    return reg, so


def _last_period_type(game: dict) -> str | None:
    outcome = game.get("gameOutcome")
    if outcome is None:
        # Schedule entry lacks the outcome; the play-by-play carries it.
        outcome = get(f"/gamecenter/{game['id']}/play-by-play").get("gameOutcome", {})
    return outcome.get("lastPeriodType")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--date", required=True, help="YYYY-MM-DD, start of a finished schedule week"
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.date)
    upcoming_day = start + timedelta(days=7)
    out_dir = config.path("tests", "fixtures")
    out_dir.mkdir(parents=True, exist_ok=True)

    schedule = get(f"/schedule/{start.isoformat()}")
    save(out_dir, f"/schedule/{start.isoformat()}", schedule)
    upcoming_schedule = get(f"/schedule/{upcoming_day.isoformat()}")
    save(out_dir, f"/schedule/{upcoming_day.isoformat()}", upcoming_schedule)

    reg, so = find_finished(start)
    if reg is None or so is None:
        raise SystemExit(
            f"no finished REG and SO/OT game found within {MAX_WEEKS_BACK} weeks of {args.date}"
        )

    for game in (reg, so):
        for suffix in ("play-by-play", "boxscore"):
            api_path = f"/gamecenter/{game['id']}/{suffix}"
            save(out_dir, api_path, get(api_path))

    upcoming = next(
        (g for g in week_games(upcoming_schedule) if g.get("gameState") in UPCOMING_STATES), None
    )
    if upcoming is None:
        raise SystemExit(f"no upcoming game in week of {upcoming_day.isoformat()}")
    landing_path = f"/gamecenter/{upcoming['id']}/landing"
    save(out_dir, landing_path, get(landing_path))

    print(f"REG game id: {reg['id']}")
    print(f"SO/OT game id: {so['id']} (lastPeriodType {_last_period_type(so)})")
    print(f"upcoming game id: {upcoming['id']}")


if __name__ == "__main__":
    main()
