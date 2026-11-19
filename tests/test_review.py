import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_export import make_game

from pipeline import config, export
from tools import review

ARGS = ["--data-only", "--skip-tests"]
GOALIE = {"id": 1, "name": "G", "status": "projected", "gsax_per60": 0.1, "p_start": 0.7,
          "mult": 1.0}


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_latest(now=None):
    now = now or datetime.now(UTC)
    game = make_game(2025020321, iso(now + timedelta(hours=5)))
    game["goalies"] = {"home": dict(GOALIE), "away": dict(GOALIE, id=2)}
    sources = {"nhl": {"status": "ok", "fetched_at": iso(now), "note": None},
               "injuries": {"status": "unavailable", "fetched_at": None, "note": "x"}}
    obj = export.build_latest([game], sources, None, now)
    assert export.validate(obj) == []
    return obj


def write(root, obj):
    f = root / "site/data/latest.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def seeded(root):
    write(root, make_latest())
    return root


def edit(root, fn):
    obj = json.loads((root / "site/data/latest.json").read_text(encoding="utf-8"))
    fn(obj)
    write(root, obj)


def report(root):
    return (root / "reports/review.md").read_text(encoding="utf-8")


def latest(root):
    return json.loads((root / "site/data/latest.json").read_text(encoding="utf-8"))


def test_clean_data_reports_ok(seeded):
    assert review.main(ARGS) == 0 and report(seeded) == "OK — 0 ongelmaa\n"


def test_bad_probability_sum_is_error(seeded):
    def f(o):
        o["games"][0]["prob"].update(home_win=0.7, away_win=0.4)

    edit(seeded, f)
    assert review.main(ARGS) == 2
    assert any(line.startswith("[VIRHE] PROB") for line in report(seeded).splitlines())


def test_extreme_probability_is_warning(seeded):
    def f(o):
        o["games"][0]["prob"].update(home_win=0.85, away_win=0.15)

    edit(seeded, f)
    assert review.main(ARGS) == 1
    text = report(seeded)
    assert "[VAROITUS] PROB" in text and "0.85" in text


def test_matrix_sum_tolerance(seeded):
    def scale(k):
        def f(o):
            m = o["games"][0]["score"]["matrix"]
            total = sum(map(sum, m))
            o["games"][0]["score"]["matrix"] = [[c / total * k for c in r] for r in m]
        return f

    edit(seeded, scale(0.995))
    assert review.main(ARGS) == 0
    edit(seeded, scale(0.97))
    assert review.main(ARGS) == 2


def test_missing_goalie_is_warning(seeded):
    edit(seeded, lambda o: o["games"][0]["goalies"].update(home=None))
    assert review.main(ARGS) == 1
    assert "[VAROITUS] ROSTER" in report(seeded)


def test_goalie_mult_at_clamp_is_warning(seeded):
    edit(seeded, lambda o: o["games"][0]["goalies"]["away"].update(mult=1.10))
    assert review.main(ARGS) == 1


def test_xg_out_of_range_is_warning(seeded):
    edit(seeded, lambda o: o["games"][0]["xg"].update(home=5.5))
    assert review.main(ARGS) == 1


def test_stale_generated_at_is_error(seeded):
    edit(seeded, lambda o: o.update(generated_at=iso(datetime.now(UTC) - timedelta(hours=2))))
    assert review.main(ARGS) == 2
    assert "[VIRHE] FRESH" in report(seeded)


def test_unavailable_source_is_not_a_problem(seeded):
    assert review.main(ARGS) == 0


def test_failed_source_is_warning(seeded):
    edit(seeded, lambda o: o["sources"][1].update(status="failed"))
    assert review.main(ARGS) == 1
    assert "[VAROITUS] SOURCE" in report(seeded)


def test_missing_scheduled_game_is_error(seeded):
    f = seeded / "data/state/schedule_ids.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps([2025020321, 2025020999]), encoding="utf-8")
    assert review.main(ARGS) == 2
    text = report(seeded)
    assert "[VIRHE] SCHED" in text and "2025020999" in text and "2025020321" not in text


def test_missing_schedule_ids_is_warning_in_full_mode(seeded):
    assert review.main(["--skip-tests"]) == 1
    assert "[VAROITUS] SCHED" in report(seeded)


def test_secret_shaped_string_is_error(seeded):
    (seeded / "pipeline").mkdir()
    (seeded / "pipeline/x.py").write_text('KEY = "' + "ab12" * 8 + '"\n', encoding="utf-8")
    assert review.main(["--skip-tests"]) == 2
    assert "[VIRHE] SECRET" in report(seeded)


def test_secret_skips_fixtures_and_cache(seeded):
    for d in ("tests/fixtures", "data/cache"):
        (seeded / d).mkdir(parents=True)
        (seeded / d / "x.json").write_text("ab12" * 8, encoding="utf-8")
    ids = seeded / "data/state/schedule_ids.json"
    ids.parent.mkdir(parents=True, exist_ok=True)
    ids.write_text("[2025020321]", encoding="utf-8")
    assert review.main(["--skip-tests"]) == 0


def test_secret_in_site_data_is_error_even_data_only(seeded):
    (seeded / "site/data/other.json").write_text('{"k": "' + "cd34" * 8 + '"}', encoding="utf-8")
    assert review.main(ARGS) == 2


def test_similar_problems_are_grouped_and_report_is_capped():
    now = datetime(2026, 11, 15, 21, 44, tzinfo=UTC)
    problems = [
        review.Problem("VAROITUS", "LINT", f"a.py:{i}", "F841 x", "poista") for i in range(20)
    ]
    problems += [review.Problem("VIRHE", "PROB", f"g{i}", "m", f"h{i}") for i in range(30)]
    text = review.render(problems, now)
    assert len(text.splitlines()) == 32 and "(+19 muuta samanlaista)" in text
    problems += [review.Problem("VIRHE", "SITE", f"s{i}", "m", f"k{i}") for i in range(30)]
    text = review.render(problems, now)
    assert len(text.splitlines()) <= 40
    assert "jätetty pois" in text
    assert text.startswith("# Tarkistusraportti 2026-11-15T21:44Z — 60 virhettä, 20 varoitusta")


def test_render_ok():
    assert review.render([], datetime.now(UTC)) == "OK — 0 ongelmaa\n"


def test_review_field_is_written_back(seeded):
    edit(seeded, lambda o: o["games"][0]["goalies"].update(home=None))
    review.main(ARGS)
    assert latest(seeded)["review"] == {"problems": 1, "worst": "warning"}


def test_review_field_clean_run(seeded):
    review.main(ARGS)
    assert latest(seeded)["review"] == {"problems": 0, "worst": None}


def test_missing_latest_json_is_error(root):
    assert review.main(ARGS) == 2
    assert "[VIRHE] SITE" in report(root)


def test_invalid_schema_is_error(seeded):
    edit(seeded, lambda o: o.pop("games"))
    assert review.main(ARGS) == 2


def test_track_missing_result_is_warning(seeded):
    now = datetime.now(UTC)
    start = iso(now - timedelta(hours=40))
    row = {"ts": iso(now - timedelta(hours=45)), "game_id": 2025020100, "start_utc": start}
    f = seeded / "data/history/predictions/20252026.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert review.main(ARGS) == 1
    text = report(seeded)
    assert "[VAROITUS] TRACK" in text and "2025020100" in text


def write_cache(root, game_id, kind=None):
    p = root / "data/cache/games/20252026" / f"{game_id}.json.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * 32 if kind == "zeros" else gzip.compress(b"{"))
    return p


@pytest.mark.parametrize("kind", ["zeros", "badjson"])
def test_unreadable_cache_file_is_error(seeded, kind):
    write_cache(seeded, 2025020100, kind)
    assert review.main(ARGS) == 2
    text = report(seeded)
    assert "[VIRHE] CACHE" in text and "2025020100.json.gz" in text


def test_unparseable_history_line_is_warning_not_crash(seeded):
    now = datetime.now(UTC)
    f = seeded / "data/history/results/20252026.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("{not json\n", encoding="utf-8")
    pred = seeded / "data/history/predictions/20252026.jsonl"
    pred.parent.mkdir(parents=True)
    pred.write_text(json.dumps({"ts": iso(now), "game_id": 1, "start_utc": iso(now)})
                    + "\n{broken\n", encoding="utf-8")
    assert review.main(ARGS) == 1
    text = report(seeded)
    assert "[VAROITUS] TRACK" in text and "(+1 muuta samanlaista)" in text  # both logs flagged


def test_cached_ids_are_built_once_per_season(seeded, monkeypatch):
    now = datetime.now(UTC)
    rows = [{"ts": iso(now - timedelta(hours=45)), "game_id": 2025020100 + i,
             "start_utc": iso(now - timedelta(hours=40))} for i in range(5)]
    f = seeded / "data/history/predictions/20252026.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    calls = []
    real = review.cache.cached_ids
    monkeypatch.setattr(review.cache, "cached_ids", lambda s: calls.append(s) or real(s))
    review.main(ARGS)
    assert calls.count("20252026") == 1


def test_xg_skipped_with_few_games(seeded):
    assert review.main(ARGS) == 0


def test_does_not_mutate_when_latest_unreadable(root):
    (root / "site/data").mkdir(parents=True)
    (root / "site/data/latest.json").write_text("{", encoding="utf-8")
    assert review.main(ARGS) == 2


def seed_draws(root, n, model, actual):
    ties = round(n * actual)
    now = datetime.now(UTC)
    preds, results = [], []
    for i in range(n):
        gid = 2025020500 + i
        start = now - timedelta(hours=6) + timedelta(seconds=i)
        preds.append({"ts": iso(start - timedelta(hours=5)), "game_id": gid,
                      "start_utc": iso(start), "model_version": config.MODEL_VERSION,
                      "p_reg": [(1 - model) / 2, model, (1 - model) / 2]})
        results.append({"game_id": gid, "reg_home_goals": 2,
                        "reg_away_goals": 2 if i < ties else 1})
    for kind, rows in (("predictions", preds), ("results", results)):
        f = root / f"data/history/{kind}/20252026.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_draw_gap_warning(root):
    write(root, make_latest())
    seed_draws(root, 150, 0.25, 0.22)
    assert review.main(ARGS) == 1
    text = report(root)
    assert "[VAROITUS] CALIB" in text and "+3.0 %-yks." in text


def test_no_draw_warning_under_100_games(root):
    write(root, make_latest())
    seed_draws(root, 60, 0.25, 0.20)
    review.main(ARGS)
    assert "CALIB" not in report(root)


def test_no_draw_warning_small_gap(root):
    write(root, make_latest())
    seed_draws(root, 120, 0.25, 0.24)
    review.main(ARGS)
    assert "CALIB" not in report(root)


# --- odds ---------------------------------------------------------------------------------


def odds_block(now, valid=False):
    return {"fetched_at": iso(now), "picks": [], "markets": [
        {"key": "h2h", "line": None, "ref": "pinnacle", "outcomes": [],
         "books": [{"book": "coolbet", "prices": [1.5, 2.6], "vig": 0.0298, "valid": True},
                   {"book": "betsson", "prices": [1.5, 2.6], "vig": 0.2, "valid": valid}]}]}


def write_state(root, name, obj):
    f = root / "data/state" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(obj), encoding="utf-8")


def seed_odds(root, hours_old=1, remaining=400, with_block=True, snapshot=None, now=None):
    now = now or datetime.now(UTC)
    edit(root, lambda o: o["games"][0].update(odds=odds_block(now) if with_block else None))
    ok = iso(now - timedelta(hours=hours_old))
    write_state(root, "sources.json", {"odds": {
        "status": "ok", "fetched_at": ok, "last_ok_at": ok, "note": None,
        "remaining": remaining, "used": 100}})
    write_state(root, "odds.json", snapshot or {
        "fetched_at": ok, "remaining": remaining, "games": {}, "unknown_teams": [],
        "unmatched": []})


@pytest.fixture
def odds_root(seeded):
    seed_odds(seeded)
    edit(seeded, lambda o: o["games"][0]["odds"]["markets"][0]["books"][1].update(
        vig=0.03, valid=True))
    return seeded


def test_clean_odds_is_ok(odds_root):
    assert review.main(ARGS) == 0


def test_no_odds_source_is_not_a_problem(seeded):
    assert review.main(ARGS) == 0 and review.check_odds(
        review.Ctx(seeded, latest(seeded), datetime.now(UTC), "data")) == []


def test_stale_odds_is_warning(seeded):
    seed_odds(seeded, hours_old=17, with_block=False)
    assert review.main(ARGS) == 1
    text = report(seeded)
    assert "[VAROITUS] ODDS" in text and "17.0 h sitten" in text


def test_low_credits_is_warning(odds_root):
    seed_odds(odds_root, remaining=59, with_block=False)
    assert review.main(ARGS) == 1
    assert "krediittejä jäljellä 59" in report(odds_root)


def test_invalid_vig_is_warning(seeded):
    seed_odds(seeded)
    assert review.main(ARGS) == 1
    assert "betsson: 1 markkinaa marginaalin ulkopuolella" in report(seeded)


def test_invalid_vig_collapses_to_one_warning_per_book(seeded):
    seed_odds(seeded)
    edit(seeded, lambda o: o["games"][0]["odds"]["markets"].append(
        dict(o["games"][0]["odds"]["markets"][0], key="totals", line=5.5)))
    review.main(ARGS)
    assert report(seeded).count("betsson: 2 markkinaa marginaalin ulkopuolella") == 1


def test_unknown_team_is_error_when_fetched_this_run(odds_root):
    stamp = latest(odds_root)["generated_at"]
    seed_odds(odds_root, snapshot={
        "fetched_at": stamp, "remaining": 400, "games": {},
        "unknown_teams": ["Utah Hockey Club"], "unmatched": []})
    assert review.main(ARGS) == 2
    text = report(odds_root)
    assert "[VIRHE] ODDS" in text and "Utah Hockey Club" in text


def test_unknown_team_from_older_fetch_is_warning(odds_root):
    seed_odds(odds_root, snapshot={
        "fetched_at": iso(datetime.now(UTC) - timedelta(hours=5)), "remaining": 400, "games": {},
        "unknown_teams": ["Utah Hockey Club"], "unmatched": []})
    assert review.main(ARGS) == 1
    text = report(odds_root)
    assert "[VAROITUS] ODDS" in text and "tuntematon joukkue: Utah Hockey Club" in text
    assert "[VIRHE]" not in text


def test_unmatched_event_is_warning(odds_root):
    seed_odds(odds_root, snapshot={
        "fetched_at": iso(datetime.now(UTC)), "remaining": 400, "games": {},
        "unknown_teams": [], "unmatched": [
            {"home": "CAR", "away": "SEA", "commence_utc": "2026-11-18T00:00:00Z"}]})
    assert review.main(ARGS) == 1
    assert "SEA @ CAR 2026-11-18T00:00:00Z" in report(odds_root)


def test_game_without_odds_is_warning_only_when_fresh(seeded):
    seed_odds(seeded, with_block=False)
    assert review.main(ARGS) == 1
    assert "ei kertoimia yhdeltäkään välittäjältä" in report(seeded)
    seed_odds(seeded, hours_old=17, with_block=False)
    review.main(ARGS)
    assert "ei kertoimia yhdeltäkään" not in report(seeded)


def test_malformed_odds_files_never_crash(odds_root):
    write_state(odds_root, "sources.json", {"odds": {"last_ok_at": "huomenna", "remaining": "x"}})
    write_state(odds_root, "odds.json", ["rikki"])
    assert review.main(ARGS) == 1
    assert "tarkistus kaatui" not in report(odds_root)
    (odds_root / "data/state/odds.json").write_text("{", encoding="utf-8")
    assert review.main(ARGS) == 1
    assert "ei ole luettavaa JSONia" in report(odds_root)


# --- players ------------------------------------------------------------------------------


def _player(pid, team):
    return {"id": pid, "name": f"P{pid}", "team": team, "pos": "C", "toi_proj": 18.0,
            "p_points": [0.6, 0.2, 0.05], "p_goals": [0.3, 0.06, 0.008],
            "exp_goals": 0.4, "exp_points": 0.9, "odds": None}


def _block(team, base):
    return {"team": team, "dressed": 18, "goals_mean": 3.2, "exp_goals": 3.1,
            "exp_points": 8.3, "unassigned": 0.03,
            "top": [_player(base + i, team) for i in range(6)]}


@pytest.fixture
def players_root(root):
    obj = make_latest()
    obj["games"][0]["players"] = {"lineup": "projected", "home": _block("CAR", 100),
                                  "away": _block("SEA", 200)}
    assert export.validate(obj) == []
    write(root, obj)
    return root


def _gate(root, passed=True):
    (root / "model").mkdir(exist_ok=True)
    (root / "model/player_params.json").write_text(
        json.dumps({"gate": {"passed": passed}}), encoding="utf-8")


def _home(o):
    return o["games"][0]["players"]["home"]


def test_clean_players_ok(players_root):
    assert review.main(ARGS) == 0


def test_non_monotone_tail_is_error(players_root):
    edit(players_root, lambda o: _home(o)["top"][0].update(p_points=[0.3, 0.4, 0.1]))
    assert review.main(ARGS) == 2
    assert "[VIRHE] PROB" in report(players_root) and "ei laskeva" in report(players_root)


def test_goals_above_points_is_error(players_root):
    edit(players_root, lambda o: _home(o)["top"][0].update(p_goals=[0.7, 0.06, 0.008]))
    assert review.main(ARGS) == 2
    assert "maalit" in report(players_root)


def test_sum_mismatch_is_error(players_root):
    edit(players_root, lambda o: _home(o).update(exp_goals=2.5))
    assert review.main(ARGS) == 2
    assert "pelaajien maaliodotus 2.50 vs joukkue 3.20" in report(players_root)


def test_points_over_three_times_goals_is_error(players_root):
    edit(players_root, lambda o: _home(o).update(exp_points=10.0))
    assert review.main(ARGS) == 2
    assert "yli 3 × maalit" in report(players_root)


def test_foreign_player_is_error(players_root):
    edit(players_root, lambda o: _home(o)["top"][0].update(team="XXX"))
    assert review.main(ARGS) == 2
    assert "[VIRHE] ROSTER" in report(players_root)


def test_player_in_both_teams_is_error(players_root):
    edit(players_root, lambda o: o["games"][0]["players"]["away"]["top"][0].update(id=100))
    assert review.main(ARGS) == 2
    assert "[VIRHE] ROSTER" in report(players_root)


def test_short_top_list_is_error(players_root):
    edit(players_root, lambda o: _home(o).update(top=_home(o)["top"][:4]))
    assert review.main(ARGS) == 2
    assert "listalla 4 pelaajaa (< 5)" in report(players_root)


def test_dressed_17_is_warning(players_root):
    edit(players_root, lambda o: _home(o).update(dressed=17))
    assert review.main(ARGS) == 1
    assert "[VAROITUS] ROSTER" in report(players_root)


def test_missing_players_warns_only_with_gate(players_root):
    edit(players_root, lambda o: o["games"][0].update(players=None))
    assert review.main(ARGS) == 0
    _gate(players_root, passed=False)
    assert review.main(ARGS) == 0
    _gate(players_root)
    assert review.main(ARGS) == 1
    assert "ei pelaajaennustetta" in report(players_root)


def test_malformed_players_block_does_not_crash(players_root):
    edit(players_root, lambda o: _home(o).update(goals_mean=None, top=None))
    review.main(ARGS)  # schema errors are SITE's; no traceback either way
    assert "tarkistus kaatui" not in report(players_root)


# --- performance --------------------------------------------------------------------------


def seed_history(root):
    f = root / "data/history/predictions/20252026.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("", encoding="utf-8")


def write_perf(root, obj):
    f = root / "site/data/performance.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(obj), encoding="utf-8")


def test_no_perf_warning_without_history(seeded):
    review.main(ARGS)
    assert "PERF" not in report(seeded)


def test_missing_performance_file_is_warning(seeded):
    seed_history(seeded)
    assert review.main(ARGS) == 1
    assert "[VAROITUS] PERF" in report(seeded)


def test_invalid_performance_file_is_warning(seeded):
    seed_history(seeded)
    write_perf(seeded, {"windows": {}})
    assert review.main(ARGS) == 1
    assert "[VAROITUS] PERF" in report(seeded) and "skeemavirhe" in report(seeded)


def test_valid_performance_file_is_clean(seeded):
    from pipeline import performance

    seed_history(seeded)
    write_perf(seeded, performance.build_performance([], [], [], None, datetime.now(UTC), None))
    review.main(ARGS)
    assert "PERF" not in report(seeded)
