import json
from dataclasses import fields

import pandas as pd
import pytest

from pipeline import adjust, backtest, config, predict, ratings
from pipeline.backtest import gate, kappa_from, run_backtest, tune
from pipeline.config import ModelParams
from tests.synth import make_goalie_games, make_team_games

SEASONS = ("20232024", "20242025", "20252026")
STRENGTHS = {"A": 0.3, "B": -0.3, "C": 0.2, "D": -0.2, "E": 0.1, "F": -0.1, "G": 0.3, "H": -0.3}
P = ModelParams()
SIMS = 300
GATE_SEASON = "20252026"


def league(games_per_team=40, strengths=STRENGTHS, home_adv=0.05, seed=1):
    return make_team_games(
        seasons=SEASONS, games_per_team=games_per_team, strengths=strengths,
        home_adv=home_adv, seed=seed,
    )


@pytest.fixture(scope="module")
def tg():
    return league(games_per_team=60)


@pytest.fixture(scope="module")
def small():
    return league(games_per_team=14)


def n_games(frame, season=GATE_SEASON):
    return int(((frame["season"] == season) & frame["is_home"]).sum())


def test_backtest_never_sees_the_future(small, monkeypatch):
    seen = []
    real = ratings.fit

    def spy(hist, asof, p, season=None):
        assert (hist["date"] < pd.Timestamp(asof)).all()
        assert season == GATE_SEASON
        seen.append(asof)
        return real(hist, asof, p, season=season)

    monkeypatch.setattr(ratings, "fit", spy)
    run_backtest(small, GATE_SEASON, P, n_sims=SIMS)
    games = small[(small["season"] == GATE_SEASON) & small["is_home"]]
    expected = sorted({d.date() for d in games["date"]})
    assert seen == expected  # one fit per game date, asof is the date predicted


def test_backtest_beats_baseline_on_synthetic_league(tg):
    assert gate(run_backtest(tg, GATE_SEASON, P, n_sims=SIMS)["metrics"])


def test_backtest_does_not_beat_baseline_on_noise():
    noise = league(games_per_team=60, strengths={}, home_adv=0.0, seed=5)
    m = run_backtest(noise, GATE_SEASON, P, n_sims=SIMS)["metrics"]
    assert m["brier"] >= m["brier_base"] - 0.002


def test_rows_one_per_game(small):
    res = run_backtest(small, GATE_SEASON, P, n_sims=SIMS)
    rows = res["rows"]
    assert list(rows.columns) == backtest.ROW_COLUMNS
    assert len(rows) == n_games(small)
    assert rows["game_id"].is_unique
    assert rows["p_home"].between(0, 1).all()
    assert res["metrics"]["n"] == len(rows)
    one = small[(small["game_id"] == rows["game_id"].iloc[0]) & small["is_home"]].iloc[0]
    assert rows["y_home"].iloc[0] == float(one["won"])
    assert rows["total_actual"].iloc[0] == (
        one["score_reg"] + one["opp_score_reg"] + (one["decided"] != "REG")
    )


def test_goalie_mode_none_equals_run_without_goalies(small):
    gg = make_goalie_games(small)
    a = run_backtest(small, GATE_SEASON, P, n_sims=SIMS)["rows"]
    b = run_backtest(small, GATE_SEASON, P, n_sims=SIMS, gg=gg, goalie_mode="none")["rows"]
    pd.testing.assert_frame_equal(a, b)


def test_goalie_mode_needs_goalie_games(small):
    with pytest.raises(ValueError):
        run_backtest(small, GATE_SEASON, P, n_sims=SIMS, goalie_mode="actual")


def test_actual_mode_uses_boxscore_starter_with_probability_one(small, monkeypatch):
    gg = make_goalie_games(small)
    calls = []
    real = predict.goalie_mult

    def spy(starters, team, gr, p):
        calls.append((starters, team))
        return real(starters, team, gr, p)

    monkeypatch.setattr(predict, "goalie_mult", spy)
    run_backtest(small, GATE_SEASON, P, n_sims=SIMS, gg=gg, goalie_mode="actual")

    starter = {(r.game_id, r.team): r.goalie for r in gg.itertuples() if r.started}
    games = small[(small["season"] == GATE_SEASON) & small["is_home"]]
    games = games.sort_values("date", kind="stable")
    expected = []
    for g in games.itertuples():  # predict computes the away goalie's call first
        expected.append(([(starter[(g.game_id, g.opp)], 1.0)], g.opp))
        expected.append(([(starter[(g.game_id, g.team)], 1.0)], g.team))
    assert calls == expected


def test_projected_mode_and_ratings_use_only_earlier_games(small, monkeypatch):
    gg = make_goalie_games(small)
    seen = {"proj": 0, "rat": 0}
    real_proj, real_rat = adjust.project_starters, adjust.goalie_ratings

    def proj(g, team, nhl_date, season=None):
        assert (g["date"] < pd.Timestamp(nhl_date)).all()
        assert season == GATE_SEASON
        seen["proj"] += 1
        return real_proj(g, team, nhl_date, season=season)

    def rat(g, asof, p, season=None):
        assert (g["date"] < pd.Timestamp(asof)).all()
        assert season == GATE_SEASON
        seen["rat"] += 1
        return real_rat(g, asof, p, season=season)

    monkeypatch.setattr(adjust, "project_starters", proj)
    monkeypatch.setattr(adjust, "goalie_ratings", rat)
    run_backtest(small, GATE_SEASON, P, n_sims=SIMS, gg=gg, goalie_mode="projected")
    assert seen["proj"] == 2 * n_games(small)
    assert seen["rat"] == small[small["season"] == GATE_SEASON]["date"].nunique()

    seen.update(proj=0, rat=0)  # the ratings of actual mode are leak-free too
    run_backtest(small, GATE_SEASON, P, n_sims=SIMS, gg=gg, goalie_mode="actual")
    assert seen["proj"] == 0 and seen["rat"] > 0


def test_tune_returns_grid_values(monkeypatch):
    monkeypatch.setattr(backtest, "TUNE_SIMS", 60)
    small = league(games_per_team=10)
    best = tune(small, "20242025", P)
    assert best.half_life_days in backtest.HALF_LIVES
    assert best.alpha in backtest.ALPHAS
    assert best.sigma_pace == best.sigma_team
    assert best.sigma_team in backtest.SIGMAS
    assert best.draw_kappa > 0
    assert best.goalie_weight == P.goalie_weight  # no goalie games, not tuned


def test_tune_picks_goalie_weight_from_grid(monkeypatch):
    monkeypatch.setattr(backtest, "TUNE_SIMS", 60)
    small = league(games_per_team=10)
    best = tune(small, "20242025", P, gg=make_goalie_games(small))
    assert best.goalie_weight in backtest.GOALIE_WEIGHTS


def test_kappa_from_closed_form():
    rows = pd.DataFrame({"y_reg_draw": [1.0] * 22 + [0.0] * 78, "p_reg_draw": [0.20] * 100})
    assert kappa_from(rows) == pytest.approx(0.22 * 0.80 / (0.20 * 0.78))


def test_gate_requires_both_metrics():
    assert not gate({"brier": .24, "brier_base": .25, "logloss": .70, "logloss_base": .69})
    assert not gate({"brier": .26, "brier_base": .25, "logloss": .68, "logloss_base": .69})
    assert gate({"brier": .24, "brier_base": .25, "logloss": .68, "logloss_base": .69})


ARGS = ["--tune-season", "20242025", "--gate-season", GATE_SEASON]


def read_params(root):
    return json.loads((root / "model/params.json").read_text(encoding="utf-8"))


def test_cli_writes_params_with_gate(root, small, monkeypatch):
    real = backtest.run_backtest
    monkeypatch.setattr(backtest, "load_team_games", lambda *a, **k: small)
    monkeypatch.setattr(backtest, "load_goalie_games", lambda *a, **k: make_goalie_games(small))
    monkeypatch.setattr(backtest, "tune", lambda tg, season, base, gg=None: base)
    monkeypatch.setattr(
        backtest, "run_backtest", lambda *a, **k: real(*a, **{**k, "n_sims": SIMS})
    )
    code = backtest.main(ARGS)
    params = read_params(root)
    g = params["gate"]
    assert isinstance(g["passed"], bool)
    assert code == (0 if g["passed"] else 1)
    assert g["variant"] == "full" and g["season"] == GATE_SEASON
    assert g["n"] == n_games(small)
    assert set(g["goalie_modes"]) == {"none", "actual", "projected"}
    assert g["goalie_mode"] in ("none", "projected")
    assert {f.name for f in fields(ModelParams)} <= set(params)
    assert (root / f"model/backtest_{GATE_SEASON}.json").exists()
    report = (root / "reports/backtest.md").read_text(encoding="utf-8")
    assert len(report.splitlines()) < 30


def stub_modes(monkeypatch, root, none_ll, projected_ll):
    def metrics_for(ll):
        return {"n": 100, "brier": 0.24, "brier_base": 0.25, "logloss": ll,
                "logloss_base": 0.70, "accuracy": 0.56, "calibration": []}

    def fake(tg, season, p, n_sims=2000, gg=None, goalie_mode="none"):
        ll = projected_ll if goalie_mode == "projected" else none_ll
        diag = {"home_mult_mean": 1.05, "lambda_mean": 3.0, "goalie_clamp_share": 0.0}
        return {"rows": None, "metrics": metrics_for(ll), "diag": diag}

    monkeypatch.setattr(backtest, "load_team_games", lambda *a, **k: None)
    monkeypatch.setattr(backtest, "load_goalie_games", lambda *a, **k: None)
    monkeypatch.setattr(backtest, "tune", lambda tg, season, base, gg=None: base)
    monkeypatch.setattr(backtest, "run_backtest", fake)


def test_cli_disables_goalie_when_projected_is_worse(root, monkeypatch):
    stub_modes(monkeypatch, root, none_ll=0.68, projected_ll=0.69)
    assert backtest.main(ARGS) == 0
    params = read_params(root)
    assert params["use_goalie"] is False
    assert params["gate"]["goalie_mode"] == "none"
    assert params["gate"]["logloss"] == 0.68
    assert config.load_params().use_goalie is False


def test_cli_keeps_goalie_when_projected_is_not_worse(root, monkeypatch):
    stub_modes(monkeypatch, root, none_ll=0.69, projected_ll=0.68)
    assert backtest.main(ARGS) == 0
    params = read_params(root)
    assert params["use_goalie"] is True
    assert params["gate"]["goalie_mode"] == "projected"


def test_cli_exit_code_one_when_gate_fails(root, monkeypatch):
    stub_modes(monkeypatch, root, none_ll=0.71, projected_ll=0.72)
    assert backtest.main(ARGS) == 1
    assert read_params(root)["gate"]["passed"] is False


def test_cli_variant_sets_params(root, monkeypatch):
    stub_modes(monkeypatch, root, none_ll=0.69, projected_ll=0.68)
    assert backtest.main([*ARGS, "--variant", "no_periods"]) == 0
    params = read_params(root)
    assert params["xg_weight"] == 0.0 and params["use_periods"] is False
    assert params["gate"]["variant"] == "no_periods"
