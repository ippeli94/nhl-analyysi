"""The Odds API v4: fetch, parse and match bookmaker prices (optional source, spec 6.11).

The API key is sent only in the `x-api-key` header and never appears in a URL or an
exception message. Odds API event ids are never kept (they look like leaked keys).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import requests

from pipeline import config, teams
from pipeline.sources.nhl import USER_AGENT, ScheduledGame, SourceError

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


class BookMarkets(TypedDict):
    h2h: list[float] | None
    h2h3: list[float] | None
    totals: list[list[float]]
    spreads: list[list[float]]


class ParsedEvent(TypedDict):
    home_name: str
    away_name: str
    home: str | None
    away: str | None
    commence_utc: str
    books: dict[str, BookMarkets]


@dataclass
class OddsResponse:
    events: list[dict]
    remaining: int | None
    used: int | None
    last: int | None
    fallback: bool = False


def _int_header(headers, name: str) -> int | None:
    try:
        return int(float(headers.get(name)))
    except (TypeError, ValueError, OverflowError):
        return None


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _fixture_response() -> OddsResponse:
    fixture = config.OFFLINE_DIR / config.ODDS_FIXTURE
    if not fixture.exists():
        raise SourceError("odds fixture missing")
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SourceError("Odds API: response is not a list")
    return OddsResponse(raw, None, None, None, False)


def _request(params: dict, api_key: str):
    try:
        return _session.request(
            "GET",
            f"{config.ODDS_BASE}/sports/{config.ODDS_SPORT}/odds",
            params=params,
            headers={"x-api-key": api_key},
            timeout=config.TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise SourceError(f"Odds API: {type(exc).__name__}") from None


def fetch_odds(now: datetime, api_key: str | None) -> OddsResponse:
    """One featured-markets call (no retries: every call costs credits), or the fixture offline."""
    if config.OFFLINE_DIR is not None:
        return _fixture_response()
    if not api_key:
        raise SourceError("ODDS_API_KEY puuttuu")
    now = now.astimezone(UTC)
    params = {
        "bookmakers": ",".join(config.ODDS_BOOKMAKERS),
        "markets": ",".join(config.ODDS_MARKETS),
        "oddsFormat": "decimal",
        "dateFormat": "iso",
        "commenceTimeFrom": now.strftime(_TS_FORMAT),
        "commenceTimeTo": (now + timedelta(hours=config.ODDS_LOOKAHEAD_H)).strftime(_TS_FORMAT),
    }
    response = _request(params, api_key)
    fallback = False
    if response.status_code == 422:
        params = {k: v for k, v in params.items() if k != "bookmakers"}
        params["regions"] = config.ODDS_FALLBACK_REGIONS
        response = _request(params, api_key)
        fallback = True
    if not 200 <= response.status_code < 300:
        raise SourceError(f"Odds API HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise SourceError("Odds API: invalid JSON") from None
    if not isinstance(body, list):
        raise SourceError("Odds API: response is not a list")
    headers = response.headers
    return OddsResponse(
        body,
        _int_header(headers, "x-requests-remaining"),
        _int_header(headers, "x-requests-used"),
        _int_header(headers, "x-requests-last"),
        fallback,
    )


def _price(outcome) -> float | None:
    if not isinstance(outcome, dict):
        return None
    price = outcome.get("price")
    if isinstance(price, bool) or not isinstance(price, int | float):
        return None
    if not math.isfinite(price) or not price > 1.0:
        return None
    return float(price)


def _point(outcome: dict) -> float | None:
    point = outcome.get("point")
    if isinstance(point, bool) or not isinstance(point, int | float):
        return None
    return float(point)


def _parse_h2h(outcomes: list, home: str, away: str) -> tuple[str, list[float]] | None:
    names = [o.get("name") if isinstance(o, dict) else None for o in outcomes]
    prices = [_price(o) for o in outcomes]
    if None in prices:
        return None
    by_name = dict(zip(names, prices, strict=True))
    if len(by_name) != len(names):
        return None
    if set(by_name) == {home, away}:
        return "h2h", [by_name[home], by_name[away]]
    if set(by_name) == {home, away, "Draw"}:
        return "h2h3", [by_name[home], by_name["Draw"], by_name[away]]
    return None


def _group(outcomes: list, side_of) -> dict[float, dict[str, list[float | None]]]:
    """Group outcomes by point and side; a None price marks an invalid price."""
    groups: dict[float, dict[str, list[float | None]]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        point, side = _point(outcome), side_of(outcome)
        if point is None or side is None:
            continue
        groups.setdefault(point, {}).setdefault(side, []).append(_price(outcome))
    return groups


def _parse_totals(outcomes: list) -> list[list[float]]:
    def side_of(o: dict) -> str | None:
        return o.get("name") if o.get("name") in ("Over", "Under") else None

    result = []
    for point, sides in sorted(_group(outcomes, side_of).items()):
        over, under = sides.get("Over", []), sides.get("Under", [])
        if len(over) == 1 and len(under) == 1 and over[0] and under[0]:
            result.append([point, over[0], under[0]])
    return result


def _parse_spreads(outcomes: list, home: str, away: str) -> list[list[float]]:
    names = {home: "home", away: "away"}
    groups = _group(outcomes, lambda o: names.get(o.get("name")))
    result = []
    for point, sides in sorted(groups.items()):
        home_prices = sides.get("home", [])
        away_prices = groups.get(-point, {}).get("away", [])
        if len(home_prices) == 1 and len(away_prices) == 1 and home_prices[0] and away_prices[0]:
            result.append([point, home_prices[0], away_prices[0]])
    return result


def _parse_book(book: dict, home: str, away: str) -> BookMarkets | None:
    parsed: BookMarkets = {"h2h": None, "h2h3": None, "totals": [], "spreads": []}
    for market in book.get("markets") or []:
        if not isinstance(market, dict) or not isinstance(market.get("outcomes"), list):
            continue
        outcomes, key = market["outcomes"], market.get("key")
        if key == "h2h":
            result = _parse_h2h(outcomes, home, away)
            if result:
                parsed[result[0]] = result[1]
        elif key == "totals":
            parsed["totals"] = _parse_totals(outcomes)
        elif key == "spreads":
            parsed["spreads"] = _parse_spreads(outcomes, home, away)
    if parsed["h2h"] or parsed["h2h3"] or parsed["totals"] or parsed["spreads"]:
        return parsed
    return None


def parse_events(raw: list[dict]) -> list[ParsedEvent]:
    """Validate raw events into ParsedEvents. Invalid markets are skipped, never raised."""
    events: list[ParsedEvent] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        home, away = item.get("home_team"), item.get("away_team")
        commence, books = item.get("commence_time"), item.get("bookmakers")
        if not (home and away and commence and isinstance(books, list)):
            continue
        try:
            commence_utc = _utc(commence).strftime(_TS_FORMAT)
        except (TypeError, ValueError):
            continue
        parsed_books: dict[str, BookMarkets] = {}
        for book in books:
            if not isinstance(book, dict) or book.get("key") not in config.ODDS_BOOKMAKERS:
                continue
            parsed = _parse_book(book, home, away)
            if parsed:
                parsed_books[book["key"]] = parsed
        events.append({
            "home_name": home,
            "away_name": away,
            "home": teams.abbr_from_odds_name(home),
            "away": teams.abbr_from_odds_name(away),
            "commence_utc": commence_utc,
            "books": parsed_books,
        })
    return events


def match_events(events: list[ParsedEvent], games: list[ScheduledGame]) -> dict:
    """Attach events to NHL games by teams and start time; game ids become string keys."""
    tolerance = timedelta(hours=config.ODDS_MATCH_TOLERANCE_H)
    matched: dict[str, dict[str, BookMarkets]] = {}
    unknown: set[str] = set()
    unmatched: list[dict] = []
    for event in events:
        if event["home"] is None:
            unknown.add(event["home_name"])
        if event["away"] is None:
            unknown.add(event["away_name"])
        if event["home"] is None or event["away"] is None:
            continue
        commence = _utc(event["commence_utc"])
        game = next(
            (
                g for g in games
                if g["home"]["abbr"] == event["home"] and g["away"]["abbr"] == event["away"]
                and abs(commence - _utc(g["start_utc"])) <= tolerance
            ),
            None,
        )
        if game is None:
            unmatched.append({"home": event["home"], "away": event["away"],
                              "commence_utc": event["commence_utc"]})
        else:
            matched.setdefault(str(game["id"]), event["books"])
    return {"games": matched, "unknown_teams": sorted(unknown), "unmatched": unmatched}
