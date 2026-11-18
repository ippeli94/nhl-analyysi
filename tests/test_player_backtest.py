import json
from dataclasses import fields

import numpy as np
import pandas as pd
import pytest

from pipeline import backtest, config, player_backtest, players, xg
from pipeline.config import ModelParams, PlayerParams
from pipeline.player_backtest import PB_COLUMNS
from pipeline.player_backtest import player_backtest as run_pb
from tests.synth import make_records

P = ModelParams()
PP = PlayerParams()
DIST = [0.05, 0.15, 0.25, 0.25, 0.15, 0.10, 0.05, 0, 0, 0, 0]
ROSTERS = {
    "AAA": [(100 + i, pos) for i, pos in enumerate("CLRCLRDDDD")],
    "BBB": [(200 + i, pos) for i, pos in enumerate("CLRCLRDDDD")],
}
GAMES = [
    ("2025-11-01", "AAA", "BBB"), ("2025-11-03", "BBB", "AAA"),
    ("2025-11-05", "AAA", "BBB"), ("2025-11-07", "BBB", "AAA"),
    ("2025-11-09", "AAA", "BBB"),
]
ARGS = ["--tune-season", "20242025", "--gate-season", "20252026"]


@pytest.fixture
def stub_score(monkeypatch):
    def fake(df, coefs):
        return np.where(df["empty_net"].to_numpy(dtype=bool), np.nan, 0.1)

    monkeypatch.setattr(xg, "score", fake)


def team_rows_for(recs):
    return pd.DataFrame([
        {"game_id": r["id"], "date": pd.Timestamp(r["date"]), "home": r["home"]["abbr"],
         "away": r["away"]["abbr"], "gd_home": DIST, "gd_away": DIST}
        for r in recs
    ])


@pytest.fixture
def league(stub_score):
    recs = make_records(GAMES, ROSTERS, seed=4)
    return players.player_games(recs, {}), team_rows_for(recs)


def test_player_backtest_is_leak_free(league, monkeypatch):
    pg, rows = league
    seen = {"rates": 0, "lineup": 0}
    real_rates, real_lineup = players.player_rates, players.project_lineup

    def rates(hist, asof, p, pp, season=None):
        assert hist["date"].max() < pd.Timestamp(asof)
        seen["rates"] += 1
        return real_rates(hist, asof, p, pp, season)

    def lineup(hist, team, asof, season):
        assert hist["date"].max() < pd.Timestamp(asof)
        seen["lineup"] += 1
        return real_lineup(hist, team, asof, season)

    monkeypatch.setattr(players, "player_rates", rates)
    monkeypatch.setattr(players, "project_lineup", lineup)
    run_pb(pg, rows, "20252026", P, PP)
    n_dates = rows["date"].nunique() - 1  # the first date has no history
    assert seen["rates"] == n_dates  # one rates fit per date
    assert seen["lineup"] == 2 * n_dates


def test_player_backtest_rows_only_dressed_projected(league):
    pg, rows = league
    # player 100 sits out the last game; 999 dresses without being projected
    last = pg["game_id"].max()
    pg = pg[~((pg["game_id"] == last) & (pg["player"] == 100))]
    extra = pg[(pg["game_id"] == last) & (pg["team"] == "AAA")].iloc[[0]].copy()
    extra["player"] = 999
    pg = pd.concat([pg, extra], ignore_index=True)
    res = run_pb(pg, rows, "20252026", P, PP)
    out = res["rows"]
    assert list(out.columns) == PB_COLUMNS
    last_rows = out[out["game_id"] == last]
    assert 100 not in set(last_rows["player"]) and 999 not in set(out["player"])
    dressed = {(r.game_id, r.player) for r in pg.itertuples()}
    assert all((r.game_id, r.player) in dressed for r in out.itertuples())
    assert out["p_pts1"].between(0, 1).all() and out["b_pts1"].between(0, 1).all()


def test_coverage_and_unassigned_known_numbers():
    def row(gid, day, team, pid, g):
        return {"game_id": gid, "season": "20252026", "game_type": 2,
                "date": pd.Timestamp(day), "team": team, "opp": "BBB" if team == "AAA" else "AAA",
                "player": pid, "name": f"P{pid}", "pos": "C", "grp": "F", "toi": 1000,
                "g": g, "a": 0, "pts": g, "ixg": 0.0, "team_goals": 0}

    data = []
    for gid, day, scorers in ((1, "2025-11-01", {}), (2, "2025-11-03", {9: 1, 1: 3})):
        for team, ids in (("AAA", [1, 2, 3, 4] + ([9] if gid == 2 else [])),
                          ("BBB", [11, 12, 13, 14])):
            for pid in ids:
                data.append(row(gid, day, team, pid, scorers.get(pid, 0) if team == "AAA" else 0))
    pg = pd.DataFrame(data, columns=players.PG_COLUMNS)
    rows = pd.DataFrame([
        {"game_id": 2, "date": pd.Timestamp("2025-11-03"), "home": "AAA", "away": "BBB",
         "gd_home": DIST, "gd_away": DIST},
    ])
    res = run_pb(pg, rows, "20252026", P, PP)
    assert res["unassigned"] == pytest.approx(0.25)
    assert res["coverage"] == pytest.approx(8 / 9)


def test_metrics_compare_against_baseline(league):
    pg, rows = league
    res = run_pb(pg, rows, "20252026", P, PP)
    m = res["metrics"]
    assert len(res["rows"]) > 0
    for k in ("points", "goals"):
        assert m[k]["n"] == len(res["rows"])
        assert m[k]["brier"] is not None and m[k]["brier_base"] is not None
    assert m["points2"]["n"] == len(res["rows"]) and m["points2"]["brier_base"] is None


def test_player_without_baseline_never_becomes_nan(league, monkeypatch):
    pg, rows = league
    real = players.baseline_probs

    def partial(hist, asof, season, slots):
        out = real(hist, asof, season, slots)
        out.pop(100, None)
        if 101 in out:
            out[101] = (None, None)
        return out

    monkeypatch.setattr(players, "baseline_probs", partial)
    res = run_pb(pg, rows, "20252026", P, PP)
    out = res["rows"]
    assert {100, 101} <= set(out["player"])
    assert out[["b_pts1", "b_g1"]].notna().all().all()
    for k in ("points", "goals"):
        assert np.isfinite(res["metrics"][k]["brier_base"])
        assert np.isfinite(res["metrics"][k]["logloss_base"])


def fake_result(ll, unassigned):
    m = {"n": 10, "brier": 0.2, "brier_base": 0.25, "logloss": ll, "logloss_base": 0.7,
         "calibration": []}
    return {"rows": pd.DataFrame(columns=PB_COLUMNS), "coverage": 0.9, "unassigned": unassigned,
            "metrics": {"points": m, "goals": m, "points2": m}}


@pytest.mark.parametrize("measured, expected", [(0.0437, 0.045), (0.25, 0.10), (0.001, 0.0)])
def test_tune_players_picks_lowest_logloss(monkeypatch, measured, expected):
    def fake(pg, rows, season, p, pp):
        ll = 0.5 if (pp.half_life_days, pp.prior_toi_h) == (80.0, 2.5) else 0.6
        return fake_result(ll, measured)

    monkeypatch.setattr(player_backtest, "player_backtest", fake)
    best = player_backtest.tune_players(None, None, "20242025", P, PlayerParams(xg_blend=0.4))
    assert (best.half_life_days, best.prior_toi_h) == (80.0, 2.5)
    assert best.unassigned == pytest.approx(expected)
    assert best.xg_blend == 0.4


def test_player_gate_requires_both_markets():
    def mk(b, bb, ll, llb):
        return {"brier": b, "brier_base": bb, "logloss": ll, "logloss_base": llb}

    good, bad_b, bad_ll = mk(.2, .25, .5, .6), mk(.3, .25, .5, .6), mk(.2, .25, .7, .6)
    assert player_backtest.player_gate({"points": good, "goals": good})
    assert not player_backtest.player_gate({"points": good, "goals": bad_b})
    assert not player_backtest.player_gate({"points": bad_ll, "goals": good})
    assert not player_backtest.player_gate({"points": good, "goals": {**good, "brier": None}})


def stub_cli(monkeypatch, passed):
    monkeypatch.setattr(backtest, "load_team_games", lambda *a, **k: None)
    monkeypatch.setattr(backtest, "load_goalie_games", lambda *a, **k: None)
    monkeypatch.setattr(player_backtest.cache, "load_seasons", lambda s: [])
    monkeypatch.setattr(player_backtest.xg, "load_coefs", lambda f: {})
    monkeypatch.setattr(player_backtest.players, "player_games", lambda *a, **k: None)
    seen = {}

    def fake_bt(tg, season, p, n_sims=2000, gg=None, goalie_mode="none", keep_goal_dist=False):
        seen.setdefault("runs", []).append((season, keep_goal_dist))
        return {"rows": pd.DataFrame({"gd_home": [DIST], "gd_away": [DIST]})}

    cal = [{"lo": 0.3, "hi": 0.35, "n": 500, "p_mean": 0.32, "y_mean": 0.33}]

    def fake_pb(pg, rows, season, p, pp):
        r = fake_result(0.5 if passed else 0.8, 0.04)
        for k in ("points", "goals"):
            r["metrics"][k] = {**r["metrics"][k], "calibration": cal}
        return r

    monkeypatch.setattr(backtest, "run_backtest", fake_bt)
    monkeypatch.setattr(player_backtest, "player_backtest", fake_pb)
    return seen


def test_cli_writes_params_and_report_on_pass(root, monkeypatch):
    seen = stub_cli(monkeypatch, True)
    assert player_backtest.main(ARGS) == 0
    assert seen["runs"] == [("20242025", True), ("20252026", True)]
    pp = json.loads((root / "model/player_params.json").read_text())
    assert pp["gate"]["passed"] is True and "calibration" not in pp["gate"]["points"]
    assert {"season", "tune_season", "goalie_mode", "coverage", "unassigned"} <= set(pp["gate"])
    assert "P(≥1 piste)" in (root / "reports/player_backtest.md").read_text(encoding="utf-8")
    loaded = config.load_player_params()  # round trip: only PlayerParams fields + gate
    assert {f.name for f in fields(PlayerParams)} == set(pp) - {"gate"}
    assert loaded.unassigned == pp["unassigned"]
    assert config.load_player_gate()["passed"] is True


def test_cli_fail_leaves_params_untouched(root, monkeypatch):
    stub_cli(monkeypatch, False)
    assert player_backtest.main(ARGS) == 1
    assert not (root / "model/player_params.json").exists()
    assert "HYLÄTTY" in (root / "reports/player_backtest.md").read_text(encoding="utf-8")
    (root / "model").mkdir()
    f = root / "model/player_params.json"
    f.write_text('{"unassigned": 0.05}\n')
    assert player_backtest.main(ARGS) == 1
    assert f.read_text() == '{"unassigned": 0.05}\n'
