import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline import adjust, cache, xg
from pipeline.adjust import GoalieRatings
from pipeline.config import ModelParams
from tests.synth import make_goalie_games, make_team_games

FIXTURES = Path(__file__).resolve().parent / "fixtures"
P = ModelParams()
ASOF = date(2025, 12, 1)


@pytest.fixture
def stub_score(monkeypatch):
    def fake(df, coefs):
        return np.where(df["empty_net"].to_numpy(dtype=bool), np.nan, 0.1)

    monkeypatch.setattr(xg, "score", fake)


@pytest.fixture
def unit_weights(monkeypatch):
    monkeypatch.setattr(
        adjust.ratings, "weights", lambda tg, asof, p, season=None: np.ones(len(tg))
    )


def gg_rows(rows):
    """rows: (day offset, team, goalie, started, xg, ga) in season 20252026."""
    base = pd.Timestamp("2025-11-01")
    return pd.DataFrame([
        {"game_id": 1000 + i, "season": "20252026", "game_type": 2,
         "date": base + pd.Timedelta(days=d), "team": t, "goalie": g, "name": f"G{g}",
         "started": s, "toi": 3600, "xg_faced": x, "ga": ga}
        for i, (d, t, g, s, x, ga) in enumerate(rows)
    ])


def test_goalie_games_from_real_fixture(stub_score):
    def load(gid, kind):
        path = FIXTURES / f"gamecenter__{gid}__{kind}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    recs = [cache.build_record(load(g, "play-by-play"), load(g, "boxscore"))
            for g in (2026020286, 2026020298)]
    gg = adjust.goalie_games(recs, {})
    assert list(gg.columns) == adjust.GG_COLUMNS
    for rec in recs:
        rows = gg[(gg["game_id"] == rec["id"]) & gg["started"]]
        assert len(rows) == 2
        goals = sum(1 for s in rec["shots"] if s["ev"] == "goal" and not s["empty_net"])
        assert rows["ga"].sum() == goals
        assert (rows["xg_faced"] > 0).all()
        box_team = {g["id"]: g["team"] for g in rec["goalies"]}
        game_rows = gg[gg["game_id"] == rec["id"]]
        for r in game_rows.itertuples():
            assert r.team == box_team[r.goalie]
        for team in set(game_rows["team"]):
            opp_goals = sum(1 for s in rec["shots"]
                            if s["team"] != team and s["ev"] == "goal" and not s["empty_net"])
            assert game_rows[game_rows["team"] == team]["ga"].sum() == opp_goals


def test_delta_formula_and_shrinkage(unit_weights):
    gr = adjust.goalie_ratings(
        gg_rows([(0, "A", 1, True, 100.0, 90), (1, "B", 2, True, 10.0, 9)]), ASOF, P
    )
    assert gr.delta[1] == pytest.approx(10 / 160)
    assert gr.delta[2] == pytest.approx(1 / 70)
    assert gr.gsax_per60[1] == 10.0


def test_delta_shrinks_weighted_sums_with_real_weights():
    # one row, weight w: (w*xg - w*ga) / (w*xg + K); K is not scaled by w
    gr = adjust.goalie_ratings(gg_rows([(0, "A", 1, True, 100.0, 90)]), date(2025, 11, 2), P)
    w = 0.5 ** (1 / 40)
    assert gr.delta[1] == pytest.approx(10 * w / (100 * w + 60))


def test_delta_ignores_rows_on_or_after_asof():
    gg = gg_rows([(0, "A", 1, True, 100.0, 90), (30, "A", 1, True, 100.0, 200)])
    gr = adjust.goalie_ratings(gg, date(2025, 11, 20), P)
    gr2 = adjust.goalie_ratings(gg.iloc[:1], date(2025, 11, 20), P)
    assert gr.delta == gr2.delta
    assert adjust.goalie_ratings(gg, date(2025, 11, 1), P).delta == {}


def test_team_delta_is_xg_weighted_mean(unit_weights):
    gg = gg_rows([(0, "A", 1, True, 300.0, 282), (1, "A", 2, True, 100.0, 106)])
    gr = adjust.goalie_ratings(gg, ASOF, P)
    d1, d2 = gr.delta[1], gr.delta[2]
    assert d1 > 0 > d2
    assert gr.team_delta["A"] == pytest.approx(0.75 * d1 + 0.25 * d2)


def test_mult_formula_and_direction():
    gr = GoalieRatings(delta={1: 0.06, 2: 0.10}, team_delta={"A": 0.02})
    m1 = adjust.goalie_mult([(1, 1.0)], "A", gr, P)
    assert m1 == pytest.approx(1 - 0.6 * 0.04)
    assert m1 == pytest.approx(0.976)
    assert adjust.goalie_mult([(2, 1.0)], "A", gr, P) < m1


def test_mult_is_clamped():
    gr = GoalieRatings(delta={1: 0.5, 2: -0.5}, team_delta={"A": 0.0})
    assert adjust.goalie_mult([(1, 1.0)], "A", gr, P) == pytest.approx(0.90)
    assert adjust.goalie_mult([(2, 1.0)], "A", gr, P) == pytest.approx(1.10)


def test_mult_is_expectation_over_starters():
    gr = GoalieRatings(delta={1: 0.06, 2: -0.04}, team_delta={"A": 0.02})
    ma = adjust.goalie_mult([(1, 1.0)], "A", gr, P)
    mb = adjust.goalie_mult([(2, 1.0)], "A", gr, P)
    assert adjust.goalie_mult([(1, 0.7), (2, 0.3)], "A", gr, P) == pytest.approx(
        0.7 * ma + 0.3 * mb
    )


def test_mult_is_one_without_data_or_when_disabled():
    gr = GoalieRatings(delta={1: 0.06}, team_delta={"A": 0.0})
    assert adjust.goalie_mult([], "A", gr, P) == 1.0
    assert adjust.goalie_mult([(1, 1.0)], "A", gr, ModelParams(use_goalie=False)) == 1.0


def test_unknown_goalie_counts_as_league_average():
    gr = GoalieRatings(delta={1: 0.06}, team_delta={"A": 0.02})
    assert adjust.goalie_mult([(99, 1.0)], "A", gr, P) == pytest.approx(1 - 0.6 * (0 - 0.02))


def _season(starts):
    """One game per day for team A from 2025-11-01, started by the listed goalies."""
    return gg_rows([(i, "A", g, True, 28.0, 2) for i, g in enumerate(starts)])


def test_projected_primary_is_most_starts_in_last_10():
    # the six old starts by goalie 3 fall outside the last 10 games
    gg = _season([3] * 6 + [1, 1, 2, 1, 2, 1, 1, 2, 1, 1])
    assert adjust.project_starters(gg, "A", date(2025, 11, 20)) == [(1, 0.7), (2, 0.3)]


def test_back_to_back_swaps_to_backup():
    gg = _season([2, 1, 2, 1, 1, 1])  # last game 2025-11-06 started by the primary (1)
    assert adjust.project_starters(gg, "A", date(2025, 11, 7)) == [(2, 0.7), (1, 0.3)]
    assert adjust.project_starters(gg, "A", date(2025, 11, 8)) == [(1, 0.7), (2, 0.3)]


def test_no_swap_when_backup_started_yesterday():
    gg = _season([1, 1, 1, 2])
    assert adjust.project_starters(gg, "A", date(2025, 11, 5)) == [(1, 0.7), (2, 0.3)]


def test_single_goalie_gets_probability_one():
    gg = _season([1, 1, 1])
    assert adjust.project_starters(gg, "A", date(2025, 11, 4)) == [(1, 1.0)]
    assert adjust.project_starters(gg, "A", date(2025, 11, 20)) == [(1, 1.0)]


def test_tie_breaks_by_most_recent_start():
    gg = _season([1, 2, 1, 2])
    assert adjust.project_starters(gg, "A", date(2025, 11, 20)) == [(2, 0.7), (1, 0.3)]


def test_goalie_block_fields():
    gr = GoalieRatings(delta={1: 0.06}, team_delta={"A": 0.02}, gsax_per60={1: 0.4},
                       names={1: "Netminder"})
    b = adjust.goalie_block([(1, 0.7), (2, 0.3)], "A", gr, P)
    assert set(b) == {"id", "name", "status", "gsax_per60", "p_start", "mult"}
    assert b["status"] == "projected"
    assert b["id"] == 1 and b["name"] == "Netminder" and b["p_start"] == 0.7
    assert b["mult"] == pytest.approx(adjust.goalie_mult([(1, 0.7), (2, 0.3)], "A", gr, P))
    assert b["mult"] != pytest.approx(adjust.goalie_mult([(1, 1.0)], "A", gr, P))


def test_goalie_block_none_for_new_team():
    gg = _season([1, 1])
    starters = adjust.project_starters(gg, "NEW", date(2025, 11, 20))
    assert starters == []
    assert adjust.goalie_block(starters, "NEW", GoalieRatings(), P) is None


def test_synthetic_goalie_games_recover_skill():
    tg = make_team_games(seasons=("20252026",), games_per_team=60)
    gg = make_goalie_games(tg, deltas={"A": (0.10, 0.10), "B": (-0.10, -0.10)})
    assert len(gg) == len(tg)
    gr = adjust.goalie_ratings(gg, date(2026, 6, 1), P)
    assert gr.team_delta["A"] > 0.01 and gr.team_delta["B"] < -0.01
