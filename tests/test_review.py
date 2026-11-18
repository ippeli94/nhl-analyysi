import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_export import make_game

from pipeline import export
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
