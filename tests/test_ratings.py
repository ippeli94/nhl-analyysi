from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline import ratings
from pipeline.config import ModelParams
from tests.synth import make_team_games

P = ModelParams()
STRENGTHS = {"A": 0.3, "H": -0.3}


def _rows(season, days, base):
    return pd.DataFrame(
        {
            "season": season,
            "game_type": 2,
            "date": [pd.Timestamp(base) + pd.Timedelta(days=d) for d in days],
        }
    )


def test_weight_halves_every_40_days():
    tg = _rows("20252026", [0, 40, 80], "2025-10-10")
    w = ratings.weights(tg, date(2025, 12, 31), P)
    assert w[1] / w[2] == pytest.approx(0.5)  # 40 days older than the newest
    assert w[0] / w[1] == pytest.approx(0.5)


def test_prev_season_weight_uses_fixed_gap_and_07():
    prev = _rows("20242025", [-5, 0], "2025-04-10")  # season ends 2025-04-10
    cur = _rows("20252026", [0], "2025-10-10")
    tg = pd.concat([prev, cur], ignore_index=True)
    w = ratings.weights(tg, date(2025, 10, 20), P)
    assert w[0] == pytest.approx(0.7 * 0.5 ** ((10 + 60 + 5) / 40))
    assert w[2] == pytest.approx(0.5 ** (10 / 40))


def test_two_seasons_back_gets_zero_weight():
    tg = pd.concat(
        [
            _rows("20232024", [0], "2024-03-01"),
            _rows("20242025", [0], "2025-03-01"),
            _rows("20252026", [0], "2025-10-10"),
        ],
        ignore_index=True,
    )
    w = ratings.weights(tg, date(2025, 10, 20), P)
    assert w[0] == 0.0 and w[1] > 0 and w[2] > 0


def test_weights_zero_on_or_after_asof():
    tg = _rows("20252026", [0, 5, 6], "2025-10-10")
    w = ratings.weights(tg, date(2025, 10, 15), P)
    assert w[0] > 0 and w[1] == 0 and w[2] == 0


def test_fit_ignores_rows_on_or_after_asof():
    tg = make_team_games(strengths=STRENGTHS)
    asof = date(2025, 12, 1)
    base = ratings.fit(tg, asof, P)
    poison = tg[tg["season"] == "20252026"].copy()
    poison["date"] = pd.Timestamp(asof) + pd.Timedelta(days=1)
    poison["target"] = 50.0
    poison["game_id"] += 10**6
    again = ratings.fit(pd.concat([tg, poison], ignore_index=True), asof, P)
    assert again.intercept == pytest.approx(base.intercept)
    assert again.attack == pytest.approx(base.attack)
    assert again.home == pytest.approx(base.home)


def test_fit_recovers_strength_order():
    tg = make_team_games(strengths=STRENGTHS, games_per_team=60)
    r = ratings.fit(tg, date(2026, 6, 1), P)
    assert r.attack["A"] > r.attack["H"]
    assert r.defense["A"] < r.defense["H"]
    assert 0 < r.home < 0.15


def test_base_lambda_plausible():
    tg = make_team_games(strengths=STRENGTHS)
    r = ratings.fit(tg, date(2026, 6, 1), P)
    for a in r.attack:
        for b in r.attack:
            if a != b:
                assert 1.5 < ratings.base_lambda(r, a, b) < 5.0


def test_unknown_team_gets_league_average():
    tg = make_team_games(strengths=STRENGTHS)
    r = ratings.fit(tg, date(2026, 6, 1), P)
    assert ratings.base_lambda(r, "NEW", "A") == pytest.approx(np.exp(r.intercept + r.defense["A"]))
    assert ratings.base_lambda(r, "A", "NEW") == pytest.approx(np.exp(r.intercept + r.attack["A"]))


def test_fit_with_under_20_rows_returns_intercept_only():
    tg = make_team_games(n_teams=4, games_per_team=4, seasons=("20252026",))
    r = ratings.fit(tg, date(2026, 6, 1), P)
    assert len(tg) < 20
    assert r.attack == {} and r.defense == {} and r.n_games == {}
    w = ratings.weights(tg, date(2026, 6, 1), P)
    assert r.intercept == pytest.approx(np.log(np.average(tg["target"], weights=w)))
    empty = ratings.fit(tg, date(2000, 1, 1), P)
    assert empty.intercept == pytest.approx(np.log(3.0))


def test_multipliers():
    r = ratings.Ratings(intercept=1.0, home=0.05, b2b_att=-0.04, b2b_def=0.02)
    assert ratings.home_mult(r) == pytest.approx(np.exp(0.05))
    assert ratings.rest_mult(r, True, False) == pytest.approx(np.exp(-0.04))
    assert ratings.rest_mult(r, True, True) == pytest.approx(np.exp(-0.02))
    assert ratings.rest_mult(r, False, False) == 1.0


def test_n_games_counts_current_season():
    tg = make_team_games(strengths=STRENGTHS, games_per_team=30)
    r = ratings.fit(tg, date(2026, 6, 1), P)
    assert r.n_games["A"] == 30


def _ps():
    return ratings.PeriodShares([0.30, 0.35, 0.35], {"A": [0.2, 0.4, 0.4]}, {"B": [0.3, 0.3, 0.4]})


def test_split_lambda_sums_to_lambda():
    split = ratings.split_lambda(3.1, "A", "B", _ps(), True)
    assert abs(split.sum() - 3.1) < 1e-9
    assert split[1] > split[0]


def test_split_lambda_without_periods_is_league_share():
    league_split = [0.9, 1.05, 1.05]
    np.testing.assert_allclose(ratings.split_lambda(3.0, "A", "B", _ps(), False), league_split)
    np.testing.assert_allclose(ratings.split_lambda(3.0, "NEW", "B", _ps(), True), league_split)


def test_period_shares_league_matches_data():
    ps = ratings.period_shares(make_team_games(), date(2026, 6, 1), P)
    np.testing.assert_allclose(ps.league, [0.30, 0.35, 0.35])


def test_period_shares_shrink_to_league_with_few_games():
    tg = make_team_games(seasons=("20252026",), games_per_team=40)
    z = tg[tg["team"] == "A"].head(2).copy()  # team Z: 2 games, raw p1 share 0.5
    z["team"] = "Z"
    z["target"] = 3.0
    z["target_p1"], z["target_p2"], z["target_p3"] = 1.5, 0.75, 0.75
    tg = pd.concat([tg, z], ignore_index=True)
    ps = ratings.period_shares(tg, date(2026, 6, 1), P)
    for k in range(3):
        assert abs(ps.scored["Z"][k] - ps.league[k]) < 0.02
    assert sum(ps.scored["Z"]) == pytest.approx(1.0)


def test_opener_with_explicit_season_uses_prev_season_weighting():
    tg = make_team_games(seasons=("20242025",), games_per_team=30, strengths=STRENGTHS)
    asof = date(2025, 10, 8)
    assert (tg["date"] < pd.Timestamp(asof)).all()
    w = ratings.weights(tg, asof, P, season="20252026")
    end = tg["date"].max()
    expected = 0.7 * 0.5 ** ((60 + (end - tg["date"]).dt.days.to_numpy()) / 40)
    np.testing.assert_allclose(w, expected)
    r = ratings.fit(tg, asof, P, season="20252026")
    assert r.attack["A"] > r.attack["H"]
    assert set(r.n_games) == set(r.attack) and all(v == 0 for v in r.n_games.values())
    ps = ratings.period_shares(tg, asof, P, season="20252026")
    assert ps.scored == {} and ps.league == pytest.approx([1 / 3] * 3)


def test_split_lambda_unknown_opponent_uses_league_allowed_share():
    ps = _ps()
    got = ratings.split_lambda(3.0, "A", "NEW", ps, True)
    scored = np.array(ps.scored["A"])
    np.testing.assert_allclose(got, 3.0 * scored / scored.sum())
