import json
import re

import pytest

from pipeline import config, tracking, value
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


TS_ODDS = "2025-11-15T21:37:40Z"
HEX32 = re.compile(r"\b[0-9a-f]{32}\b")


def odds_books(home_pinn=1.58, away_pinn=2.52, home_cool=1.66, away_cool=2.35):
    return {
        "pinnacle": {"h2h": [home_pinn, away_pinn], "h2h3": None, "totals": [], "spreads": []},
        "coolbet": {"h2h": [home_cool, away_cool], "h2h3": None, "totals": [], "spreads": []},
    }


def test_log_row_market_and_picks_from_odds():
    g = game_obj()
    g["odds"] = value.game_odds(g, odds_books(), TS_ODDS)
    r = tracking.log_row(g, "2025-11-15T15:17:00Z")
    assert r["market"]["ref"] == "pinnacle"
    assert r["market"]["best_h2h"]["home"]["book"] == "coolbet"
    assert len(g["odds"]["picks"]) > 0
    assert r["picks"] == g["odds"]["picks"]


def test_log_row_without_odds_keeps_nulls():
    g = game_obj()
    g["odds"] = None
    r = tracking.log_row(g, "2025-11-15T15:17:00Z")
    assert r["market"] is None and r["picks"] is None


def test_odds_rows_and_append_roundtrip(root):
    g = game_obj(2025020321)
    snapshot = {"fetched_at": TS_ODDS, "remaining": 100, "unknown_teams": [], "unmatched": [],
                "games": {"2025020321": odds_books(), "2025020322": odds_books()}}
    rows = tracking.odds_rows(snapshot, [g])
    assert len(rows) == 1
    assert rows[0]["ts"] == TS_ODDS and rows[0]["game_id"] == 2025020321
    assert rows[0]["home"] == "CAR" and rows[0]["away"] == "SEA"
    assert rows[0]["ref"]["source"] == "pinnacle"
    assert tracking.append_odds(rows) == 1
    assert tracking.read_log("odds", "20252026") == rows


def test_odds_history_has_no_hex_ids_or_key(root):
    # the pattern must match a runtime-built 32-hex string, or the negative check is vacuous
    runtime_hex = f"{0xABCDEF0123456789ABCDEF0123456789:032x}"
    assert HEX32.search(" " + runtime_hex + " ") is not None
    g = game_obj(2025020321)
    snapshot = {"fetched_at": TS_ODDS, "games": {"2025020321": odds_books()}}
    tracking.append_odds(tracking.odds_rows(snapshot, [g]))
    text = (root / "data" / "history" / "odds" / "20252026.jsonl").read_text(encoding="utf-8")
    assert text.strip() and HEX32.search(text) is None


def test_read_log_rejects_unknown_kind():
    with pytest.raises(ValueError):
        tracking.read_log("picks", "20252026")


def _pred(gid, ts, start, draw, version=None):
    return {"ts": ts, "game_id": gid, "start_utc": start,
            "model_version": version or config.MODEL_VERSION,
            "p_reg": [(1 - draw) / 2, draw, (1 - draw) / 2]}


def _res(gid, tie):
    return {"game_id": gid, "reg_home_goals": 2, "reg_away_goals": 2 if tie else 1}


def _games(n, draw, ties):
    preds = [_pred(2026020000 + i, f"2026-10-{1 + i // 20:02d}T00:00:00Z",
                   f"2026-10-{1 + i // 20:02d}T12:00:{i % 20:02d}Z", draw) for i in range(n)]
    return preds, [_res(2026020000 + i, i < ties) for i in range(n)]


def test_draw_gap_counts_official_current_version_only():
    preds = [
        _pred(2026020001, "2026-10-01T00:00:00Z", "2026-10-01T12:00:00Z", 0.40),
        _pred(2026020001, "2026-10-01T06:00:00Z", "2026-10-01T12:00:00Z", 0.25),
        _pred(2026020002, "2026-10-02T06:00:00Z", "2026-10-02T12:00:00Z", 0.25),
        _pred(2026020003, "2026-10-03T06:00:00Z", "2026-10-03T12:00:00Z", 0.25, "1.0.0"),
    ]
    results = [_res(2026020001, True), _res(2026020002, False), _res(2026020003, True)]
    d = tracking.draw_gap(preds, results, "20262027")
    assert d["n"] == 2 and d["model"] == pytest.approx(0.25) and d["actual"] == pytest.approx(0.5)
    assert d["gap"] == pytest.approx(-0.25) and d["warn"] is False


def test_draw_gap_window_keeps_latest_300():
    preds, results = _games(350, 0.25, 0)
    results = [_res(r["game_id"], r["game_id"] < 2026020050) for r in results]
    d = tracking.draw_gap(preds, results, "20262027")
    assert d["n"] == 300 and d["actual"] == 0.0


def test_draw_gap_warn_threshold():

    # n 120 with 24 draws of 120 = 0.20 actual, model 0.221 -> gap 0.021
    preds, results = _games(120, 0.221, 24)
    assert tracking.draw_gap(preds, results, "20262027")["warn"] is True
    preds, results = _games(120, 0.219, 24)
    assert tracking.draw_gap(preds, results, "20262027")["warn"] is False


def test_draw_gap_none_without_matches():
    assert tracking.draw_gap([], [], "20262027") is None
