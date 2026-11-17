"""Resumable history download into the game cache (spec 5.1).

    python -m pipeline.backfill --seasons 20232024 20242025 [--limit N]

Re-running is safe: cached games are counted as `already` and never fetched again.
"""

from __future__ import annotations

import argparse
import json
import logging

from pipeline import cache, config
from pipeline.sources import nhl

logger = logging.getLogger(__name__)

SEASON_START_MD = "09-25"
SEASON_END_MD = "06-30"
MAX_SKIPPED = 10  # more skipped games than this in one run exits 1 (the cache is still written)


def season_dates(season: str) -> tuple[str, str]:
    """Calendar window of a season id such as "20252026"."""
    return f"{season[:4]}-{SEASON_START_MD}", f"{season[4:]}-{SEASON_END_MD}"


def _season_games(season: str) -> list[nhl.ScheduledGame]:
    """Games of every schedule week from the season start until past its end."""
    start, end = season_dates(season)
    games: list[nhl.ScheduledGame] = []
    week: str | None = start
    while week is not None and week <= end:
        week_games, next_week = nhl.fetch_schedule_week(week)
        games.extend(week_games)
        if next_week is None or next_week <= week:
            break
        week = next_week
    return games


def run(seasons: list[str], limit: int | None = None) -> dict:
    """Cache the finished games of `seasons`.

    `limit` caps how many games are written in this run, so a long backfill can be split
    over several runs. Failing games do not count against it.
    """
    by_id: dict[int, nhl.ScheduledGame] = {}
    for season in seasons:
        for game in _season_games(season):
            by_id[game["id"]] = game
    finished = sorted(
        (g for g in by_id.values()
         if cache.is_finished(g) and g["game_type"] in config.CACHED_GAME_TYPES),
        key=lambda g: (g["nhl_date"], g["id"]),
    )
    cached = {s: cache.cached_ids(s) for s in {g["season"] for g in finished}}
    pending = [g for g in finished if g["id"] not in cached[g["season"]]]
    already = len(finished) - len(pending)
    written, skipped = cache.cache_finished(pending, limit=limit)
    return {"written": len(written), "skipped": len(skipped), "already": already}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cache finished games (resumable).")
    parser.add_argument("--seasons", nargs="+", required=True, choices=config.BACKFILL_SEASONS)
    parser.add_argument("--limit", type=int, default=None,
                        help="fetch at most N uncached games in this run")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = run(args.seasons, args.limit)
    except nhl.SourceError as exc:
        logger.error("NHL source failed, run aborted: %s", exc)
        return 1
    print(json.dumps(result))
    if result["skipped"] > MAX_SKIPPED:
        logger.error("%d games skipped (limit %d)", result["skipped"], MAX_SKIPPED)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
