import json
from pathlib import Path

import pytest
import requests

from pipeline import config
from pipeline.sources import nhl

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_DATE = "2026-11-10"
REG_GAME_ID = 2026020286
SO_GAME_ID = 2026020298


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE_DIR", FIXTURES)


class _Response:
    def __init__(self, status_code: int, body: str = "{}"):
        self.status_code = status_code
        self._body = body

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return json.loads(self._body)


def test_fixture_name():
    assert nhl.fixture_name("/gamecenter/1/play-by-play") == "gamecenter__1__play-by-play.json"


def test_schedule_week_parses_games(offline):
    games, nxt = nhl.fetch_schedule_week(FIXTURE_DATE)
    g = games[0]
    assert set(g) == {"id", "season", "game_type", "nhl_date", "start_utc", "state", "home", "away"}
    assert (
        g["start_utc"].endswith("Z")
        and len(g["home"]["abbr"]) == 3
        and g["home"]["logo"].startswith("https://")
    )
    assert set(g["home"]) == {"id", "abbr", "name", "logo", "logo_dark"}
    assert isinstance(g["season"], str) and len(g["season"]) == 8 and nxt


def test_require_names_missing_path():
    with pytest.raises(nhl.SourceError, match="a.b"):
        nhl.require({"a": {}}, ["a.b"])


def test_require_passes_when_all_present():
    assert nhl.require({"a": {"b": None}}, ["a.b"]) is None


def test_pbp_validated(offline):
    assert nhl.fetch_pbp(REG_GAME_ID)["plays"]


def test_boxscore_validated(offline):
    box = nhl.fetch_boxscore(SO_GAME_ID)
    assert box["playerByGameStats"]["homeTeam"]["goalies"]


def test_missing_offline_file_is_source_error(offline):
    with pytest.raises(nhl.SourceError):
        nhl.fetch_pbp(1)


def test_offline_payload_missing_required_field_is_source_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OFFLINE_DIR", tmp_path)
    payload = json.loads(
        (FIXTURES / nhl.fixture_name(f"/gamecenter/{REG_GAME_ID}/play-by-play")).read_text("utf-8")
    )
    del payload["plays"]
    (tmp_path / nhl.fixture_name(f"/gamecenter/{REG_GAME_ID}/play-by-play")).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(nhl.SourceError, match="plays"):
        nhl.fetch_pbp(REG_GAME_ID)


def test_throttle_spaces_requests(monkeypatch):
    now = [100.0]
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(nhl.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(nhl.time, "sleep", fake_sleep)
    monkeypatch.setattr(nhl, "_last_request", None)
    nhl._throttle()
    nhl._throttle()
    assert sleeps == [pytest.approx(1 / config.REQUESTS_PER_S)]


def test_online_retries_then_raises(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE_DIR", None)
    monkeypatch.setattr(nhl, "_throttle", lambda: None)
    calls = []
    sleeps = []

    def fake_request(self, method, url, **kwargs):
        calls.append(url)
        return _Response(500)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(nhl.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(nhl.SourceError):
        nhl.get_json("/schedule/now")
    assert len(calls) == 4  # 1 try + 3 retries
    assert sleeps == [1, 3, 9]


def test_online_404_raises_without_retry(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE_DIR", None)
    calls = []

    def fake_request(self, method, url, **kwargs):
        calls.append(url)
        return _Response(404)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(nhl, "_throttle", lambda: None)
    monkeypatch.setattr(nhl.time, "sleep", lambda s: None)
    with pytest.raises(nhl.SourceError):
        nhl.get_json("/gamecenter/1/play-by-play")
    assert len(calls) == 1


def test_online_non_json_is_source_error(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE_DIR", None)

    def fake_request(self, method, url, **kwargs):
        return _Response(200, body="<html>maintenance</html>")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(nhl, "_throttle", lambda: None)
    monkeypatch.setattr(nhl.time, "sleep", lambda s: None)
    with pytest.raises(nhl.SourceError):
        nhl.get_json("/schedule/now")


def _schedule_with(tmp_path, monkeypatch, mutate):
    payload = json.loads(
        (FIXTURES / nhl.fixture_name(f"/schedule/{FIXTURE_DATE}")).read_text("utf-8")
    )
    mutate(payload["gameWeek"][0]["games"])
    monkeypatch.setattr(config, "OFFLINE_DIR", tmp_path)
    (tmp_path / nhl.fixture_name(f"/schedule/{FIXTURE_DATE}")).write_text(
        json.dumps(payload), "utf-8"
    )
    return nhl.fetch_schedule_week(FIXTURE_DATE)


def test_non_regular_game_with_missing_fields_is_skipped(tmp_path, monkeypatch):
    def mutate(games):
        games[0]["gameType"] = 4  # All-Star
        del games[0]["homeTeam"]["abbrev"]

    original = json.loads(
        (FIXTURES / nhl.fixture_name(f"/schedule/{FIXTURE_DATE}")).read_text("utf-8")
    )
    total = sum(len(day["games"]) for day in original["gameWeek"])
    skipped_id = original["gameWeek"][0]["games"][0]["id"]

    games, _ = _schedule_with(tmp_path, monkeypatch, mutate)
    assert skipped_id not in [g["id"] for g in games]
    assert len(games) == total - 1


def test_regular_game_missing_dark_logo_gets_none(tmp_path, monkeypatch):
    def mutate(games):
        del games[0]["homeTeam"]["darkLogo"]

    games, _ = _schedule_with(tmp_path, monkeypatch, mutate)
    assert games[0]["home"]["logo_dark"] is None
    assert games[0]["home"]["logo"].startswith("https://")


def test_regular_game_missing_core_field_is_source_error(tmp_path, monkeypatch):
    def mutate(games):
        del games[0]["homeTeam"]["abbrev"]

    with pytest.raises(nhl.SourceError, match="abbrev"):
        _schedule_with(tmp_path, monkeypatch, mutate)


def test_name_falls_back_to_abbreviation(tmp_path, monkeypatch):
    def mutate(games):
        del games[0]["awayTeam"]["placeName"]
        del games[0]["awayTeam"]["commonName"]

    games, _ = _schedule_with(tmp_path, monkeypatch, mutate)
    assert games[0]["away"]["name"] == games[0]["away"]["abbr"]
