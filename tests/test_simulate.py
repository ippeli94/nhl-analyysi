import time
from dataclasses import replace

import numpy as np

from pipeline.config import ModelParams
from pipeline.simulate import simulate

P = ModelParams()
SP = {"league_period_share": [0.3, 0.35, 0.35], "en_prob": {1: 0.3, 2: 0.15}}


def lam(total):
    return np.array([0.3, 0.35, 0.35]) * total


def run(h=3.0, a=3.0, p=P, seed=1, sp=SP, n=20000):
    return simulate(lam(h), lam(a), sp, p, seed, n)


def test_same_seed_same_result():
    a, b = lam(3.2), lam(2.8)
    assert simulate(a, b, SP, P, 7, 2000) == simulate(a, b, SP, P, 7, 2000)


def test_different_seed_differs():
    a, b = lam(3.2), lam(2.8)
    assert simulate(a, b, SP, P, 1, 2000) != simulate(a, b, SP, P, 2, 2000)


def test_probabilities_sum():
    r = run(3.4, 2.9)
    pr = r["prob"]
    assert abs(pr["home_win"] + pr["away_win"] - 1) < 1e-3
    assert abs(pr["reg_home"] + pr["reg_draw"] + pr["reg_away"] - 1) < 1e-3
    assert abs(np.sum(r["score"]["matrix"]) - 1) < 1e-3
    assert np.shape(r["score"]["matrix"]) == (8, 8)
    assert abs(sum(r["goal_dist"]["home"]) - 1) < 1e-3
    assert len(r["goal_dist"]["away"]) == 11


def test_equal_teams_are_symmetric():
    # with draw_kappa 1.0 the 3.0 v 3.0 draw share is ~0.167 (plain Poisson); the
    # 0.18-0.28 band holds for a tuned kappa > 1, so test with 1.2.
    pr = run(3.0, 3.0, replace(P, draw_kappa=1.2))["prob"]
    assert abs(pr["home_win"] - 0.5) < 0.02
    assert 0.18 < pr["reg_draw"] < 0.28


def test_stronger_team_wins_more_but_not_absurdly():
    assert 0.58 < run(3.6, 2.6)["prob"]["home_win"] < 0.72


def test_no_final_score_is_a_tie():
    m = run(3.0, 3.0)["score"]["matrix"]
    assert all(m[i][i] == 0 for i in range(7))


def test_kappa_raises_draw_share():
    base = run(3.0, 3.0, replace(P, draw_kappa=1.0))["prob"]
    more = run(3.0, 3.0, replace(P, draw_kappa=1.3))["prob"]
    assert more["reg_draw"] > base["reg_draw"]
    assert abs(more["reg_home"] + more["reg_draw"] + more["reg_away"] - 1) < 1e-3


def test_uncertainty_pulls_towards_half():
    flat = run(3.6, 2.6, replace(P, sigma_pace=0.0, sigma_team=0.0))["prob"]["home_win"]
    wide = run(3.6, 2.6, replace(P, sigma_pace=0.25, sigma_team=0.25))["prob"]["home_win"]
    assert abs(wide - 0.5) < abs(flat - 0.5)


def test_periods_block():
    a, b = lam(3.3), lam(2.7)
    per = simulate(a, b, SP, P, 3, 5000)["periods"]
    assert [x["n"] for x in per] == [1, 2, 3]
    for i, x in enumerate(per):
        assert x["xg_home"] == round(a[i], 2) and x["xg_away"] == round(b[i], 2)
        assert abs(x["p_home_wins_period"] + x["p_away_wins_period"] + x["p_draw"] - 1) < 1e-3


def test_spread_complements():
    s = run(3.4, 2.9)["score"]["spread"]
    assert set(s) == {"home_-1.5", "away_+1.5", "away_-1.5", "home_+1.5"}
    assert abs(s["home_-1.5"] + s["away_+1.5"] - 1) < 1e-3
    assert abs(s["away_-1.5"] + s["home_+1.5"] - 1) < 1e-3


def test_top_scores_sorted_and_most_likely_first():
    sc = run(3.4, 2.9)["score"]
    top = sc["top"]
    assert len(top) == 5
    assert [t["p"] for t in top] == sorted((t["p"] for t in top), reverse=True)
    assert sc["most_likely"] == top[0]
    assert set(top[0]) == {"home", "away", "p"}
    assert set(sc["total"]["over"]) == {"5.5", "6.5"}


def test_zero_en_prob_matches_plain_total():
    p = replace(P, sigma_pace=0.0, sigma_team=0.0, draw_kappa=1.0)
    sp = {"league_period_share": [0.3, 0.35, 0.35], "en_prob": {1: 0.0, 2: 0.0}}
    r = run(3.0, 2.5, p, sp=sp)
    assert abs(r["score"]["total"]["mean"] - (5.5 + r["prob"]["reg_draw"])) < 0.05


def test_fast_and_plain_json_types():
    t = time.time()
    r = run()
    assert time.time() - t < 1.5

    def walk(x):
        if isinstance(x, dict):
            assert all(isinstance(k, str) for k in x)
            [walk(v) for v in x.values()]
        elif isinstance(x, list):
            [walk(v) for v in x]
        else:
            assert type(x) in (int, float, str)

    walk(r)
