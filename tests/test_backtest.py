import json
from dataclasses import fields

import numpy as np
import pandas as pd
import pytest

from pipeline import adjust, backtest, config, predict, ratings
from pipeline.backtest import (
    draw_report,
    fit_draw_kappa,
    gate,
    kappa_from,
    reweight_draw,
    run_backtest,
    tune,
)
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


# ---- draw calibration (spec 15.3) ----

def test_rows_have_regulation_outcome_columns(small):
    rows = run_backtest(small, GATE_SEASON, P, n_sims=SIMS)["rows"]
    assert {"p_reg_home", "p_reg_away", "y_reg"} <= set(rows.columns)
    assert np.allclose(rows.p_reg_home + rows.p_reg_draw + rows.p_reg_away, 1, atol=2e-3)
    assert set(rows.y_reg) <= {0, 1, 2}
    assert (rows.y_reg == 1).mean() == rows.y_reg_draw.mean()


PH, PD, PA = np.array([0.45, 0.50]), np.array([0.17, 0.10]), np.array([0.38, 0.40])


def test_reweight_draw_identity_and_sum():
    h, d, a = reweight_draw(PH, PD, PA, 1.0)
    assert np.allclose((h, d, a), (PH, PD, PA))
    h, d, a = reweight_draw(PH, PD, PA, 1.6)
    assert np.allclose(h + d + a, 1) and (d > PD).all()


def const_rows(n_home, n_draw, n_away, p=(0.45, 0.17, 0.38)):
    y = [0] * n_home + [1] * n_draw + [2] * n_away
    n = len(y)
    return pd.DataFrame({
        "p_reg_home": [p[0]] * n, "p_reg_draw": [p[1]] * n, "p_reg_away": [p[2]] * n,
        "y_reg": y, "y_reg_draw": [float(v == 1) for v in y],
    })


def test_fit_draw_kappa_matches_closed_form_for_constant_probs():
    rows = const_rows(400, 247, 353)
    k = fit_draw_kappa(rows)
    assert abs(k - kappa_from(rows)) <= 0.005 and abs(k - 1.6015) < 0.006


def test_fit_draw_kappa_stays_on_grid():
    lo, hi, _ = config.KAPPA_GRID
    assert fit_draw_kappa(const_rows(0, 100, 0)) == pytest.approx(hi)
    assert fit_draw_kappa(const_rows(50, 0, 50)) == pytest.approx(lo)


def test_draw_report_fields():
    rows = const_rows(400, 247, 353)
    r = draw_report(rows, 1.0)
    assert set(r) == {"n", "p_mean", "y_mean", "gap", "logloss_1x2"}
    assert r["n"] == 1000 and r["p_mean"] == pytest.approx(0.17)
    assert r["gap"] == pytest.approx(r["p_mean"] - r["y_mean"])
    assert draw_report(rows, 1.6)["p_mean"] > 0.17


def test_tune_uses_fit_draw_kappa(monkeypatch):
    monkeypatch.setattr(backtest, "TUNE_SIMS", 60)
    monkeypatch.setattr(backtest, "fit_draw_kappa", lambda rows: 1.42)
    best = tune(league(games_per_team=10), "20242025", P)
    assert best.draw_kappa == 1.42


REFIT = ["--tune-season", "20242025", "--gate-season", GATE_SEASON,
         "--refit-draw", "20252026", "20262027"]


def refit_stub(monkeypatch, root, gate_draw_rate=0.17, ll=(0.68, 0.68), calls=None):
    """Stub the backtest: kappa-1 rows with a mean draw rate 0.24 vs `gate_draw_rate` simulated."""
    (root / "model").mkdir(exist_ok=True)
    (root / "model/params.json").write_text(json.dumps({
        "draw_kappa": 1.27, "use_goalie": True,
        "gate": {"passed": True, "goalie_mode": "projected", "variant": "full"},
    }), encoding="utf-8")
    seen = {}

    def fake(tg, season, p, n_sims=2000, gg=None, goalie_mode="none"):
        if calls is not None:
            calls.append((season, p.draw_kappa, goalie_mode))
        rows = const_rows(380, 240, 380, p=(0.415, gate_draw_rate, 0.415))
        seen[season] = rows
        m = {"n": 1000, "brier": 0.24, "brier_base": 0.25,
             "logloss": ll[0] if goalie_mode == "none" else ll[1],
             "logloss_base": 0.70, "accuracy": 0.56, "calibration": []}
        diag = {"home_mult_mean": 1.05, "lambda_mean": 3.0, "goalie_clamp_share": 0.0}
        return {"rows": rows, "metrics": m, "diag": diag}

    loaded = []
    monkeypatch.setattr(backtest, "load_team_games",
                        lambda seasons, *a, **k: loaded.append(list(seasons)))
    monkeypatch.setattr(backtest, "load_goalie_games", lambda *a, **k: None)
    monkeypatch.setattr(backtest, "run_backtest", fake)
    return loaded


def test_refit_writes_kappa_and_draw_block(root, monkeypatch):
    calls = []
    loaded = refit_stub(monkeypatch, root, calls=calls)
    assert backtest.main(REFIT) == 0
    params = read_params(root)
    expected = fit_draw_kappa(const_rows(380, 240, 380, p=(0.415, 0.17, 0.415)).pipe(
        lambda r: pd.concat([r, r])))
    assert params["draw_kappa"] == pytest.approx(expected)
    d = params["gate"]["draw"]
    assert d["fit_seasons"] == ["20252026", "20262027"]
    assert d["kappa"] == pytest.approx(expected)
    assert d["kappa_rate_match"] == pytest.approx(kappa_from(
        const_rows(380, 240, 380, p=(0.415, 0.17, 0.415))), rel=1e-6)
    assert set(d["seasons"]) == {"20242025", "20252026", "20262027"}
    assert set(d["seasons"]["20252026"]) == {"n", "p_mean", "y_mean", "gap", "logloss_1x2"}
    assert abs(d["seasons"]["20252026"]["gap"]) < config.DRAW_GAP_MAX
    # fitting runs: one per season, all at kappa 1.0 in the judged goalie mode
    fitting = [c for c in calls if c[1] == 1.0]
    assert sorted(c[0] for c in fitting) == ["20242025", "20252026", "20262027"]
    assert {c[2] for c in fitting} == {"projected"}
    # gate season: the three modes at the fitted kappa
    final = [c for c in calls if c[1] != 1.0]
    assert sorted(c[2] for c in final) == ["actual", "none", "projected"]
    assert all(c[0] == GATE_SEASON for c in final)
    # seasons loaded include the season before each requested season
    assert loaded[0] == ["20232024", "20242025", "20252026", "20262027"]
    # written file is still accepted by the loaders
    assert config.load_params().draw_kappa == pytest.approx(expected)
    assert config.load_gate()["draw"]["fit_seasons"] == ["20252026", "20262027"]
    assert (root / f"model/backtest_{GATE_SEASON}.json").exists()
    report = (root / "reports/backtest.md").read_text(encoding="utf-8")
    assert report.count("## Draw calibration") == 1
    assert "20262027" in report


def test_refit_report_section_is_replaced_not_duplicated(root, monkeypatch):
    refit_stub(monkeypatch, root)
    backtest.main(REFIT)
    backtest.main(REFIT)
    report = (root / "reports/backtest.md").read_text(encoding="utf-8")
    assert report.count("## Draw calibration") == 1


def test_refit_leaves_params_untouched_when_draw_gap_too_large(root, monkeypatch):
    # fitted kappa cannot reach the data: rows are all at the kappa grid ceiling
    refit_stub(monkeypatch, root, gate_draw_rate=0.02)
    before = (root / "model/params.json").read_bytes()
    assert backtest.main(REFIT) == 1
    assert (root / "model/params.json").read_bytes() == before
    assert not (root / f"model/backtest_{GATE_SEASON}.json").exists()
    assert "## Draw calibration" in (root / "reports/backtest.md").read_text(encoding="utf-8")


def test_refit_leaves_params_untouched_when_gate_fails(root, monkeypatch):
    refit_stub(monkeypatch, root, ll=(0.71, 0.72))
    before = (root / "model/params.json").read_bytes()
    assert backtest.main(REFIT) == 1
    assert (root / "model/params.json").read_bytes() == before
    assert "## Draw calibration" in (root / "reports/backtest.md").read_text(encoding="utf-8")


# ---- player goal distributions ----

def test_run_backtest_keep_goal_dist_columns(small):
    default = run_backtest(small, GATE_SEASON, P, n_sims=SIMS)["rows"]
    kept = run_backtest(small, GATE_SEASON, P, n_sims=SIMS, keep_goal_dist=True)["rows"]
    assert list(default.columns) == backtest.ROW_COLUMNS
    assert list(kept.columns) == backtest.ROW_COLUMNS + ["home", "away", "gd_home", "gd_away"]
    pd.testing.assert_frame_equal(default, kept[backtest.ROW_COLUMNS])
    r = kept.iloc[0]
    assert len(r["gd_home"]) == 11 and abs(sum(r["gd_away"]) - 1) < 1e-3
    assert r["home"] != r["away"]
