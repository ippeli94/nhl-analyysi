import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from pipeline import config, teams
from pipeline.sources import nhl, odds
from pipeline.sources.nhl import SourceError

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 17, 21, 37, tzinfo=UTC)
KEY = "k" * 32


def load_fixture() -> list[dict]:
    return json.loads((FIXTURES / config.ODDS_FIXTURE).read_text(encoding="utf-8"))


def schedule_games(monkeypatch) -> list:
    monkeypatch.setattr(config, "OFFLINE_DIR", FIXTURES)
    games, _ = nhl.fetch_schedule_week("2026-11-17")
    return games


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = [] if body is None else body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Call:
    def __init__(self, method, url, kwargs):
        self.method, self.url = method, url
        self.params = kwargs.get("params", {})
        self.headers = kwargs.get("headers", {})


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[Call] = []

    def install(self, monkeypatch):
        def request(session, method, url, **kwargs):
            self.calls.append(Call(method, url, kwargs))
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(requests.Session, "request", request)
        return self


USAGE = {"x-requests-remaining": "312", "x-requests-used": "188", "x-requests-last": "3"}


def test_abbr_from_odds_name_variants():
    names = ("St Louis Blues", "St. Louis Blues", "Montreal Canadiens",
             "Montréal Canadiens", "Utah Hockey Club", "Utah Mammoth")
    expected = ["STL", "STL", "MTL", "MTL", "UTA", "UTA"]
    assert [teams.abbr_from_odds_name(n) for n in names] == expected
    assert teams.abbr_from_odds_name("Arizona Coyotes") is None


def test_every_nhl_team_maps_from_its_display_name():
    assert len(teams.TEAM_NAMES) == 32
    for abbr, name in teams.TEAM_NAMES.items():
        assert teams.abbr_from_odds_name(name) == abbr


def test_offline_fetch_reads_fixture(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE_DIR", FIXTURES)
    result = odds.fetch_odds(NOW, None)
    assert len(result.events) == 4
    assert (result.remaining, result.used, result.last) == (None, None, None)
    assert result.fallback is False


def test_offline_fetch_missing_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OFFLINE_DIR", tmp_path)
    with pytest.raises(SourceError, match="odds fixture missing"):
        odds.fetch_odds(NOW, KEY)


def test_online_request_uses_header_not_query(monkeypatch):
    recorder = Recorder([FakeResponse(200, load_fixture(), USAGE)]).install(monkeypatch)
    result = odds.fetch_odds(NOW, KEY)
    call = recorder.calls[0]
    assert call.method == "GET" and call.url.endswith("/sports/icehockey_nhl/odds")
    assert call.headers["x-api-key"] == KEY and KEY not in call.url + str(call.params)
    assert call.params["bookmakers"].count(",") == 9
    assert call.params["markets"] == "h2h,totals,spreads"
    assert call.params["oddsFormat"] == "decimal" and call.params["dateFormat"] == "iso"
    assert call.params["commenceTimeFrom"] == "2026-11-17T21:37:00Z"
    assert call.params["commenceTimeTo"] == "2026-11-19T09:37:00Z"
    assert (result.remaining, result.used, result.last) == (312, 188, 3)
    assert result.fallback is False and len(result.events) == 4


def test_non_numeric_usage_headers_are_none(monkeypatch):
    Recorder([FakeResponse(200, [], {"x-requests-remaining": "abc"})]).install(monkeypatch)
    result = odds.fetch_odds(NOW, KEY)
    assert (result.remaining, result.used, result.last) == (None, None, None)


def test_422_falls_back_to_regions(monkeypatch):
    recorder = Recorder([FakeResponse(422), FakeResponse(200, [], USAGE)]).install(monkeypatch)
    result = odds.fetch_odds(NOW, KEY)
    assert len(recorder.calls) == 2
    second = recorder.calls[1].params
    assert second["regions"] == "eu,fi" and "bookmakers" not in second
    assert result.fallback is True and result.events == []


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_error_is_source_error_without_key(monkeypatch, status):
    recorder = Recorder([FakeResponse(status)]).install(monkeypatch)
    with pytest.raises(SourceError) as e:
        odds.fetch_odds(NOW, KEY)
    assert str(status) in str(e.value) and KEY not in str(e.value) and len(recorder.calls) == 1


def test_connection_error_message_has_no_key(monkeypatch):
    Recorder([requests.ConnectionError(f"failed for {KEY}")]).install(monkeypatch)
    with pytest.raises(SourceError) as e:
        odds.fetch_odds(NOW, KEY)
    assert "ConnectionError" in str(e.value) and KEY not in str(e.value)


def test_non_list_body_is_source_error(monkeypatch):
    Recorder([FakeResponse(200, {"message": "x"})]).install(monkeypatch)
    with pytest.raises(SourceError):
        odds.fetch_odds(NOW, KEY)


def test_invalid_json_is_source_error(monkeypatch):
    Recorder([FakeResponse(200, ValueError("bad"))]).install(monkeypatch)
    with pytest.raises(SourceError):
        odds.fetch_odds(NOW, KEY)


@pytest.mark.parametrize("key", [None, ""])
def test_missing_key_is_source_error(monkeypatch, key):
    monkeypatch.setattr(config, "OFFLINE_DIR", None)
    with pytest.raises(SourceError, match="ODDS_API_KEY puuttuu"):
        odds.fetch_odds(NOW, key)


def test_parse_fixture_shapes():
    ev = odds.parse_events(load_fixture())
    assert len(ev) == 4
    lak = next(e for e in ev if e["home"] == "BOS" and e["away"] == "LAK")
    assert lak["books"]["pinnacle"]["h2h"]
    assert [6.0, 6.5] == [t[0] for t in lak["books"]["pinnacle"]["totals"]]
    assert lak["books"]["pinnacle"]["spreads"][0][0] == -1.5
    assert set(lak["books"]) == {"pinnacle", "veikkaus_fi", "coolbet", "betfair_ex_eu"}
    fla = next(e for e in ev if e["home"] == "FLA")
    assert fla["books"]["marathonbet"]["h2h3"] and fla["books"]["marathonbet"]["h2h"] is None
    assert next(e for e in ev if e["home"] == "NJD")["away"] == "MTL"


def test_fixture_has_value_price_on_bos_at_det():
    ev = next(e for e in odds.parse_events(load_fixture()) if e["home"] == "DET")
    home, away = ev["books"]["pinnacle"]["h2h"]
    fair_away = (1 / away) / (1 / home + 1 / away)
    assert ev["books"]["coolbet"]["h2h"][1] >= 1.08 / fair_away


def event_with(markets: list[dict], book="pinnacle") -> list[dict]:
    return [{
        "id": "x", "commence_time": "2026-11-18T00:00:00Z",
        "home_team": "Boston Bruins", "away_team": "Los Angeles Kings",
        "bookmakers": [{"key": book, "markets": markets}],
    }]


def market(key, outcomes):
    return {"key": key, "outcomes": outcomes}


H, A = "Boston Bruins", "Los Angeles Kings"


def books_of(markets, book="pinnacle"):
    return odds.parse_events(event_with(markets, book))[0]["books"]


def test_malformed_markets_are_skipped():
    markets = [
        market("h2h", [{"name": H, "price": 1.8}]),  # one side missing
        market("totals", [
            # price 1.0
            {"name": "Over", "price": 1.0, "point": 5.5},
            {"name": "Under", "price": 2.0, "point": 5.5},
            # points differ
            {"name": "Over", "price": 1.9, "point": 6.5},
            {"name": "Under", "price": 1.9, "point": 6.0},
            # valid
            {"name": "Over", "price": 1.9, "point": 7.5},
            {"name": "Under", "price": 1.9, "point": 7.5},
        ]),
        market("spreads", [
            # duplicate home outcome
            {"name": H, "price": 2.5, "point": -1.5},
            {"name": H, "price": 2.6, "point": -1.5},
            {"name": A, "price": 1.5, "point": 1.5},
        ]),
    ]
    book = books_of(markets)["pinnacle"]
    assert book["h2h"] is None and book["h2h3"] is None
    assert book["totals"] == [[7.5, 1.9, 1.9]]
    assert book["spreads"] == []


def test_book_without_valid_market_is_omitted():
    assert books_of([market("h2h", [{"name": H, "price": 1.0}, {"name": A, "price": 2.0}])]) == {}


def test_h2h_with_foreign_name_is_skipped():
    markets = [market("h2h", [{"name": H, "price": 1.8}, {"name": "Draw", "price": 4.0}])]
    assert books_of(markets) == {}


def test_spreads_pair_home_and_away_points():
    markets = [market("spreads", [
        {"name": A, "price": 1.5, "point": 1.5}, {"name": H, "price": 2.5, "point": -1.5},
        {"name": H, "price": 1.4, "point": 1.5}, {"name": A, "price": 2.9, "point": -1.5},
    ])]
    assert books_of(markets)["pinnacle"]["spreads"] == [[-1.5, 2.5, 1.5], [1.5, 1.4, 2.9]]


def test_unknown_book_is_ignored():
    markets = [market("h2h", [{"name": H, "price": 1.8}, {"name": A, "price": 2.1}])]
    assert books_of(markets, book="bet365") == {}
    assert "pinnacle" in books_of(markets)


def test_events_missing_required_fields_are_dropped():
    raw = event_with([market("h2h", [{"name": H, "price": 1.8}, {"name": A, "price": 2.1}])])
    for field in ("home_team", "away_team", "commence_time", "bookmakers"):
        broken = copy.deepcopy(raw)
        del broken[0][field]
        assert odds.parse_events(broken) == []


def test_match_events_by_teams_and_time(monkeypatch):
    matched = odds.match_events(odds.parse_events(load_fixture()), schedule_games(monkeypatch))
    assert sorted(matched["games"]) == ["2026020333", "2026020335", "2026020344", "2026020345"]
    assert matched["unknown_teams"] == [] and matched["unmatched"] == []
    assert "pinnacle" in matched["games"]["2026020333"]
    assert json.loads(json.dumps(matched)) == matched


def test_unknown_team_is_reported(monkeypatch):
    raw = load_fixture()
    raw[0]["home_team"] = "Utah Hockey Kings"
    matched = odds.match_events(odds.parse_events(raw), schedule_games(monkeypatch))
    assert matched["unknown_teams"] == ["Utah Hockey Kings"]
    assert "2026020333" not in matched["games"] and len(matched["games"]) == 3


def test_mapped_event_without_game_is_unmatched(monkeypatch):
    raw = load_fixture()
    raw[0]["commence_time"] = "2026-11-18T10:00:00Z"
    matched = odds.match_events(odds.parse_events(raw), schedule_games(monkeypatch))
    expected = {"home": "BOS", "away": "LAK", "commence_utc": "2026-11-18T10:00:00Z"}
    assert matched["unmatched"] == [expected]
    assert "2026020333" not in matched["games"]


def test_parsed_events_hold_no_event_id():
    raw = load_fixture()
    for n, event in enumerate(raw):
        event["id"] = f"{n:x}".ljust(32, "a")
    assert re.search(r"[0-9a-f]{32}", json.dumps(raw))
    assert not re.search(r"[0-9a-f]{32}", json.dumps(odds.parse_events(raw)))


def test_fixture_files_contain_no_key_shaped_string():
    for path in FIXTURES.glob("odds__*.json"):
        assert not re.search(r"\b[0-9a-f]{32}\b", path.read_text(encoding="utf-8"))


LIVE = FIXTURES / "odds__icehockey_nhl__live.json"


@pytest.mark.skipif(not LIVE.exists(), reason="no live odds fixture recorded")
def test_live_fixture_parses_if_present():
    raw = json.loads(LIVE.read_text(encoding="utf-8"))
    events = odds.parse_events(raw)
    assert len(events) == len(raw)
    for event in events:
        assert event["home"] and event["away"], (event["home_name"], event["away_name"])
        assert event["books"]
