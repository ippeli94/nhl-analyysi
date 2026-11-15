import json
from pathlib import Path

import numpy as np
import pytest

from pipeline import shots

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REG_GAME_ID = 2026020286
SO_GAME_ID = 2026020298


def load(kind: str, game_id: int) -> dict:
    name = f"gamecenter__{game_id}__{'play-by-play' if kind == 'pbp' else kind}.json"
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("code,home,exp", [
    ("1551", True, ("5v5", False, False)), ("1441", False, ("ev", False, False)),
    ("1451", True, ("pp", False, False)), ("1451", False, ("sh", False, False)),
    ("0651", True, ("sh", True, False)), ("0651", False, ("pp", False, False)),
    ("1560", False, ("sh", True, False)),
    ("1541", False, ("pp", False, False)),  # FIELDS.md hand-read example: PIT (away) PP goal
])
def test_parse_situation(code, home, exp):
    assert shots.parse_situation(code, home) == exp


def test_penalty_shot_situation_is_excluded():
    assert shots.parse_situation("0101", True)[2] is True


def test_normalize_home_defending_left_keeps_sign():
    assert shots.normalize_xy(70, 10, True, "left", None) == (70, 10)


def test_normalize_home_defending_right_flips():
    assert shots.normalize_xy(-70, 10, True, "right", None) == (70, -10)


def test_normalize_away_is_opposite_of_home():
    assert shots.normalize_xy(-70, 10, False, "left", None) == (70, -10)


def test_normalize_infers_from_median_when_side_missing():
    assert shots.normalize_xy(-60, 5, True, None, -55.0) == (60, -5)


def _score(pbp, abbr):
    return pbp["homeTeam" if pbp["homeTeam"]["abbrev"] == abbr else "awayTeam"]["score"]


def test_real_game_shots_point_at_the_net():
    rows = shots.extract_shots(load("pbp", REG_GAME_ID))
    assert np.mean([r["x"] > 0 for r in rows]) > 0.85
    assert np.median([r["dist"] for r in rows if r["ev"] == "goal"]) < 40
    assert {r["type"] for r in rows} <= {"wrist", "snap", "slap", "backhand", "tip-in",
                                         "deflected", "wrap-around", "other"}


def test_goal_count_matches_final_score():
    pbp = load("pbp", REG_GAME_ID)
    rows = shots.extract_shots(pbp)
    for abbr in (pbp["homeTeam"]["abbrev"], pbp["awayTeam"]["abbrev"]):
        goals = [r for r in rows if r["team"] == abbr and r["ev"] == "goal"]
        assert len(goals) == _score(pbp, abbr)
    assert any(r["empty_net"] and r["goalie"] is None for r in rows if r["ev"] == "goal")


def test_shootout_attempts_are_excluded():
    pbp = load("pbp", SO_GAME_ID)
    rows = shots.extract_shots(pbp)
    assert rows and all(r["p"] != 5 for r in rows)
    assert sum(r["ev"] == "goal" for r in rows) == 4  # 2-2 after overtime; SO winner not counted


def test_real_game_power_play_goal_strength():
    rows = shots.extract_shots(load("pbp", SO_GAME_ID))
    pp = [r for r in rows if r["ev"] == "goal" and r["sit"] == "1541"]
    assert pp and all(r["team"] == "PIT" and r["strength"] == "pp" for r in pp)


def _pbp(plays):
    return {"homeTeam": {"id": 1, "abbrev": "HOM"}, "awayTeam": {"id": 2, "abbrev": "AWY"},
            "plays": plays}


def _play(kind, t, owner=1, x=70, y=5, period=1, side="left", sit="1551", **details):
    d = {"eventOwnerTeamId": owner, **details}
    if x is not None:
        d["xCoord"], d["yCoord"] = x, y
    play = {"typeDescKey": kind, "periodDescriptor": {"number": period, "periodType": "REG"},
            "timeInPeriod": f"{t // 60:02d}:{t % 60:02d}", "situationCode": sit, "details": d}
    if side:
        play["homeTeamDefendingSide"] = side
    return play


def test_shot_without_coordinates_is_dropped():
    assert shots.extract_shots(_pbp([_play("shot-on-goal", 10, x=None)])) == []


def test_missing_defending_side_is_inferred_from_median():
    plays = [_play("shot-on-goal", 10, x=-60, side=None),
             _play("shot-on-goal", 50, x=-70, side=None)]
    assert [r["x"] for r in shots.extract_shots(_pbp(plays))] == [60, 70]


def test_blocked_shots_and_excluded_situations_are_dropped():
    plays = [_play("blocked-shot", 10), _play("shot-on-goal", 20, sit="1421")]
    assert shots.extract_shots(_pbp(plays)) == []


def test_rebound_and_rush_flags():
    plays = [_play("shot-on-goal", 100), _play("shot-on-goal", 102),
             _play("faceoff", 200, x=0, y=0), _play("shot-on-goal", 203, x=60),
             _play("shot-on-goal", 400)]
    a, b, c, d = shots.extract_shots(_pbp(plays))
    assert (a["rebound"], b["rebound"], c["rebound"], d["rebound"]) == (False, True, False, False)
    assert (a["rush"], b["rush"], c["rush"], d["rush"]) == (False, False, True, False)


def test_rebound_requires_same_team_and_period():
    plays = [_play("shot-on-goal", 100, owner=2, x=-70), _play("shot-on-goal", 101),
             _play("shot-on-goal", 1199), _play("shot-on-goal", 1, period=2)]
    assert [r["rebound"] for r in shots.extract_shots(_pbp(plays))] == [False] * 4


def test_goal_rows_carry_assists():
    plays = [_play("goal", 10, scoringPlayerId=7, assist1PlayerId=8, goalieInNetId=9),
             _play("shot-on-goal", 300, shootingPlayerId=7, goalieInNetId=9)]
    goal_row, sog_row = shots.extract_shots(_pbp(plays))
    assert goal_row["a1"] == 8 and goal_row["a2"] is None and goal_row["shooter"] == 7
    assert "a1" not in sog_row


def test_extract_penalties_minutes():
    pens = shots.extract_penalties(load("pbp", REG_GAME_ID))
    assert pens and all(p["min"] in (2, 4, 5, 10) for p in pens)
    assert {p["team"] for p in pens} <= {"TOR", "COL"}
