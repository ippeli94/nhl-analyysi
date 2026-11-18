"""Record a live The Odds API response as an offline fixture (manual, network).

The only odds code allowed to hit the network. The key comes from the environment only:

    ODDS_API_KEY=... python tools/record_odds_fixture.py [--probe-3way]

Saves tests/fixtures/odds__icehockey_nhl__live.json with every event id replaced by
"live-{n}" (real ids are 32 hex characters). --probe-3way spends one more call on the
first event to see which bookmakers return the `h2h_3_way` market. Never prints the key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config  # noqa: E402
from pipeline.sources import odds  # noqa: E402

OUT = config.path("tests", "fixtures", "odds__icehockey_nhl__live.json")


def probe_3way(event_id: str, key: str) -> None:
    try:
        response = requests.get(
            f"{config.ODDS_BASE}/sports/{config.ODDS_SPORT}/events/{event_id}/odds",
            params={"bookmakers": ",".join(config.ODDS_BOOKMAKERS), "markets": "h2h_3_way",
                    "oddsFormat": "decimal", "dateFormat": "iso"},
            headers={"x-api-key": key},
            timeout=config.TIMEOUT_S,
        )
    except requests.RequestException as exc:
        print(f"koe epäonnistui: {type(exc).__name__}", file=sys.stderr)
        return
    print(f"HTTP {response.status_code}")
    if response.ok:
        books = [b["key"] for b in response.json().get("bookmakers", []) if b.get("markets")]
        print("h2h_3_way:", ", ".join(books) if books else "ei yhtään")
    print("x-requests-last:", response.headers.get("x-requests-last"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Record Odds API featured markets as a fixture.")
    parser.add_argument("--probe-3way", action="store_true")
    args = parser.parse_args()
    key = os.environ.get(config.ODDS_ENV_KEY)
    if not key:
        print(f"{config.ODDS_ENV_KEY} puuttuu", file=sys.stderr)
        return 1
    try:
        result = odds.fetch_odds(datetime.now(UTC), key)
    except odds.SourceError as exc:
        print(f"virhe: {exc}", file=sys.stderr)
        return 1
    first_id = result.events[0].get("id") if result.events else None
    events = [{**event, "id": f"live-{n}"} for n, event in enumerate(result.events, 1)]
    OUT.write_text(json.dumps(events, indent=1), encoding="utf-8")
    print(f"{len(events)} tapahtumaa -> {OUT.name}")
    print(f"remaining={result.remaining} used={result.used} last={result.last}")
    if args.probe_3way:
        if first_id:
            probe_3way(first_id, key)
        else:
            print("ei tapahtumia, koetta ei voi tehdä")
    return 0


if __name__ == "__main__":
    sys.exit(main())
