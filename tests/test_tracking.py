import json

from pipeline import config, tracking
from tests.test_predict import GAME, run


def game_obj(gid=2025020321, state="upcoming"):
    g = run(game=dict(GAME, id=gid))
    g["state"] = state
    return g


def rec(gid):
    return {"game_id": gid, "date": "2025-11-15", "home": "CAR", "away": "SEA",
            "home_goals": 4, "away_goals": 2, "reg_home_goals": 4, "reg_away_goals": 2,
            "decided": "REG", "home_won": True}


def row(ts, gid=1, start="2025-11-16T00:00:00Z"):
    return {"ts": ts, "game_id": gid, "start_utc": start}


def test_append_writes_one_row_per_upcoming_game_and_skips_started(root):
    n = tracking.append_predictions(
        [game_obj(2025020321), game_obj(2025020322), game_obj(2025020323, "started")],
        "2025-11-15T15:17:00Z")
    assert n == 2
    rows = tracking.read_log("predictions", "20252026")
    assert [r["game_id"] for r in rows] == [2025020321, 2025020322]
    assert rows[0]["ts"] == "2025-11-15T15:17:00Z"
    assert tracking.append_predictions([game_obj(2025020323, "started")], "x") == 0


def test_season_file_follows_game_id(root):
    tracking.append_predictions([game_obj(2026020001)], "2026-10-08T06:43:00Z")
    assert len(tracking.read_log("predictions", "20262027")) == 1
    assert tracking.read_log("predictions", "20252026") == []


def test_record_results_is_idempotent(root):
    recs = [rec(2025020321), rec(2025020322)]
    assert tracking.record_results(recs) == 2
    assert tracking.record_results(recs) == 0
    assert tracking.record_results(recs + [rec(2025020323)]) == 1
    assert len(tracking.read_log("results", "20252026")) == 3


def test_official_is_last_row_before_start():
    rows = [row("2025-11-15T15:17:00Z"), row("2025-11-15T21:37:00Z"),
            row("2025-11-16T00:30:00Z"), row("2025-11-16T00:00:00Z")]
    assert tracking.official(rows)[1]["ts"] == "2025-11-15T21:37:00Z"
    assert tracking.official([row("2025-11-16T00:30:00Z")]) == {}


def test_official_is_per_game_and_order_independent():
    rows = [row("2025-11-15T21:37:00Z", 1), row("2025-11-15T15:17:00Z", 1),
            row("2025-11-15T15:17:00Z", 2)]
    off = tracking.official(rows)
    assert off[1]["ts"] == "2025-11-15T21:37:00Z"
    assert off[2]["ts"] == "2025-11-15T15:17:00Z"


def test_log_rows_are_valid_single_line_json(root):
    tracking.append_predictions([game_obj()], "2025-11-15T15:17:00Z")
    path = root / "data" / "history" / "predictions" / "20252026.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    r = json.loads(lines[0])
    assert r["model_version"] == config.MODEL_VERSION
    assert set(r) == {"ts", "model_version", "game_id", "nhl_date", "start_utc", "home", "away",
                      "p_home_win", "p_reg", "lam_home", "lam_away", "total_mean", "most_likely",
                      "confidence", "goalie_home", "goalie_away", "market", "picks"}
    assert r["lam_home"] == g_lam(r)
    assert len(r["p_reg"]) == 3 and r["market"] is None and r["goalie_home"] is None


def g_lam(r):
    return game_obj()["_internal"]["lam_home"]
