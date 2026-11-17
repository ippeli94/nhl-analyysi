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
