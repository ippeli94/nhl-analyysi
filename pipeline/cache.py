"""Immutable per-game cache of finished games (spec 5.1, CachedGame).

One gzip JSON file per game: data/cache/games/{season}/{id}.json.gz. A file is written once
and never overwritten, so a re-run cannot change history.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import zlib
from pathlib import Path

from pipeline import config, shots
from pipeline.sources import nhl

logger = logging.getLogger(__name__)

RECORD_VERSION = 1
FINISHED_STATES = ("OFF", "FINAL")
DECIDED_TYPES = ("REG", "OT", "SO")
GAMES_DIR = ("data", "cache", "games")
SUFFIX = ".json.gz"
MAX_CONSECUTIVE_OUTAGES = 5
PROGRESS_EVERY = 100


def is_finished(game: nhl.ScheduledGame) -> bool:
    return game["state"] in FINISHED_STATES


def _toi_seconds(clock: str) -> int:
    minutes, seconds = clock.split(":")
    return int(minutes) * 60 + int(seconds)


def _name(player: dict) -> str | None:
    return (player.get("name") or {}).get("default")


def _skaters(team_box: dict, abbr: str) -> list[dict]:
    rows = []
    for group in ("forwards", "defense"):
        for p in team_box[group]:
            rows.append({
                "id": p["playerId"], "name": _name(p), "team": abbr, "pos": p["position"],
                "toi": _toi_seconds(p["toi"]), "g": p["goals"], "a": p["assists"], "sog": p["sog"],
            })
    return rows


def _goalies(team_box: dict, abbr: str) -> list[dict]:
    return [
        {
            "id": g["playerId"], "name": _name(g), "team": abbr, "started": bool(g["starter"]),
            "toi": _toi_seconds(g["toi"]), "sa": g["shotsAgainst"], "saves": g["saves"],
            "ga": g["goalsAgainst"],
        }
        for g in team_box["goalies"]
    ]


def build_record(pbp: dict, box: dict) -> dict:
    """CachedGame from one play-by-play and one boxscore payload.

    Raises ValueError when the game cannot be cached: no plays, an unknown outcome, or a
    payload missing a field the record needs.
    """
    if not pbp.get("plays"):
        raise ValueError(f"game {pbp.get('id')}: play-by-play has no plays")
    try:
        return _build(pbp, box)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"game {pbp.get('id')}: malformed payload ({exc})") from exc


def _build(pbp: dict, box: dict) -> dict:
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    decided = pbp["gameOutcome"]["lastPeriodType"]
    if decided not in DECIDED_TYPES:
        raise ValueError(f"game {pbp['id']}: unknown lastPeriodType {decided!r}")
    score = {"home": home["score"], "away": away["score"]}
    if decided == "REG":
        score_reg = dict(score)
    else:
        # OT or SO: the regulation score is the tie at the loser's score.
        tie = min(score.values())
        score_reg = {"home": tie, "away": tie}

    players = box["playerByGameStats"]
    return {
        "v": RECORD_VERSION,
        "id": pbp["id"],
        "season": str(pbp["season"]),
        "game_type": pbp["gameType"],
        "date": pbp["gameDate"],
        "start_utc": pbp["startTimeUTC"],
        "home": {"id": home["id"], "abbr": home["abbrev"]},
        "away": {"id": away["id"], "abbr": away["abbrev"]},
        "score": score,
        "score_reg": score_reg,
        "decided": decided,
        "shots": shots.extract_shots(pbp),
        "skaters": _skaters(players["homeTeam"], home["abbrev"])
        + _skaters(players["awayTeam"], away["abbrev"]),
        "goalies": _goalies(players["homeTeam"], home["abbrev"])
        + _goalies(players["awayTeam"], away["abbrev"]),
        "penalties": shots.extract_penalties(pbp),
    }


def game_path(season: str, game_id: int) -> Path:
    return config.path(*GAMES_DIR, season, f"{game_id}{SUFFIX}")


def write(record: dict) -> Path:
    """Write the record once. An existing file is left untouched and its path returned."""
    target = game_path(record["season"], record["id"])
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(record, separators=(",", ":")).encode("utf-8")
    # mtime=0 keeps identical input byte-identical. The temp file keeps an interrupted
    # write from leaving a partial file that would count as cached.
    payload = gzip.compress(data, mtime=0)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


def read(path: Path) -> dict:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


UNREADABLE = (OSError, EOFError, ValueError, zlib.error)


def read_or_discard(path: Path) -> dict | None:
    """Read a cache file; an unreadable one is logged and deleted, and None is returned.

    A corrupt file (for example zero-filled after an interrupted run) must never wedge the
    pipeline. Deleting it lets a later backfill or cache_finished fetch the game again.
    """
    try:
        return read(path)
    except UNREADABLE as exc:
        logger.warning("unreadable cache file %s (%s: %s); deleting it", path,
                       type(exc).__name__, exc)
        try:
            path.unlink()
        except OSError as unlink_exc:
            logger.warning("could not delete %s: %s", path, unlink_exc)
        return None


def cached_ids(season: str) -> set[int]:
    folder = config.path(*GAMES_DIR, season)
    if not folder.is_dir():
        return set()
    return {int(p.name.removesuffix(SUFFIX)) for p in folder.glob(f"*{SUFFIX}")}


def load_seasons(seasons: list[str]) -> list[dict]:
    records = [
        rec
        for season in seasons
        for game_id in cached_ids(season)
        if (rec := read_or_discard(game_path(season, game_id))) is not None
    ]
    return sorted(records, key=lambda r: (r["date"], r["id"]))


def cache_finished(
    games: list[nhl.ScheduledGame], limit: int | None = None,
) -> tuple[list[int], list[int]]:
    """Cache finished games of a cached game type that are not cached yet.

    Returns (written_ids, skipped_ids). `limit` caps the number of games written, so
    failing games do not use up the budget. A game that is not found (404, missing
    fixture), has no usable data (ValueError), or fails unexpectedly is logged with its id
    and skipped. Other source errors are outages: after MAX_CONSECUTIVE_OUTAGES in a row,
    the last SourceError is re-raised and the run stops. A successful game resets the count.
    """
    written: list[int] = []
    skipped: list[int] = []
    known: dict[str, set[int]] = {}
    outages = 0
    attempts = 0
    for game in games:
        if limit is not None and len(written) >= limit:
            break
        if not is_finished(game) or game["game_type"] not in config.CACHED_GAME_TYPES:
            continue
        season = game["season"]
        if season not in known:
            known[season] = cached_ids(season)
        if game["id"] in known[season]:
            if read_or_discard(game_path(season, game["id"])) is not None:
                continue
            known[season].discard(game["id"])
        attempts += 1
        if attempts % PROGRESS_EVERY == 0:
            logger.info("cache: %d games attempted, %d written, %d skipped",
                        attempts, len(written), len(skipped))
        try:
            pbp = nhl.fetch_pbp(game["id"])
            box = nhl.fetch_boxscore(game["id"])
            record = build_record(pbp, box)
        except (nhl.NotFoundError, ValueError) as exc:
            logger.warning("skipping game %s: %s", game["id"], exc)
            skipped.append(game["id"])
            continue
        except nhl.SourceError as exc:
            outages += 1
            logger.warning("source error for game %s (%d in a row): %s",
                           game["id"], outages, exc)
            if outages >= MAX_CONSECUTIVE_OUTAGES:
                raise
            skipped.append(game["id"])
            continue
        except Exception:
            logger.exception("skipping game %s: unexpected error", game["id"])
            skipped.append(game["id"])
            continue
        outages = 0
        write(record)
        known[season].add(game["id"])
        written.append(game["id"])
    return written, skipped
