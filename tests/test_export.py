import copy
import json
from datetime import UTC, datetime

import pytest

from pipeline import config, export, value
from pipeline.adjust import GoalieRatings
from pipeline.config import ModelParams
from pipeline.predict import predict_game
from pipeline.ratings import PeriodShares, Ratings

NOW = datetime(2025, 11, 15, 21, 41, 7, tzinfo=UTC)
SP = {"league_period_share": [0.3, 0.35, 0.35], "en_prob": {1: 0.3, 2: 0.15}}
PS = PeriodShares([0.3, 0.35, 0.35], {}, {})
SOURCES = {
    "nhl": {"status": "ok", "fetched_at": "2025-11-15T21:40:00Z", "last_ok_at": "x", "note": None},
    "injuries": {"status": "unavailable", "fetched_at": None, "note": "ESPN 403"},
}


def team(abbr, i):
    return {"id": i, "abbr": abbr, "name": abbr, "logo": f"{abbr}.svg", "logo_dark": None}


def make_game(gid, start_utc, nhl_date="2025-11-15", home="CAR", away="SEA"):
    game = {"id": gid, "season": "20252026", "game_type": 2, "nhl_date": nhl_date,
            "start_utc": start_utc, "state": "FUT",
            "home": team(home, 12), "away": team(away, 55)}
    r = Ratings(intercept=1.0, n_games={home: 20, away: 20})
    out = predict_game(game, r, PS, SP, ModelParams(), {}, GoalieRatings(), {})
    out.update(form=None, season_compare=None, h2h=None)
    return out


@pytest.fixture
def g1():
    return make_game(2025020321, "2025-11-16T00:00:00Z")


@pytest.fixture
def g2():
    return make_game(2025020322, "2025-11-16T02:00:00Z", home="TOR", away="BOS")


def latest(games, next_game_date=None):
    return export.build_latest(games, SOURCES, next_game_date, NOW)


def test_build_latest_validates(g1, g2):
    assert export.validate(latest([g1, g2], None)) == []


def test_top_level_keys_and_values(g1):
    obj = latest([g1], "2025-11-17")
    assert set(obj) == {"schema_version", "generated_at", "model_version", "sources",
                        "review", "games", "next_game_date", "calibration"}
    assert obj["schema_version"] == 1
    assert obj["model_version"] == config.MODEL_VERSION
    assert obj["next_game_date"] == "2025-11-17"
    assert obj["review"] == {"problems": 0, "worst": None}
    assert obj["sources"] == [
        {"id": "nhl", "status": "ok", "fetched_at": "2025-11-15T21:40:00Z", "note": None},
        {"id": "injuries", "status": "unavailable", "fetched_at": None, "note": "ESPN 403"},
    ]


def test_games_sorted_by_start(g1, g2):
    obj = latest([g2, g1])
    assert [g["id"] for g in obj["games"]] == [g1["id"], g2["id"]]


def test_games_with_same_start_sorted_by_id(g1):
    later_id = make_game(2025020400, g1["start_utc"], home="TOR", away="BOS")
    obj = latest([later_id, g1])
    assert [g["id"] for g in obj["games"]] == [g1["id"], later_id["id"]]


def test_empty_games_with_next_date_is_valid():
    obj = latest([], "2025-11-17")
    assert obj["games"] == []
    assert export.validate(obj) == []


def test_empty_games_without_next_date_is_valid():
    assert export.validate(latest([], None)) == []


def test_private_keys_are_not_exported(g1):
    assert "_internal" in g1
    obj = latest([g1])
    assert not any(k.startswith("_") for k in obj["games"][0])
    assert "_internal" not in json.dumps(obj)


def test_build_latest_does_not_mutate_input(g1):
    before = copy.deepcopy(g1)
    latest([g1])
    assert g1 == before


def test_probability_out_of_range_is_reported(g1):
    obj = latest([g1])
    obj["games"][0]["prob"]["home_win"] = 1.2
    errors = export.validate(obj)
    assert errors and any("home_win" in e for e in errors)


def test_missing_required_field_is_reported(g1):
    obj = latest([g1])
    del obj["games"][0]["xg"]
    errors = export.validate(obj)
    assert errors and any("xg" in e for e in errors)


def test_unknown_source_status_is_rejected(g1):
    obj = latest([g1])
    obj["sources"][0]["status"] = "bogus"
    assert export.validate(obj)


def test_later_phase_fields_accept_null_and_objects(g1):
    obj = latest([g1])
    game = obj["games"][0]
    game["insights"] = {"note": "x"}
    game["form"] = {"home": {}}
    game["h2h"] = None
    assert export.validate(obj) == []


def player_block():
    top = [{"id": 8478403, "name": None, "team": "CAR", "pos": "C", "toi_proj": 19.4,
            "p_points": [0.6, 0.2, 0.07], "p_goals": [0.3, 0.06, 0.008],
            "exp_goals": 0.4, "exp_points": 0.95, "odds": None}]
    side = {"team": "CAR", "dressed": 18, "goals_mean": 3.2, "exp_goals": 3.1,
            "exp_points": 8.3, "unassigned": 0.03, "top": top}
    return {"lineup": "projected", "home": side, "away": None}


def test_players_block_validates(g1):
    obj = latest([g1])
    obj["games"][0]["players"] = player_block()
    assert export.validate(obj) == []


def test_players_bad_lengths_rejected(g1):
    obj = latest([g1])
    obj["games"][0]["players"] = player_block()
    obj["games"][0]["players"]["home"]["top"][0]["p_points"] = [0.6, 0.2]
    errors = export.validate(obj)
    assert errors and any("p_points" in e for e in errors)


def test_goalie_status_enum_and_null_goalie(g1):
    obj = latest([g1])
    game = obj["games"][0]
    game["goalies"]["home"] = None
    assert export.validate(obj) == []
    game["goalies"]["away"] = {"id": 1, "name": "G", "status": "maybe", "gsax_per60": None,
                               "p_start": 0.5, "mult": 1.0}
    assert export.validate(obj)


def test_write_refuses_invalid(root, g1):
    bad = latest([g1])
    bad["games"][0]["prob"]["home_win"] = 1.2
    with pytest.raises(ValueError):
        export.write_latest(bad)
    assert not (root / "site" / "data" / "latest.json").exists()


def test_write_latest_writes_valid_file(root, g1):
    path = export.write_latest(latest([g1]))
    assert path == root / "site" / "data" / "latest.json"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not list(path.parent.glob("*.tmp"))


def test_generated_at_is_utc_z(g1):
    obj = latest([g1])
    assert obj["generated_at"] == "2025-11-15T21:41:07Z"
    assert obj["generated_at"].endswith("Z")


ODDS_BOOKS = {
    "pinnacle": {"h2h": [1.58, 2.52], "h2h3": None, "totals": [[5.5, 1.9, 1.95]],
                 "spreads": [[-1.5, 2.4, 1.6]]},
    "coolbet": {"h2h": [1.66, 2.30], "h2h3": None, "totals": [[5.5, 2.0, 1.85]],
                "spreads": []},
}


def test_odds_block_validates(g1):
    g1["odds"] = value.game_odds(g1, ODDS_BOOKS, "2025-11-15T21:37:40Z")
    assert g1["odds"]["markets"]
    assert export.validate(latest([g1])) == []


def test_odds_bad_flag_rejected(g1):
    g1["odds"] = value.game_odds(g1, ODDS_BOOKS, "2025-11-15T21:37:40Z")
    obj = latest([g1])
    obj["games"][0]["odds"]["markets"][0]["outcomes"][0]["flag"] = "maybe"
    errors = export.validate(obj)
    assert errors and any("flag" in e or "maybe" in e for e in errors)


def test_calibration_block_validates_and_defaults(g1):
    now = datetime(2025, 11, 15, 12, 0, tzinfo=UTC)
    obj = export.build_latest([g1], {}, None, now)
    assert obj["calibration"] == {"draw": None} and export.validate(obj) == []
    draw = {"season": "20262027", "model_version": "1.1.0", "n": 120, "model": 0.25,
            "actual": 0.22, "gap": 0.03, "warn": True}
    obj = export.build_latest([g1], {}, None, now, {"draw": draw})
    assert obj["calibration"]["draw"] == draw and export.validate(obj) == []
    draw["warn"] = "yes"
    assert export.validate(export.build_latest([g1], {}, None, now, {"draw": draw}))
