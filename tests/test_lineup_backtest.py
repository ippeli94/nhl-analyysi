
import pandas as pd
import pytest

from pipeline import backtest, lineup_backtest
from pipeline.config import ModelParams
from pipeline.lineup import Contrib
from pipeline.lineup_backtest import actual_expect, decide
from pipeline.predict import Mults
from tests.synth import make_team_games

P = ModelParams()


def m(ll, ll1x2):
    return {"logloss": ll, "logloss_1x2": ll1x2}


def test_gate_decision():
    base = m(0.680, 1.000)
    # lineup better on both, h2h on top better on winner log loss
    assert decide({"baseline": base, "lineup": m(0.679, 0.999), "h2h": m(0.6805, 1.0),
                   "lineup_h2h": m(0.6785, 1.001)}) == (True, True)
    # lineup better on winner only -> off; h2h judged on top of baseline
    assert decide({"baseline": base, "lineup": m(0.679, 1.001), "h2h": m(0.6799, 1.0),
                   "lineup_h2h": m(0.670, 0.990)}) == (False, True)
    # ties do not pass
    assert decide({"baseline": base, "lineup": m(0.680, 0.999), "h2h": m(0.680, 1.0),
                   "lineup_h2h": m(0.680, 0.999)}) == (False, False)
    # lineup passes, h2h on top worse
    assert decide({"baseline": base, "lineup": m(0.679, 0.999), "h2h": m(0.670, 0.99),
                   "lineup_h2h": m(0.6791, 0.998)}) == (True, False)


def test_actual_expect_marks_dressed_players():
    contrib = {1: Contrib(1, "a", "F", 0.1, 0.9, 0.2), 2: Contrib(2, "b", "D", 0.05, 0.8, 0.3)}
    assert actual_expect(contrib, {1, 3}) == {1: 1.0, 2: 0.0, 3: 1.0}


def test_run_backtest_applies_mults_hook():
    tg = make_team_games(seasons=("20242025", "20252026"), games_per_team=12,
                         strengths={"A": 0.2, "B": -0.2, "C": 0.1, "D": -0.1}, seed=3)
    seen = []

    def hook(ts, game_id, home, away):
        assert isinstance(ts, pd.Timestamp)
        seen.append((game_id, home, away))
        return Mults(att_h=1.2)

    plain = backtest.run_backtest(tg, "20252026", P, n_sims=200)["rows"]
    hooked = backtest.run_backtest(tg, "20252026", P, n_sims=200, mults_fn=hook)["rows"]
    assert len(seen) == len(plain)
    assert hooked["p_home"].mean() > plain["p_home"].mean() + 0.03
    assert (hooked["p_home"] > plain["p_home"]).mean() > 0.75
    assert {g for g, _, _ in seen} == set(plain["game_id"])


def test_winner_logloss_at_kappa_one_is_plain():
    rows = pd.DataFrame({"p_home": [0.6, 0.4], "y_home": [1.0, 0.0], "p_reg_home": [0.5, 0.3],
                         "p_reg_draw": [0.25, 0.25], "p_reg_away": [0.3, 0.45]})
    plain = lineup_backtest.winner_logloss(rows, 1.0)
    assert plain == pytest.approx(-(pd.Series([0.6, 0.6]).map(__import__("math").log)).mean())
    # kappa > 1 moves home win toward the OT split of the draw mass
    assert lineup_backtest.winner_logloss(rows, 1.6) != pytest.approx(plain)


def _pg_for(tg):
    rows = []
    for g in tg.itertuples():
        for k in range(3):
            pid = (ord(g.team) * 10) + k
            rows.append({"game_id": g.game_id, "season": g.season, "game_type": 2,
                         "date": g.date, "team": g.team, "opp": g.opp, "player": pid,
                         "name": f"P{pid}", "pos": "C", "grp": "F", "toi": 900, "g": 0,
                         "a": 0, "pts": 0, "ixg": 0.1, "team_goals": 2})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("mode", ["actual", "projected"])
def test_lineup_inputs_use_only_earlier_rows(mode, monkeypatch):
    tg = make_team_games(seasons=("20242025", "20252026"), games_per_team=6,
                         strengths={"A": 0.2, "B": -0.2, "C": 0.1, "D": -0.1}, seed=3)
    pg = _pg_for(tg)
    calls = []
    real = lineup_backtest.lineup.contributions

    def spy(pg_, tg_, team, asof, p, season, extra=()):
        calls.append((asof, set(extra)))
        assert pg_["date"].max() < pd.Timestamp(asof)
        assert tg_["date"].max() < pd.Timestamp(asof)
        return real(pg_, tg_, team, asof, p, season, extra)

    monkeypatch.setattr(lineup_backtest.lineup, "contributions", spy)
    out = lineup_backtest.lineup_inputs(pg, tg, "20252026", P, mode)
    games = tg[(tg["season"] == "20252026") & tg["is_home"]]
    assert set(out) == {(int(g), str(t)) for g, h, a in
                        zip(games["game_id"], games["team"], games["opp"], strict=True)
                        for t in (h, a)}
    if mode == "actual":
        # dressed players of that game are the expected lineup
        gid, team = next(iter(out))
        dressed = set(pg[(pg["game_id"] == gid) & (pg["team"] == team)]["player"])
        _, expect = out[(gid, team)]
        assert {p for p, e in expect.items() if e == 1.0} == dressed
    assert calls
