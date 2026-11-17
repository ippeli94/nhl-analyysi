import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline import cache, features, xg

FIXTURES = Path(__file__).resolve().parent / "fixtures"
XG = 0.1


@pytest.fixture(autouse=True)
def stub_score(monkeypatch):
    def fake(df, coefs):
        return np.where(df["empty_net"].to_numpy(dtype=bool), np.nan, XG)

    monkeypatch.setattr(xg, "score", fake)


def shot(p=1, team="AAA", ev="sog", empty_net=False):
    return {"p": p, "t": 100, "team": team, "shooter": 1, "goalie": None if empty_net else 9,
            "x": 70.0, "y": 0.0, "dist": 19.0, "angle": 0.0, "type": "wrist", "sit": "1551",
            "strength": "5v5", "ev": ev, "empty_net": empty_net, "rebound": False, "rush": False}


def game(gid=1, date="2025-11-01", shots=None, home="AAA", away="BBB", score=(2, 1),
         score_reg=None, decided="REG"):
    reg = score_reg or score
    return {"v": 1, "id": gid, "season": "20252026", "game_type": 2, "date": date,
            "start_utc": date + "T23:00:00Z", "home": {"id": 1, "abbr": home},
            "away": {"id": 2, "abbr": away}, "score": {"home": score[0], "away": score[1]},
            "score_reg": {"home": reg[0], "away": reg[1]},
            "decided": decided, "shots": shots or [], "skaters": [], "goalies": [], "penalties": []}


def row(tg, gid, team):
    return tg[(tg["game_id"] == gid) & (tg["team"] == team)].iloc[0]


def test_two_rows_per_game_and_columns():
    tg = features.team_games([game(1), game(2, "2025-11-03")], {}, 0.6)
    assert len(tg) == 4
    assert list(tg.columns) == features.COLUMNS
    assert pd.api.types.is_datetime64_any_dtype(tg["date"])


def test_target_blends_xg_and_goals():
    shots = [shot(ev="goal"), shot(ev="goal")] + [shot() for _ in range(18)]
    r = row(features.team_games([game(shots=shots)], {}, 0.6), 1, "AAA")
    assert r["goals_reg"] == 2 and r["xg_reg"] == pytest.approx(2.0)
    assert r["target"] == pytest.approx(0.6 * 2.0 + 0.4 * 2)


def test_target_excludes_empty_net_and_overtime():
    shots = [shot(ev="goal"), shot(p=4, ev="goal"), shot(p=4), shot(empty_net=True, ev="goal", p=3)]
    r = row(features.team_games([game(shots=shots)], {}, 0.6), 1, "AAA")
    assert r["goals_reg"] == 1 and r["xg_reg"] == pytest.approx(XG)
    assert r["target"] == pytest.approx(0.6 * XG + 0.4)
    assert r["en_goals"] == 1


def test_period_targets_sum_to_target():
    shots = [shot(p=1, ev="goal"), shot(p=2), shot(p=2), shot(p=3, ev="goal"), shot(p=3), shot(p=4)]
    r = row(features.team_games([game(shots=shots)], {}, 0.6), 1, "AAA")
    assert r["target_p1"] + r["target_p2"] + r["target_p3"] == pytest.approx(r["target"])
    assert r["target_p1"] == pytest.approx(0.6 * XG + 0.4)


def test_b2b_flags():
    recs = [game(1, "2025-11-01"), game(2, "2025-11-02", home="BBB", away="CCC"),
            game(3, "2025-11-05", home="AAA", away="BBB")]
    tg = features.team_games(recs, {}, 0.6)
    r = row(tg, 2, "BBB")
    assert r["b2b"] == 1 and r["opp_b2b"] == 0
    assert row(tg, 2, "CCC")["opp_b2b"] == 1
    assert row(tg, 1, "AAA")["b2b"] == 0
    assert row(tg, 3, "AAA")["b2b"] == 0


def test_ot_game_reg_scores_are_equal_and_won_is_set():
    tg = features.team_games([game(score=(3, 2), score_reg=(2, 2), decided="OT")], {}, 0.6)
    h, a = row(tg, 1, "AAA"), row(tg, 1, "BBB")
    assert h["score_reg"] == h["opp_score_reg"] == 2 == a["score_reg"]
    assert bool(h["won"]) and not bool(a["won"])
    assert h["decided"] == "OT"


def test_sim_params_shares_sum_to_one():
    shots = [shot(p=1, ev="goal"), shot(p=2), shot(p=3), shot(p=3)]
    sp = features.sim_params(features.team_games([game(shots=shots)], {}, 0.6))
    assert abs(sum(sp["league_period_share"]) - 1) < 1e-9
    assert len(sp["league_period_share"]) == 3


def one_goal_games(n, with_en):
    recs = []
    for i in range(n):
        shots = [shot(ev="goal")]
        if i < with_en:
            shots.append(shot(p=3, ev="goal", empty_net=True))
        recs.append(game(i + 1, "2025-11-01", shots=shots, home="AAA", away=f"A{i}"))
    return recs


def test_en_prob_fallback_below_50_games():
    tg = features.team_games(one_goal_games(49, 20), {}, 0.6)
    assert features.sim_params(tg)["en_prob"] == {1: 0.30, 2: 0.15}


def test_en_prob_estimated():
    tg = features.team_games(one_goal_games(100, 30), {}, 0.6)
    assert features.sim_params(tg)["en_prob"][1] == 0.30


def test_en_prob_estimated_differs_from_fallback():
    tg = features.team_games(one_goal_games(100, 40), {}, 0.6)
    sp = features.sim_params(tg)
    assert sp["en_prob"][1] == 0.40
    assert sp["en_prob"][2] == 0.15  # no two-goal games: fallback


def test_last_played():
    tg = features.team_games([game(1, "2025-11-01"), game(2, "2025-11-04")], {}, 0.6)
    assert features.last_played(tg) == {"AAA": datetime.date(2025, 11, 4),
                                        "BBB": datetime.date(2025, 11, 4)}


def test_real_fixtures_build():
    def load(gid, kind):
        path = FIXTURES / f"gamecenter__{gid}__{kind}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    recs = [cache.build_record(load(g, "play-by-play"), load(g, "boxscore"))
            for g in (2026020286, 2026020298)]
    tg = features.team_games(recs, {}, 0.6)
    assert len(tg) == 4
    assert not tg["target"].isna().any()
