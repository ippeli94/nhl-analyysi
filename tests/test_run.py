import json
from datetime import datetime
from pathlib import Path

import pytest

from pipeline import cache, config, export, predict, run, tracking, xg
from pipeline.sources import injuries, nhl
from pipeline.sources.nhl import SourceError

FIXTURES = Path(__file__).parent / "fixtures"
NOW = "2026-11-17T15:17:00Z"
SEASON = "20262027"


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def seed_cache():
    for game_id in (2026020286, 2026020298):
        pbp = json.loads(
            (FIXTURES / f"gamecenter__{game_id}__play-by-play.json").read_text(encoding="utf-8")
        )
        box = json.loads(
            (FIXTURES / f"gamecenter__{game_id}__boxscore.json").read_text(encoding="utf-8")
        )
        cache.write(cache.build_record(pbp, box))


@pytest.fixture
def seeded(root):
    seed_cache()
    return root


def go(now=NOW, *extra):
    return run.main(["--offline", str(FIXTURES), "--now", now, *extra])


def latest_of(root):
    return json.loads((root / "site/data/latest.json").read_text(encoding="utf-8"))


def assert_nothing_durable(root):
    assert not (root / "site/data/latest.json").exists()
    assert not (root / "data/history").exists()
    assert not (root / "data/state").exists()
    assert not (root / "data/state/schedule_ids.json").exists()


def online_with_gate(root, monkeypatch):
    """Online path (gate passed) with a stubbed schedule and no model/xg_coefs.json."""
    (root / "model").mkdir()
    (root / "model/params.json").write_text('{"gate": {"passed": true}}', encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(config, "OFFLINE_DIR", FIXTURES)
        week, _ = nhl.fetch_schedule_week("2026-11-17")
    monkeypatch.setattr(run, "_fetch_week", lambda today: week)
    monkeypatch.setattr(cache, "cache_finished", lambda games: ([], []))


def game(game_id, state="FUT", date="2026-11-17", start="2026-11-18T00:00:00Z"):
    return {"id": game_id, "state": state, "nhl_date": date, "start_utc": start, "season": SEASON}


def test_offline_run_end_to_end(seeded):
    assert go() == 0
    latest = latest_of(seeded)
    assert export.validate(latest) == [] and len(latest["games"]) >= 1
    assert {s["id"]: s["status"] for s in latest["sources"]} == {
        "nhl": "ok", "injuries": "unavailable",
    }
    assert tracking.read_log("predictions", SEASON)
    assert (seeded / "data/state/last_games.json").exists()
    ids = json.loads((seeded / "data/state/schedule_ids.json").read_text(encoding="utf-8"))
    assert sorted(ids) == sorted(g["id"] for g in latest["games"])
    assert all(set(g["goalies"]) == {"home", "away"} for g in latest["games"])
    assert all("_internal" not in g for g in latest["games"])
    assert latest["next_game_date"] is None


def test_core_source_failure_publishes_nothing(root, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run.main(["--offline", str(empty), "--now", NOW]) == 1
    assert_nothing_durable(root)


def test_optional_source_failure_does_not_fail_run():
    state = {"injuries": {"status": "ok", "last_ok_at": "2026-11-16T15:17:00Z"}}

    def boom():
        raise SourceError("down")

    assert run.optional_source("injuries", boom, state, dt(NOW)) is None
    assert state["injuries"]["status"] == "failed"
    assert state["injuries"]["last_ok_at"] == "2026-11-16T15:17:00Z"
    assert state["injuries"]["fetched_at"] == NOW


def test_optional_source_success_and_unavailable_note():
    state = {}
    assert run.optional_source("x", lambda: [1], state, dt(NOW)) == [1]
    assert state["x"] == {"status": "ok", "fetched_at": NOW, "last_ok_at": NOW, "note": None}
    run.optional_source("y", lambda: 1 / 0, state, dt(NOW), "unavailable", "note")
    assert state["y"]["status"] == "unavailable" and state["y"]["note"] == "note"


def test_no_games_gives_empty_list_and_next_date(seeded):
    assert go("2026-11-12T15:17:00Z") == 0
    latest = latest_of(seeded)
    assert latest["games"] == [] and latest["next_game_date"] == "2026-11-15"
    assert export.validate(latest) == []


def test_started_game_is_frozen_and_not_logged(seeded):
    assert go() == 0
    first = {g["id"]: g for g in latest_of(seeded)["games"]}
    rows_before = tracking.read_log("predictions", SEASON)
    assert go("2026-11-18T00:10:00Z") == 0
    second = {g["id"]: g for g in latest_of(seeded)["games"]}
    started = second[2026020333]
    assert started["state"] == "started"
    assert started["prob"] == first[2026020333]["prob"]
    new_rows = tracking.read_log("predictions", SEASON)[len(rows_before):]
    assert 2026020333 not in [r["game_id"] for r in new_rows]
    assert any(r["game_id"] == 2026020333 for r in rows_before)
    assert second[2026020344]["state"] == "upcoming"


def test_started_game_without_previous_export_is_dropped(seeded):
    assert go("2026-11-18T00:10:00Z") == 0
    ids = {g["id"] for g in latest_of(seeded)["games"]}
    assert 2026020333 not in ids and 2026020344 in ids
    sched = json.loads((seeded / "data/state/schedule_ids.json").read_text(encoding="utf-8"))
    assert 2026020333 not in sched and set(sched) == ids


def test_online_run_refuses_without_gate(root, capsys):
    assert run.main([]) == 2
    assert "Backtest-ehto" in capsys.readouterr().out


def test_extra_run_skips_when_no_early_game(seeded, monkeypatch):
    out = seeded / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert go(NOW, "--extra-if-early") == 0
    assert "ran=false" in out.read_text(encoding="utf-8")
    assert not (seeded / "site/data/latest.json").exists()


def test_extra_run_runs_when_early_game(seeded, monkeypatch, tmp_path):
    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    for f in FIXTURES.glob("schedule__*.json"):
        text = f.read_text(encoding="utf-8").replace("2026-11-18T00:00:00Z", "2026-11-17T15:00:00Z")
        (fixtures / f.name).write_text(text, encoding="utf-8")
    out = seeded / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    argv = ["--offline", str(fixtures), "--now", "2026-11-17T14:00:00Z", "--extra-if-early"]
    assert run.main(argv) == 0
    assert "ran=true" in out.read_text(encoding="utf-8")
    assert (seeded / "site/data/latest.json").exists()


def test_nhl_today_uses_eastern_time():
    assert run.nhl_today(dt("2026-11-16T03:00:00Z")) == "2026-11-15"
    assert run.nhl_today(dt("2026-11-16T15:00:00Z")) == "2026-11-16"


def test_select_games_window():
    week = [
        game(1, date="2026-11-16"), game(2, date="2026-11-17"), game(3, date="2026-11-18"),
        game(4, date="2026-11-19"), game(5, "OFF", date="2026-11-17"),
    ]
    chosen, nxt = run.select_games(week, "2026-11-17")
    assert [g["id"] for g in chosen] == [2, 3] and nxt is None
    chosen, nxt = run.select_games(week, "2026-11-12")
    assert chosen == [] and nxt == "2026-11-16"
    assert run.select_games([game(9, "OFF")], "2026-11-17") == ([], None)


def test_needs_extra_run():
    now = dt("2026-11-21T13:00:00Z")
    early = game(1, start="2026-11-21T14:30:00Z")
    late = game(2, start="2026-11-21T16:00:00Z")  # after the 15:17 UTC scheduled run
    assert run.needs_extra_run([early, late], now)
    assert not run.needs_extra_run([late], now)
    assert not run.needs_extra_run([game(3, "OFF", start="2026-11-21T14:30:00Z")], now)


def test_results_recorded_for_newly_cached_games(root):
    # the cache is empty, so the run writes the two fixture games and logs their results
    assert go() == 0
    rows = tracking.read_log("results", SEASON)
    assert {r["game_id"] for r in rows} == {2026020286, 2026020298}
    assert {r["decided"] for r in rows} == {"REG", "SO"}


def test_injuries_fetch_validates(monkeypatch):
    class Resp:
        ok = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    monkeypatch.setattr(injuries._session, "get", lambda *a, **k: Resp({"injuries": [{"a": 1}]}))
    assert injuries.fetch() == [{"a": 1}]
    monkeypatch.setattr(injuries._session, "get", lambda *a, **k: Resp({"x": 1}))
    with pytest.raises(SourceError):
        injuries.fetch()


def test_stub_coefficients_shape():
    stub = json.loads((FIXTURES / "xg_coefs_stub.json").read_text(encoding="utf-8"))
    assert len(stub["coef"]) == 16 and stub["features"] == xg.FEATURES
    assert stub["coef"][xg.FEATURES.index("dist")] < 0


def test_model_failure_publishes_nothing_durable(seeded, monkeypatch):
    online_with_gate(seeded, monkeypatch)
    assert run.main(["--now", NOW]) == 1  # model/xg_coefs.json is missing
    assert_nothing_durable(seeded)


def test_unexpected_exception_publishes_nothing_durable(seeded, monkeypatch):
    def boom(*args, **kwargs):
        raise ZeroDivisionError("bug")

    monkeypatch.setattr(predict, "predict_game", boom)
    assert go() == 1
    assert_nothing_durable(seeded)


def test_cached_game_without_result_row_is_recorded_next_run(seeded):
    assert tracking.read_log("results", SEASON) == []
    assert go() == 0
    assert {r["game_id"] for r in tracking.read_log("results", SEASON)} == {
        2026020286, 2026020298,
    }
    assert go() == 0  # idempotent
    assert len(tracking.read_log("results", SEASON)) == 2
