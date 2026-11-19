import json
from datetime import date

import numpy as np
import pytest

from pipeline.adjust import GoalieRatings
from pipeline.config import ModelParams
from pipeline.metrics import score
from pipeline.predict import predict_game
from pipeline.ratings import PeriodShares, Ratings

P = ModelParams()
SP = {"league_period_share": [0.3, 0.35, 0.35], "en_prob": {1: 0.3, 2: 0.15}}
PS = PeriodShares([0.3, 0.35, 0.35], {}, {})
BASE = float(np.exp(1.0))  # intercept 1.0, no team effects


def team(abbr, i):
    return {"id": i, "abbr": abbr, "name": abbr, "logo": f"{abbr}.svg", "logo_dark": None}


GAME = {"id": 2025020321, "season": "20252026", "game_type": 2, "nhl_date": "2025-11-15",
        "start_utc": "2025-11-16T00:00:00Z", "state": "FUT",
        "home": team("CAR", 12), "away": team("SEA", 55)}


def ratings(**kw):
    args = {"intercept": 1.0, "n_games": {"CAR": 20, "SEA": 20}}
    args.update(kw)
    return Ratings(**args)


def run(r=None, last_played=None, gr=None, starters=None, game=GAME, p=P):
    return predict_game(game, r or ratings(), PS, SP, p, last_played or {},
                        gr or GoalieRatings(), starters or {})


def factor(g, key):
    return next(f for f in g["factors"] if f["key"] == key)


def test_predict_game_matches_contract_keys():
    g = run(ratings(home=0.05))
    assert g["state"] == "upcoming"
    assert [f["key"] for f in g["factors"]] == ["base", "home_ice", "rest", "goalie"]
    assert [f["unit"] for f in g["factors"]] == ["goals", "mult", "mult", "mult"]
    assert [f["label"] for f in g["factors"]] == [
        "Joukkuevahvuus", "Kotietu", "Lepo / back-to-back", "Maalivahti"]
    assert all(g[k] is None for k in
               ("lineup_changes", "players", "insights", "odds"))
    assert "form" not in g and "season_compare" not in g and "h2h" not in g
    assert g["xg"]["home"] == round(BASE * np.exp(0.05), 2)
    assert g["home"] == {"id": 12, "abbr": "CAR", "name": "Carolina Hurricanes",
                         "logo": "CAR.svg", "logo_dark": None}
    assert g["prob"]["confidence"] == "normal"
    assert "goal_dist" not in g and len(g["periods"]) == 3
    json.dumps(g)


def test_factors_multiply_to_lambda():
    r = ratings(home=0.05, b2b_att=-0.04, b2b_def=0.03)
    g = run(r, last_played={"CAR": date(2025, 11, 14), "SEA": date(2025, 11, 14)})
    f = {k: factor(g, k) for k in ("base", "home_ice", "rest", "goalie")}
    product = f["base"]["home"] * f["home_ice"]["home"] * f["rest"]["home"] * f["goalie"]["home"]
    assert product == pytest.approx(g["_internal"]["lam_home"], abs=1e-6)
    assert f["base"]["home"] == pytest.approx(BASE, abs=1e-9)
    assert f["rest"]["home"] == pytest.approx(np.exp(-0.04 + 0.03))


def test_goalie_factor_scales_opponent_lambda():
    # mult = 1 - goalie_weight * (delta - team_delta); a good goalie lowers the other lambda
    gr = GoalieRatings(delta={1: 0.05 / 0.6}, team_delta={"SEA": 0.0}, names={1: "G"})
    plain = run()
    g = run(gr=gr, starters={"SEA": [(1, 1.0)]})
    assert factor(g, "goalie")["home"] == pytest.approx(0.95)
    assert factor(g, "goalie")["away"] == 1.0
    assert g["xg"]["home"] == pytest.approx(plain["xg"]["home"] * 0.95, abs=0.006)
    assert g["xg"]["away"] == plain["xg"]["away"]


def test_goalies_block_present_per_team():
    gr = GoalieRatings(delta={1: 0.0}, names={1: "Keeper"}, gsax_per60={1: 0.3})
    g = run(gr=gr, starters={"CAR": [(1, 0.7)]})
    assert g["goalies"]["home"]["status"] == "projected"
    assert g["goalies"]["home"]["id"] == 1 and g["goalies"]["home"]["p_start"] == 0.7
    assert g["goalies"]["away"] is None


def test_factor_product_is_clamped():
    r = ratings(home=float(np.log(1.1)), b2b_def=float(np.log(1.1)))
    gr = GoalieRatings(delta={1: -0.5}, team_delta={"SEA": 0.0})  # clamps to 1.10
    g = run(r, last_played={"SEA": date(2025, 11, 14)}, gr=gr, starters={"SEA": [(1, 1.0)]})
    assert g["xg"]["home"] == round(BASE * 1.25, 2)
    prod = 1.0
    for k in ("home_ice", "rest", "goalie"):
        prod *= factor(g, k)["home"]
    assert prod == pytest.approx(1.25, abs=1e-9)
    assert factor(g, "home_ice")["away"] == 1.0


def test_b2b_only_when_played_yesterday():
    r = ratings(b2b_att=-0.1)
    assert factor(run(r, last_played={"CAR": date(2025, 11, 13)}), "rest")["home"] == 1.0
    assert factor(run(r, last_played={"CAR": date(2025, 11, 14)}), "rest")["home"] \
        == pytest.approx(np.exp(-0.1))


def test_unknown_team_is_low_confidence_and_average():
    game = dict(GAME, home=team("XXX", 99), away=team("YYY", 98))
    g = run(game=game)
    assert g["prob"]["confidence"] == "low"
    assert g["xg"]["home"] == round(BASE, 2) == g["xg"]["away"]
    assert g["home"]["name"] == "XXX"


def test_low_confidence_when_one_team_has_few_games():
    assert run(ratings(n_games={"CAR": 20, "SEA": 9}))["prob"]["confidence"] == "low"


def test_predict_is_deterministic():
    assert run() == run()


def test_metrics_perfect_and_coinflip():
    y = np.array([1, 0, 1, 0])
    perfect = score(y.astype(float), y, 0.5)
    assert perfect["brier"] == 0 and perfect["accuracy"] == 1.0
    flip = score(np.full(4, 0.5), y, 0.5)
    assert flip["brier"] == pytest.approx(0.25)
    assert flip["logloss"] == pytest.approx(np.log(2))


def test_metrics_baseline_uses_base_rate():
    y = np.array([1, 0, 1, 1, 0])
    m = score(np.full(5, 0.6), y, 0.54)
    assert m["brier_base"] == pytest.approx(np.mean((0.54 - y) ** 2))
    assert m["logloss_base"] == pytest.approx(
        -np.mean(y * np.log(0.54) + (1 - y) * np.log(0.46)))


def test_metrics_logloss_clips_extremes():
    m = score(np.array([1.0, 0.0]), np.array([0, 1]), 0.5)
    assert np.isfinite(m["logloss"])


def test_calibration_bins_cover_all_rows():
    p = np.array([0.0, 0.04, 0.05, 0.5, 0.52, 0.99, 1.0])
    y = np.array([0, 0, 1, 1, 0, 1, 1])
    m = score(p, y, 0.5)
    assert sum(b["n"] for b in m["calibration"]) == len(p)
    top = m["calibration"][-1]
    assert (top["lo"], top["hi"], top["n"]) == (0.95, 1.0, 2)
    assert top["y_mean"] == 1.0


def test_bin_edge_is_robust_to_float_error():
    m = score(np.array([0.30, 0.3499]), np.array([1, 0]), 0.5)
    assert [(b["lo"], b["n"]) for b in m["calibration"]] == [(0.3, 2)]


def synthetic_pin(teams=("CAR", "SEA")):
    from pipeline import players
    from pipeline.config import PlayerParams

    slots = [{"id": 100 + i, "name": f"S{i}", "pos": "D" if i >= 12 else "C",
              "toi_proj": 18.0 - i * 0.5} for i in range(18)]
    rates = players._fallback_rates()
    lineups = {t: slots for t in teams}
    base = {s["id"]: (0.3, 0.1) for s in slots}
    return players.PlayerInputs(rates, lineups, base, PlayerParams())


def test_predict_game_with_players_fills_block():
    g = predict_game(GAME, ratings(), PS, SP, P, {}, GoalieRatings(), {}, synthetic_pin())
    assert len(g["players"]["home"]["top"]) >= 5
    assert g["players"]["away"]["team"] == "SEA"
    assert "credited_dist" in g["_internal"] and g["_internal"]["players_log"]["teams"]
    json.dumps(g["players"])


def test_predict_game_without_pin_keeps_null():
    g = run()
    assert g["players"] is None and g["_internal"]["players_log"] is None
    assert "credited_dist" in g["_internal"]


def test_player_failure_gives_null(monkeypatch):
    from pipeline import players

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(players, "game_players", boom)
    g = predict_game(GAME, ratings(), PS, SP, P, {}, GoalieRatings(), {}, synthetic_pin())
    assert g["players"] is None and g["_internal"]["players_log"] is None
    assert g["prob"]["home_win"] > 0


def test_game_lambdas_default_unchanged():
    from pipeline.predict import Mults, game_lambdas

    r = ratings(home=0.05, b2b_att=-0.04)
    args = (r, "CAR", "SEA", True, False, [], [], GoalieRatings(), P)
    a, b = game_lambdas(*args), game_lambdas(*args, mults=Mults())
    assert a == b
    assert a["lam_h"] == pytest.approx(BASE * np.exp(0.05) * np.exp(-0.04))
    assert a["lam_a"] == pytest.approx(BASE)
    assert a["lineup_h"] == a["lineup_a"] == a["h2h_h"] == a["h2h_a"] == 1.0


def test_mults_enter_clamp():
    from pipeline.predict import Mults, game_lambdas

    lm = game_lambdas(ratings(), "CAR", "SEA", False, False, [], [], GoalieRatings(), P,
                      mults=Mults(att_h=1.3, def_h=0.9, att_a=1.1, def_a=1.0, h2h_a=1.02))
    assert lm["lineup_h"] == pytest.approx(1.3)
    assert lm["m_h"] == pytest.approx(1.25)
    assert lm["lineup_a"] == pytest.approx(1.1 * 0.9)
    assert lm["m_a"] == pytest.approx(1.1 * 0.9 * 1.02)
    assert lm["lam_a"] == pytest.approx(BASE * 1.1 * 0.9 * 1.02)


def test_factors_multiply_to_applied():
    from pipeline.config import LineupParams
    from pipeline.predict import Mults

    r = ratings(home=0.1)
    m = Mults(att_h=1.15, def_a=1.1, att_a=0.95)
    g = predict_game(GAME, r, PS, SP, P, {}, GoalieRatings(), {},
                     mults=m, lp=LineupParams(use_lineup=True, use_h2h=False))
    keys = [f["key"] for f in g["factors"]]
    assert keys == ["base", "home_ice", "rest", "goalie", "lineup"]
    lab = factor(g, "lineup")
    assert lab["label"] == "Poissaolot ja kaupat" and lab["unit"] == "mult"
    for side, lam in (("home", "lam_home"), ("away", "lam_away")):
        prod = float(np.prod([f[side] for f in g["factors"]]))
        assert prod == pytest.approx(g["_internal"][lam], rel=1e-9)
    # home clamp binds (1.105 * 1.265 > 1.25); away is the lineup multiplier unclamped
    assert g["_internal"]["lam_home"] == pytest.approx(BASE * 1.25)
    assert g["_internal"]["lam_away"] == pytest.approx(BASE * 0.95)
    m2 = Mults(att_h=1.15, def_a=1.1, att_a=0.95, h2h_h=1.03, h2h_a=0.97)
    both = predict_game(GAME, r, PS, SP, P, {}, GoalieRatings(), {},
                        mults=m2, lp=LineupParams(use_lineup=True, use_h2h=True))
    assert [f["key"] for f in both["factors"]][-2:] == ["lineup", "h2h"]
    assert factor(both, "h2h")["label"] == "Keskinäiset"
    prod = float(np.prod([f["away"] for f in both["factors"]]))
    assert prod == pytest.approx(both["_internal"]["lam_away"], rel=1e-9)
    assert both["_internal"]["lam_away"] == pytest.approx(BASE * 0.95 * 0.97)
