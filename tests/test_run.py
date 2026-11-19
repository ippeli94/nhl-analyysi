import json
from datetime import datetime
from pathlib import Path

import pytest

from pipeline import cache, config, export, predict, run, tracking, xg
from pipeline.sources import injuries, nhl, odds
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
    assert all(isinstance(g["insights"], list) for g in latest["games"])
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


def sources_of(root):
    return {s["id"]: s for s in latest_of(root)["sources"]}


def odds_history(root):
    return tracking.read_log("odds", SEASON)


def test_offline_odds_run_fills_odds(seeded):
    assert go(NOW, "--odds") == 0
    lat = latest_of(seeded)
    games = {g["id"]: g for g in lat["games"]}
    assert games[2026020333]["odds"]["markets"][0]["key"] == "h2h"
    assert len([g for g in lat["games"] if g["odds"]]) == 4
    assert any(g["odds"] and g["odds"]["picks"] for g in lat["games"])
    assert sources_of(seeded)["odds"]["status"] == "ok"
    assert export.validate(lat) == [] and odds_history(seeded)
    assert (seeded / "data/state/odds.json").exists()
    row = tracking.read_log("predictions", SEASON)[-1]
    assert "market" in row and "picks" in row


def test_run_without_odds_flag_reuses_fresh_snapshot(seeded):
    assert go(NOW, "--odds") == 0
    first = latest_of(seeded)
    rows = len(odds_history(seeded))
    assert go("2026-11-17T21:37:00Z") == 0
    second = latest_of(seeded)
    fetched = {g["odds"]["fetched_at"] for g in second["games"] if g["odds"]}
    assert fetched == {NOW}
    assert len(odds_history(seeded)) == rows
    assert second["games"][0]["odds"]["markets"] == first["games"][0]["odds"]["markets"]


def test_snapshot_older_than_36h_is_not_used(seeded):
    assert go(NOW, "--odds") == 0
    f = seeded / "data/state/odds.json"
    snap = json.loads(f.read_text(encoding="utf-8"))
    snap["fetched_at"] = "2026-11-16T02:17:00Z"  # 37 h before NOW
    f.write_text(json.dumps(snap), encoding="utf-8")
    assert go() == 0
    assert all(g["odds"] is None for g in latest_of(seeded)["games"])


def test_odds_failure_does_not_fail_run(seeded, monkeypatch):
    assert go(NOW, "--odds") == 0

    def boom(now, key):
        raise SourceError("Odds API HTTP 401")

    monkeypatch.setattr(odds, "fetch_odds", boom)
    assert go("2026-11-17T21:37:00Z", "--odds") == 0
    lat = latest_of(seeded)
    src = {s["id"]: s for s in lat["sources"]}["odds"]
    assert src["status"] == "failed" and "401" in src["note"]
    assert any(g["odds"] for g in lat["games"])  # previous snapshot, still fresh


def test_malformed_payload_marks_failed_without_key(seeded, monkeypatch):
    monkeypatch.setenv(config.ODDS_ENV_KEY, "secret-key-value")
    monkeypatch.setattr(
        odds, "fetch_odds", lambda now, key: odds.OddsResponse([1], 400, 100, 3, False)
    )
    monkeypatch.setattr(odds, "parse_events", lambda raw: raw["x"])  # TypeError
    assert go(NOW, "--odds") == 0
    src = sources_of(seeded)["odds"]
    assert src["status"] == "failed" and src["note"] == "TypeError"
    assert not (seeded / "data/state/odds.json").exists()
    assert "secret-key-value" not in json.dumps(latest_of(seeded))


def test_skip_when_credits_low(seeded, monkeypatch):
    state = seeded / "data/state"
    state.mkdir(parents=True)
    (state / "sources.json").write_text(json.dumps({"odds": {
        "status": "ok", "fetched_at": "2026-11-17T09:00:00Z", "last_ok_at": "2026-11-17T09:00:00Z",
        "note": None, "remaining": 25, "used": 475,
    }}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(odds, "fetch_odds", lambda *a: calls.append(a))
    assert go(NOW, "--odds") == 0
    assert calls == []
    src = sources_of(seeded)["odds"]
    assert src["status"] == "stale" and "25" in src["note"]


def test_low_credits_reprobe_when_last_ok_is_old(seeded, monkeypatch):
    state = seeded / "data/state"
    state.mkdir(parents=True)
    (state / "sources.json").write_text(json.dumps({"odds": {
        "status": "ok", "fetched_at": "2026-11-16T09:00:00Z", "last_ok_at": "2026-11-16T09:00:00Z",
        "note": None, "remaining": 25, "used": 475,
    }}), encoding="utf-8")  # 30 h before NOW
    calls = []

    def fake(*a):
        calls.append(a)
        raise SourceError("offline test")

    monkeypatch.setattr(odds, "fetch_odds", fake)
    assert go(NOW, "--odds") == 0
    assert len(calls) == 1


def test_credit_skip_still_applies_fresh_snapshot(seeded, monkeypatch):
    assert go(NOW, "--odds") == 0
    f = seeded / "data/state/sources.json"
    sources = json.loads(f.read_text(encoding="utf-8"))
    sources["odds"]["remaining"] = 25
    f.write_text(json.dumps(sources), encoding="utf-8")
    monkeypatch.setattr(odds, "fetch_odds", lambda *a: pytest.fail("must not fetch"))
    assert go("2026-11-17T21:37:00Z", "--odds") == 0
    lat = latest_of(seeded)
    assert any(g["odds"] for g in lat["games"])
    assert sources_of(seeded)["odds"]["status"] == "stale"


@pytest.mark.parametrize("content", ["{not json", '{"fetched_at": "2026-11-17T15:17:00Z"}'])
def test_corrupt_odds_snapshot_is_ignored(seeded, content):
    state = seeded / "data/state"
    state.mkdir(parents=True)
    (state / "odds.json").write_text(content, encoding="utf-8")
    assert go() == 0
    lat = latest_of(seeded)
    assert lat["games"] and all(g["odds"] is None for g in lat["games"])


def test_game_odds_failure_does_not_fail_run(seeded, monkeypatch):
    from pipeline import value

    def boom(*a):
        raise ZeroDivisionError("x")

    monkeypatch.setattr(value, "game_odds", boom)
    assert go(NOW, "--odds") == 0
    lat = latest_of(seeded)
    assert all(g["odds"] is None for g in lat["games"])
    src = sources_of(seeded)["odds"]
    assert src["status"] == "failed" and src["note"] == "ZeroDivisionError"


def test_started_game_in_fresh_snapshot_gets_no_history_row(seeded):
    assert go(NOW, "--odds") == 0
    rows = len(odds_history(seeded))
    f = seeded / "data/state/odds.json"
    snap = json.loads(f.read_text(encoding="utf-8"))
    snap["fetched_at"] = "2026-11-18T00:05:00Z"
    f.write_text(json.dumps(snap), encoding="utf-8")
    assert go("2026-11-18T00:10:00Z", "--odds") == 0  # fetch happens, 333 has started
    new = odds_history(seeded)[rows:]
    assert 2026020333 not in [r["game_id"] for r in new]


def test_skip_when_no_game_within_24h(seeded, monkeypatch):
    calls = []
    monkeypatch.setattr(odds, "fetch_odds", lambda *a: calls.append(a))
    far = game(1, start="2026-11-18T21:17:00Z")  # 30 h after NOW
    state = {}
    assert run.odds_stage([far], state, dt(NOW), True) == (None, False)
    assert calls == [] and "odds" not in state
    started = game(2, "LIVE", start="2026-11-17T15:00:00Z")
    assert run.odds_stage([started], state, dt(NOW), True) == (None, False)
    assert calls == []


def test_no_fetch_without_flag_even_with_game_soon(seeded, monkeypatch):
    calls = []
    monkeypatch.setattr(odds, "fetch_odds", lambda *a: calls.append(a))
    assert go() == 0 and calls == []


def test_empty_odds_response_is_ok_with_note(seeded, monkeypatch):
    monkeypatch.setattr(odds, "fetch_odds", lambda now, key: odds.OddsResponse([], 480, 20, 0))
    assert go(NOW, "--odds") == 0
    src = sources_of(seeded)["odds"]
    assert src["status"] == "ok" and src["note"] == "Ei kertoimia tarjolla"
    assert all(g["odds"] is None for g in latest_of(seeded)["games"])


def test_remaining_credits_in_note_and_state(seeded, monkeypatch):
    events = json.loads((FIXTURES / config.ODDS_FIXTURE).read_text(encoding="utf-8"))
    monkeypatch.setattr(
        odds, "fetch_odds",
        lambda now, key: odds.OddsResponse(events, 480, 20, 3, True),
    )
    assert go(NOW, "--odds") == 0
    assert sources_of(seeded)["odds"]["note"] == (
        "Krediittejä jäljellä 480 · varahaku alueilla eu,fi"
    )
    saved = json.loads((seeded / "data/state/sources.json").read_text(encoding="utf-8"))
    assert saved["odds"]["remaining"] == 480 and saved["odds"]["used"] == 20


def test_started_game_keeps_frozen_odds(seeded):
    assert go(NOW, "--odds") == 0
    first = {g["id"]: g for g in latest_of(seeded)["games"]}
    assert first[2026020333]["odds"]
    assert go("2026-11-18T00:10:00Z", "--odds") == 0
    second = {g["id"]: g for g in latest_of(seeded)["games"]}
    assert second[2026020333]["state"] == "started"
    assert second[2026020333]["odds"] == first[2026020333]["odds"]


def test_offline_without_odds_flag_has_no_odds_source(seeded):
    assert go() == 0
    assert set(sources_of(seeded)) == {"nhl", "injuries"}


def test_latest_has_calibration(seeded):
    assert go() == 0
    assert "calibration" in latest_of(seeded)


def by_id(lat):
    return {g["id"]: g for g in lat["games"]}


def test_offline_run_fills_players_where_history_exists(seeded):
    assert go() == 0
    lat = latest_of(seeded)
    g = by_id(lat)[2026020338]  # TOR @ CHI; TOR has a cached fixture game
    assert g["players"]["away"]["team"] == "TOR" and len(g["players"]["away"]["top"]) >= 5
    assert g["players"]["home"] is None and export.validate(lat) == []
    assert by_id(lat)[2026020333]["players"] is None  # LAK @ BOS: no history
    assert tracking.read_log("players", SEASON)


def test_online_without_player_gate_has_no_players(seeded, monkeypatch):
    root = seeded
    online_with_gate(root, monkeypatch)
    monkeypatch.setattr(run, "_load_coefs", lambda: xg.load_coefs(run.XG_COEFS_STUB))
    assert run.players_enabled(None) is False
    assert run.main(["--now", NOW]) == 0
    lat = latest_of(root)
    assert lat["games"] and all(g["players"] is None for g in lat["games"])
    assert tracking.read_log("players", SEASON) == []


def test_player_gate_enables_online(root):
    (root / "model").mkdir()
    (root / "model/player_params.json").write_text('{"gate": {"passed": true}}', encoding="utf-8")
    assert run.players_enabled(None) is True
    assert run.players_enabled(FIXTURES) is True


def test_started_game_keeps_frozen_players(seeded):
    assert go() == 0
    first = by_id(latest_of(seeded))
    assert go("2026-11-18T00:40:00Z") == 0
    second = by_id(latest_of(seeded))
    started = second[2026020338]
    assert started["state"] == "started"
    assert started["players"] == first[2026020338]["players"]
    assert started["players"] is not None


def test_invalid_players_block_is_dropped_not_fatal(seeded, monkeypatch, caplog):
    real = run.players.project_lineup

    def bad(pg, team, day, season):
        slots = real(pg, team, day, season)
        return [{**slots[0], "pos": "F"}, *slots[1:]] if slots else slots

    monkeypatch.setattr(run.players, "project_lineup", bad)
    # "F" is normalised in the real code; force the bad value past it via the block itself
    real_block = run.players.team_block

    def bad_block(*a, **k):
        b = real_block(*a, **k)
        if b:
            b["top"][0]["pos"] = "F"
        return b

    monkeypatch.setattr(run.players, "team_block", bad_block)
    assert go() == 0
    lat = latest_of(seeded)
    assert export.validate(lat) == []
    g = by_id(lat)[2026020338]
    assert g["players"] is None
    assert "players dropped" in caplog.text
    assert tracking.read_log("players", SEASON) == []


def test_players_log_failure_does_not_block(seeded, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(run.tracking, "append_players", boom)
    assert go() == 0
    assert by_id(latest_of(seeded))[2026020338]["players"] is not None


def test_game_mults_follow_lineup_params(monkeypatch):
    from datetime import date as _date

    from pipeline import context as _context
    from pipeline.config import LineupParams
    from pipeline.predict import Mults

    lu = {"home": {"m_att": 1.05, "m_def": 0.98}, "away": {"m_att": 0.9, "m_def": 1.02}}
    calls = []

    def fake_h2h(tg, r, team, opp, asof):
        calls.append((team, opp, asof))
        return 1.02 if team == "CAR" else 0.99

    monkeypatch.setattr(_context, "h2h_mult", fake_h2h)
    d = _date(2026, 11, 20)
    assert run._game_mults(LineupParams(), lu, None, None, "CAR", "SEA", d) == Mults()
    assert calls == []
    assert run._game_mults(LineupParams(use_lineup=True), lu, None, None, "CAR", "SEA", d) == \
        Mults(att_h=1.05, def_h=0.98, att_a=0.9, def_a=1.02)
    assert run._game_mults(LineupParams(use_lineup=True), None, None, None, "CAR", "SEA", d) \
        == Mults()
    both = run._game_mults(LineupParams(use_lineup=True, use_h2h=True), lu, None, None,
                           "CAR", "SEA", d)
    assert (both.h2h_h, both.h2h_a, both.att_h) == (1.02, 0.99, 1.05)
    assert calls == [("CAR", "SEA", d), ("SEA", "CAR", d)]
