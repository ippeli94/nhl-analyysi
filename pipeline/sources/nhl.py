"""NHL web API: rate-limited HTTP, payload validation and offline fixtures (core source, spec 5.1).

Every response is validated for the fields the pipeline reads. A missing field or a
non-JSON response is a SourceError, and a SourceError from this module aborts the run.
"""

from __future__ import annotations

import json
import time
from typing import TypedDict

import requests

from pipeline import config

USER_AGENT = "nhl-analyysi/1.0"
MAX_ATTEMPTS = 1 + len(config.RETRY_BACKOFF_S)

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
_last_request: float | None = None


class SourceError(Exception):
    """A required NHL source is missing, malformed or unreachable."""


class Team(TypedDict):
    id: int
    abbr: str
    name: str
    logo: str
    logo_dark: str | None


class ScheduledGame(TypedDict):
    id: int
    season: str
    game_type: int
    nhl_date: str
    start_utc: str
    state: str
    home: Team
    away: Team


# Core identifiers. Names and dark logo are optional and fall back to None or the abbreviation.
_TEAM_FIELDS = ("id", "abbrev", "logo")
SCHEDULE_GAME_FIELDS = ["id", "season", "gameType", "startTimeUTC", "gameState"] + [
    f"{side}.{field}" for side in ("homeTeam", "awayTeam") for field in _TEAM_FIELDS
]
PLAY_FIELDS = ["typeDescKey", "periodDescriptor.number", "periodDescriptor.periodType"]
PBP_FIELDS = [
    "id",
    "gameState",
    "homeTeam.id",
    "homeTeam.abbrev",
    "awayTeam.id",
    "awayTeam.abbrev",
    "plays",
]
BOXSCORE_FIELDS = [
    "id",
    "gameState",
    "homeTeam.id",
    "homeTeam.abbrev",
    "awayTeam.id",
    "awayTeam.abbrev",
] + [
    f"playerByGameStats.{side}.{group}"
    for side in ("homeTeam", "awayTeam")
    for group in ("forwards", "defense", "goalies")
]


def fixture_name(path: str) -> str:
    return path.lstrip("/").replace("/", "__") + ".json"


def require(payload: dict, dotted_paths: list[str]) -> None:
    """Raise SourceError naming the first dotted path that is missing from `payload`."""
    for dotted in dotted_paths:
        node: object = payload
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                raise SourceError(f"missing field {dotted}")
            node = node[key]


def _throttle() -> None:
    """Keep at least 1 / REQUESTS_PER_S seconds between requests (monotonic clock)."""
    global _last_request
    if _last_request is not None:
        wait = 1 / config.REQUESTS_PER_S - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
    _last_request = time.monotonic()


def _read_offline(path: str) -> dict:
    fixture = config.OFFLINE_DIR / fixture_name(path)
    if not fixture.exists():
        raise SourceError(f"offline fixture missing: {fixture.name}")
    try:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SourceError(f"offline fixture is not JSON: {fixture.name}") from exc
    if not isinstance(payload, dict):
        raise SourceError(f"offline fixture is not an object: {fixture.name}")
    return payload


def _get_online(path: str) -> dict:
    url = config.NHL_BASE + path
    last_error = "no attempt made"
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(config.RETRY_BACKOFF_S[attempt - 1])
        _throttle()
        try:
            response = _session.get(url, timeout=config.TIMEOUT_S)
        except requests.RequestException as exc:
            last_error = type(exc).__name__
            continue
        if response.status_code == 404:
            raise SourceError(f"not found: {path}")
        if not response.ok:
            last_error = f"HTTP {response.status_code}"
            continue
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(f"not JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise SourceError(f"not a JSON object: {path}")
        return payload
    raise SourceError(f"{path} failed after {MAX_ATTEMPTS} attempts: {last_error}")


def get_json(path: str) -> dict:
    """GET `path` from the NHL API, or read its fixture when config.OFFLINE_DIR is set."""
    if config.OFFLINE_DIR is not None:
        return _read_offline(path)
    return _get_online(path)


def _localized(node: dict, key: str) -> str | None:
    value = node.get(key)
    return value.get("default") if isinstance(value, dict) else None


def _team(team: dict) -> Team:
    parts = [_localized(team, "placeName"), _localized(team, "commonName")]
    return {
        "id": team["id"],
        "abbr": team["abbrev"],
        "name": " ".join(part for part in parts if part) or team["abbrev"],
        "logo": team["logo"],
        "logo_dark": team.get("darkLogo"),
    }


def fetch_schedule_week(date: str) -> tuple[list[ScheduledGame], str | None]:
    """Games of the schedule week starting at `date`, and the next week's start date."""
    payload = get_json(f"/schedule/{date}")
    require(payload, ["gameWeek"])
    games: list[ScheduledGame] = []
    for day in payload["gameWeek"]:
        require(day, ["date", "games"])
        for game in day["games"]:
            # gameType is core: a game without it is a source failure. Non-regular games
            # (All-Star, preseason) are not modelled; skip them before the rest of validation
            # so one malformed exhibition game cannot abort the week.
            require(game, ["gameType"])
            if game["gameType"] not in config.CACHED_GAME_TYPES:
                continue
            require(game, SCHEDULE_GAME_FIELDS)
            games.append(
                {
                    "id": game["id"],
                    "season": str(game["season"]),
                    "game_type": game["gameType"],
                    "nhl_date": day["date"],
                    "start_utc": game["startTimeUTC"],
                    "state": game["gameState"],
                    "home": _team(game["homeTeam"]),
                    "away": _team(game["awayTeam"]),
                }
            )
    return games, payload.get("nextStartDate")


def fetch_pbp(game_id: int) -> dict:
    payload = get_json(f"/gamecenter/{game_id}/play-by-play")
    require(payload, PBP_FIELDS)
    for play in payload["plays"]:
        require(play, PLAY_FIELDS)
    return payload


def fetch_boxscore(game_id: int) -> dict:
    payload = get_json(f"/gamecenter/{game_id}/boxscore")
    require(payload, BOXSCORE_FIELDS)
    return payload
