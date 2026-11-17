import json
import logging
from pathlib import Path

import pytest

from pipeline import backfill, cache, config
from pipeline.sources import nhl

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REG_GAME_ID = 2026020286  # COL 6 at TOR 3
SO_GAME_ID = 2026020298  # PIT 3 at SJS 2, decided in the shootout
MISSING_GAME_ID = 2026029999  # not in the fixtures
SEASON = "20262027"
KNOWN_SCORE = {"home": 3, "away": 6}


def load(kind: str, game_id: int) -> dict:
    name = f"gamecenter__{game_id}__{'play-by-play' if kind == 'pbp' else kind}.json"
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def scheduled(game_id: int, state: str = "OFF", game_type: int = 2) -> nhl.ScheduledGame:
    def team(abbr: str, team_id: int) -> nhl.Team:
        return {"id": team_id, "abbr": abbr, "name": abbr, "logo": "", "logo_dark": None}

    return {
        "id": game_id, "season": SEASON, "game_type": game_type, "nhl_date": "2026-11-10",
        "start_utc": "2026-11-11T00:00:00Z", "state": state,
        "home": team("HOME", 1), "away": team("AWAY", 2),
    }


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE_DIR", FIXTURES)


@pytest.fixture
def reg_record():
    return cache.build_record(load("pbp", REG_GAME_ID), load("boxscore", REG_GAME_ID))


def test_build_record_shape():
    r = cache.build_record(load("pbp", REG_GAME_ID), load("boxscore", REG_GAME_ID))
    assert r["v"] == 1 and r["decided"] == "REG"
    assert r["score"] == r["score_reg"] == KNOWN_SCORE
    assert r["season"] == SEASON and r["game_type"] == 2 and r["date"] == "2026-11-10"
    assert r["home"] == {"id": 10, "abbr": "TOR"} and r["away"] == {"id": 21, "abbr": "COL"}
    assert len(r["skaters"]) >= 36 and sum(g["started"] for g in r["goalies"]) == 2
    assert all(isinstance(s["toi"], int) for s in r["skaters"])
    assert r["shots"] and r["penalties"] is not None


def test_skater_and_goalie_fields_are_mapped():
    r = cache.build_record(load("pbp", REG_GAME_ID), load("boxscore", REG_GAME_ID))
    first = r["skaters"][0]  # home forward "Z. MacEwen", "09:13" on ice
    assert first["name"] == "Z. MacEwen" and first["team"] == "TOR" and first["pos"] == "R"
    assert first["toi"] == 9 * 60 + 13
    assert len(r["goalies"]) == 4  # two dressed goalies per team, starter flag per FIELDS.md
    assert all(isinstance(g["toi"], int) for g in r["goalies"])


def test_shootout_game_reg_score_is_tie():
    r = cache.build_record(load("pbp", SO_GAME_ID), load("boxscore", SO_GAME_ID))
    assert r["decided"] == "SO"
    assert r["score"] == {"home": 2, "away": 3}  # SJS 2, PIT 3 (shootout winner)
    assert r["score_reg"] == {"home": 2, "away": 2}


def test_build_record_rejects_empty_plays():
    with pytest.raises(ValueError):
        cache.build_record({**load("pbp", REG_GAME_ID), "plays": []}, load("boxscore", REG_GAME_ID))


def test_write_read_roundtrip_under_20kb(root, reg_record):
    p = cache.write(reg_record)
    assert p == root / "data" / "cache" / "games" / SEASON / f"{REG_GAME_ID}.json.gz"
    assert cache.read(p) == reg_record
    assert p.stat().st_size < 20_000


def test_write_never_overwrites(root, reg_record):
    p = cache.write(reg_record)
    changed = {**reg_record, "decided": "OT"}
    assert cache.write(changed) == p
    assert cache.read(p) == reg_record


def test_cached_ids_and_load_seasons_sorted(root, reg_record):
    later = {**reg_record, "id": 2026020301, "date": "2026-11-20"}
    cache.write(later)
    cache.write(reg_record)
    assert cache.cached_ids(SEASON) == {REG_GAME_ID, 2026020301}
    assert cache.cached_ids("20252026") == set()
    assert [r["id"] for r in cache.load_seasons([SEASON])] == [REG_GAME_ID, 2026020301]


def test_cache_finished_skips_cached_and_unfinished(root, offline, reg_record):
    cache.write(reg_record)
    written, skipped = cache.cache_finished([
        scheduled(REG_GAME_ID),  # finished and already cached
        scheduled(SO_GAME_ID, state="FUT"),  # not finished yet
        scheduled(SO_GAME_ID, state="OFF", game_type=1),  # exhibition, not a cached type
    ])
    assert (written, skipped) == ([], [])
    assert cache.cached_ids(SEASON) == {REG_GAME_ID}


def test_cache_finished_writes_new_finished_games(root, offline):
    written, skipped = cache.cache_finished([scheduled(REG_GAME_ID), scheduled(SO_GAME_ID)])
    assert written == [REG_GAME_ID, SO_GAME_ID] and skipped == []
    assert cache.cached_ids(SEASON) == {REG_GAME_ID, SO_GAME_ID}


def test_cache_finished_skips_game_with_missing_pbp(root, offline, caplog):
    with caplog.at_level(logging.WARNING):
        written, skipped = cache.cache_finished([
            scheduled(MISSING_GAME_ID), scheduled(REG_GAME_ID),
        ])
    assert written == [REG_GAME_ID] and skipped == [MISSING_GAME_ID]
    assert any(str(MISSING_GAME_ID) in rec.getMessage() for rec in caplog.records)


def test_season_dates():
    assert backfill.season_dates("20252026") == ("2025-09-25", "2026-06-30")


def test_backfill_is_resumable(root, offline, monkeypatch):
    week = ([scheduled(REG_GAME_ID), scheduled(SO_GAME_ID)], None)
    monkeypatch.setattr(nhl, "fetch_schedule_week", lambda date: week)

    first = backfill.run([SEASON])
    assert first == {"written": 2, "skipped": 0, "already": 0}

    second = backfill.run([SEASON])
    assert second == {"written": 0, "skipped": 0, "already": 2}


EARLY_MISSING_ID = 2026000001  # sorts before the fixture games


def stub_weeks(monkeypatch, weeks):
    """Schedule stub: weeks maps a start date to (games, nextStartDate). Records the calls."""
    calls: list[str] = []

    def fake(date):
        calls.append(date)
        return weeks[date]

    monkeypatch.setattr(nhl, "fetch_schedule_week", fake)
    return calls


def test_walk_never_fetches_week_past_season_end(root, offline, monkeypatch):
    calls = stub_weeks(monkeypatch, {
        "2026-09-25": ([], "2026-10-02"),
        "2026-10-02": ([], "2027-06-25"),
        "2027-06-25": ([], "2027-07-02"),  # next week starts after 2027-06-30
        "2027-07-02": ([], None),
    })
    assert backfill.run([SEASON]) == {"written": 0, "skipped": 0, "already": 0}
    assert calls == ["2026-09-25", "2026-10-02", "2027-06-25"]


@pytest.mark.parametrize("weeks", [
    {"2026-09-25": ([], "2026-10-02"), "2026-10-02": ([], "2026-09-25")},  # two-week cycle
    {"2026-09-25": ([], "2026-09-25")},  # week points at itself
])
def test_repeating_next_start_date_terminates(root, offline, monkeypatch, weeks):
    calls = stub_weeks(monkeypatch, weeks)
    backfill.run([SEASON])
    assert len(calls) <= 2


def test_overlapping_weeks_are_deduplicated(root, offline, monkeypatch):
    fetched: list[int] = []
    real_fetch = nhl.fetch_pbp

    def counting(game_id):
        fetched.append(game_id)
        return real_fetch(game_id)

    monkeypatch.setattr(nhl, "fetch_pbp", counting)
    stub_weeks(monkeypatch, {
        "2026-09-25": ([scheduled(REG_GAME_ID), scheduled(SO_GAME_ID)], "2026-10-02"),
        "2026-10-02": ([scheduled(REG_GAME_ID)], None),
    })
    assert backfill.run([SEASON]) == {"written": 2, "skipped": 0, "already": 0}
    assert fetched == [REG_GAME_ID, SO_GAME_ID]


def test_limit_counts_written_games_not_attempts(root, offline, monkeypatch):
    stub_weeks(monkeypatch, {
        "2026-09-25": ([scheduled(EARLY_MISSING_ID), scheduled(REG_GAME_ID),
                        scheduled(SO_GAME_ID)], None),
    })
    first = backfill.run([SEASON], limit=1)
    assert first == {"written": 1, "skipped": 1, "already": 0}
    assert cache.cached_ids(SEASON) == {REG_GAME_ID}

    second = backfill.run([SEASON])
    assert second == {"written": 1, "skipped": 1, "already": 1}
    assert cache.cached_ids(SEASON) == {REG_GAME_ID, SO_GAME_ID}


def test_main_exit_code_depends_on_skipped_count(root, monkeypatch, capsys):
    monkeypatch.setattr(backfill, "run", lambda seasons, limit=None: {
        "written": 0, "skipped": 11, "already": 0})
    assert backfill.main(["--seasons", SEASON]) == 1
    assert '"skipped": 11' in capsys.readouterr().out

    monkeypatch.setattr(backfill, "run", lambda seasons, limit=None: {
        "written": 0, "skipped": 10, "already": 0})
    assert backfill.main(["--seasons", SEASON]) == 0


def test_main_exits_1_on_source_error(root, monkeypatch):
    def outage(date):
        raise nhl.SourceError("HTTP 503")

    monkeypatch.setattr(nhl, "fetch_schedule_week", outage)
    assert backfill.main(["--seasons", SEASON]) == 1


def test_five_consecutive_outages_raise(root, monkeypatch):
    attempts: list[int] = []

    def outage(game_id):
        attempts.append(game_id)
        raise nhl.SourceError("HTTP 503")

    monkeypatch.setattr(nhl, "fetch_pbp", outage)
    games = [scheduled(EARLY_MISSING_ID + i) for i in range(7)]
    with pytest.raises(nhl.SourceError):
        cache.cache_finished(games)
    assert len(attempts) == 5


def test_success_resets_outage_count(root, offline, monkeypatch):
    real_fetch = nhl.fetch_pbp

    def flaky(game_id):
        if game_id == REG_GAME_ID:
            return real_fetch(game_id)
        raise nhl.SourceError("HTTP 503")

    monkeypatch.setattr(nhl, "fetch_pbp", flaky)
    games = [scheduled(EARLY_MISSING_ID + i) for i in range(4)]
    games += [scheduled(REG_GAME_ID)]
    games += [scheduled(EARLY_MISSING_ID + 10 + i) for i in range(4)]
    written, skipped = cache.cache_finished(games)
    assert written == [REG_GAME_ID] and len(skipped) == 8


def test_not_found_games_are_skipped_without_outage_count(root, monkeypatch):
    def not_found(game_id):
        raise nhl.NotFoundError(f"not found: {game_id}")

    monkeypatch.setattr(nhl, "fetch_pbp", not_found)
    games = [scheduled(EARLY_MISSING_ID + i) for i in range(6)]
    written, skipped = cache.cache_finished(games)
    assert written == [] and len(skipped) == 6


def test_unexpected_error_skips_one_game_and_continues(root, offline, monkeypatch, caplog):
    real_build = cache.build_record

    def broken_for_reg(pbp, box):
        if pbp["id"] == REG_GAME_ID:
            raise AttributeError("odd payload")
        return real_build(pbp, box)

    monkeypatch.setattr(cache, "build_record", broken_for_reg)
    with caplog.at_level(logging.ERROR):
        written, skipped = cache.cache_finished([scheduled(REG_GAME_ID), scheduled(SO_GAME_ID)])
    assert written == [SO_GAME_ID] and skipped == [REG_GAME_ID]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and str(REG_GAME_ID) in errors[0].getMessage() and errors[0].exc_info


def test_write_twice_gives_identical_bytes(root, reg_record):
    p = cache.write(reg_record)
    first = p.read_bytes()
    p.unlink()
    cache.write(reg_record)
    assert p.read_bytes() == first
