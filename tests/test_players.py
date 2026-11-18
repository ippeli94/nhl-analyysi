import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline import config, players, xg
from pipeline.config import ModelParams, PlayerParams
from tests.synth import make_records

P = ModelParams()
ASOF = date(2025, 12, 1)
COEFS: dict = {}
ROSTERS = {
    "AAA": [(100 + i, pos) for i, pos in enumerate("CLRCLRDDDD")],
    "BBB": [(200 + i, pos) for i, pos in enumerate("CLRCLRDDDD")],
}


@pytest.fixture
def stub_score(monkeypatch):
    def fake(df, coefs):
        return np.where(df["empty_net"].to_numpy(dtype=bool), np.nan, 0.1)

    monkeypatch.setattr(xg, "score", fake)


@pytest.fixture
def unit_weights(monkeypatch):
    def fake(tg, asof, p, season=None):
        return (tg["date"] < pd.Timestamp(asof)).to_numpy(dtype=float)

    monkeypatch.setattr(players.ratings, "weights", fake)


def pg_rows(rows):
    """rows: (player, grp, date, toi_s, g, a, ixg[, season]) -> player_games-shaped frame."""
    out = []
    for i, r in enumerate(rows):
        pid, grp, day, toi, g, a, ixg = r[:7]
        season = r[7] if len(r) > 7 else "20252026"
        out.append({
            "game_id": 1000 + i, "season": season, "game_type": 2, "date": pd.Timestamp(day),
            "team": "AAA", "opp": "BBB", "player": pid, "name": f"P{pid}",
            "pos": "C" if grp == "F" else "D", "grp": grp, "toi": toi, "g": g, "a": a,
            "pts": g + a, "ixg": ixg, "team_goals": g,
        })
    return pd.DataFrame(out, columns=players.PG_COLUMNS)


def test_player_games_columns_points_and_team_goals(stub_score):
    games = [("2025-11-01", "AAA", "BBB"), ("2025-11-03", "BBB", "AAA")]
    recs = make_records(games, ROSTERS, seed=3)
    pg = players.player_games(recs, COEFS)
    assert list(pg.columns) == players.PG_COLUMNS
    assert len(pg) == 2 * 20
    assert (pg.pts == pg.g + pg.a).all() and set(pg.grp) <= {"F", "D"}
    assert (pg[pg.pos == "D"].grp == "D").all() and (pg[pg.pos == "C"].grp == "F").all()
    gid = recs[0]["id"]
    one = pg[(pg.game_id == gid) & (pg.team == "AAA")]
    assert (one.team_goals == one.g.sum()).all()
    assert set(one.opp) == {"BBB"}
    # assists derived from the shots add up to the credited assists
    total_a = sum(len([x for x in (s["a1"], s["a2"]) if x]) for r in recs for s in r["shots"])
    assert pg.a.sum() == total_a


def test_ixg_counts_shots_and_empty_net_goals(stub_score):
    rec = make_records([("2025-11-01", "AAA", "BBB")], ROSTERS, seed=1)[0]
    pid = ROSTERS["AAA"][0][0]

    def shot(ev, en):
        return {"p": 1, "t": 5, "team": "AAA", "shooter": pid, "goalie": None if en else 1,
                "x": 80.0, "y": 0.0, "dist": 9.0, "angle": 5.0, "type": "wrist", "sit": "1551",
                "strength": "5v5", "ev": ev, "empty_net": en, "rebound": False, "rush": False,
                "a1": None, "a2": None}

    rec["shots"] = [shot("sog", False), shot("miss", False), shot("goal", False),
                    shot("goal", True)]
    pg = players.player_games([rec], COEFS)
    assert pg[pg.player == pid].ixg.iloc[0] == pytest.approx(1.3)
    assert (pg[pg.player != pid].ixg == 0).all()


def test_rate_formula_with_unit_weights(stub_score, unit_weights):
    pid = 7
    pg = pg_rows([
        (pid, "F", "2025-11-01", 1200, 1, 1, 0.3),
        (pid, "F", "2025-11-03", 1200, 1, 0, 0.3),
    ])
    pp = PlayerParams(prior_toi_h=5.0)
    r = players.player_rates(pg, ASOF, P, pp)
    # g_hat = 0.5 * 0.6 + 0.5 * 2 = 1.3; points_hat = 1.3 + 1
    assert r.pos_g60["F"] == pytest.approx(1.3 / (40 / 60))
    assert r.pos_p60["F"] == pytest.approx(2.3 / (40 / 60))
    assert r.g60[pid] == pytest.approx((1.3 + 5 * r.pos_g60["F"]) / (40 / 60 + 5))
    assert r.p60[pid] == pytest.approx((2.3 + 5 * r.pos_p60["F"]) / (40 / 60 + 5))
    assert r.pos_g60["D"] == config.PLAYER_PRIOR_FALLBACK["D"][0]


def test_unknown_player_gets_position_prior(unit_weights):
    pg = pg_rows([
        (1, "F", "2025-11-01", 1200, 1, 0, 0.4),
        (2, "D", "2025-11-01", 1300, 0, 1, 0.1),
    ])
    r = players.player_rates(pg, ASOF, P, PlayerParams())
    assert r.rate(999, "D") == (r.pos_g60["D"], r.pos_p60["D"])
    assert r.rate(999, "C")[0] == r.pos_g60["F"]
    assert r.rate(999, "L") == (r.pos_g60["F"], r.pos_p60["F"])
    assert r.rate(1, "C") == (r.g60[1], r.p60[1])


def test_empty_input_uses_fallbacks():
    r = players.player_rates(pg_rows([]), ASOF, P, PlayerParams())
    assert r.pos_g60 == {"F": 0.85, "D": 0.25} and r.pos_p60 == {"F": 2.1, "D": 1.1}
    assert r.ppg == config.PLAYER_PPG_FALLBACK and r.g60 == {}


def test_traded_player_keeps_rates(unit_weights):
    rows = [
        (1, "F", "2025-11-01", 1200, 1, 0, 0.5),
        (1, "F", "2025-11-05", 1100, 0, 1, 0.2),
        (2, "F", "2025-11-01", 1000, 0, 0, 0.1),
    ]
    a = pg_rows(rows)
    b = a.copy()
    b.loc[b.date > pd.Timestamp("2025-11-03"), ["team", "opp"]] = ["CCC", "DDD"]
    ra = players.player_rates(a, ASOF, P, PlayerParams())
    rb = players.player_rates(b, ASOF, P, PlayerParams())
    assert ra.g60 == rb.g60 and ra.p60 == rb.p60


def test_previous_season_rows_count_with_lower_weight():
    pg = pg_rows([
        (1, "F", "2025-10-20", 1200, 2, 0, 1.0, "20252026"),  # current-season scorer
        (2, "F", "2025-10-21", 1200, 0, 0, 0.0, "20252026"),  # current-season blank
        (3, "F", "2025-04-01", 1200, 2, 0, 1.0, "20242025"),  # previous season only
    ])
    r = players.player_rates(pg, date(2025, 11, 1), P, PlayerParams())
    assert 3 in r.g60
    assert r.g60[3] != pytest.approx(r.pos_g60["F"])
    assert abs(r.g60[3] - r.pos_g60["F"]) < abs(r.g60[1] - r.pos_g60["F"])
    assert r.g60[3] > r.pos_g60["F"]


def test_rows_on_or_after_asof_are_ignored():
    early = pg_rows([(1, "F", "2025-11-01", 1200, 1, 0, 0.3)])
    late = pg_rows([
        (1, "F", "2025-11-01", 1200, 1, 0, 0.3),
        (1, "F", "2025-12-01", 1200, 5, 5, 4.0),
        (2, "F", "2025-12-05", 1200, 3, 3, 2.0),
    ])
    ra = players.player_rates(early, ASOF, P, PlayerParams())
    rb = players.player_rates(late, ASOF, P, PlayerParams())
    assert ra == rb
    assert 2 not in rb.g60


def test_ppg_from_data_capped_and_fallback(unit_weights):
    hi = pg_rows([(1, "F", "2025-11-01", 1200, 5, 12, 1.0)])  # 17 / 5 = 3.4
    assert players.player_rates(hi, ASOF, P, PlayerParams()).ppg == config.PLAYER_PPG_MAX
    mid = pg_rows([(1, "F", "2025-11-01", 1200, 2, 3, 1.0)])  # 5 / 2 = 2.5
    assert players.player_rates(mid, ASOF, P, PlayerParams()).ppg == pytest.approx(2.5)
    none = pg_rows([(1, "F", "2025-11-01", 1200, 0, 2, 0.1)])
    assert players.player_rates(none, ASOF, P, PlayerParams()).ppg == config.PLAYER_PPG_FALLBACK


def slots(*specs):
    return [{"id": i, "name": None, "pos": pos, "toi_proj": toi} for i, pos, toi in specs]


def test_baseline_is_unweighted_current_season():
    cur = [
        (1, "F", "2025-11-01", 1200, 1, 1, 0.3),
        (1, "F", "2025-11-03", 2400, 0, 0, 0.3),
    ]
    prev = [(1, "F", "2025-03-01", 1200, 4, 4, 2.0, "20242025")]
    base = players.baseline_probs(pg_rows(cur), ASOF, "20252026", slots((1, "C", 19.0)))
    base2 = players.baseline_probs(pg_rows(cur + prev), ASOF, "20252026", slots((1, "C", 19.0)))
    assert base == base2
    hours = 3600 / 3600
    toi_h = (1800 / 60) / 60  # season-average TOI of 30 min
    mu_pts = 2 / hours * toi_h
    mu_g = 1 / hours * toi_h
    p_pts, p_g = base[1]
    assert p_pts == pytest.approx(1 - np.exp(-mu_pts))
    assert p_g == pytest.approx(1 - np.exp(-mu_g))


def test_baseline_ignores_rows_on_or_after_asof():
    rows = [
        (1, "F", "2025-11-01", 1200, 1, 0, 0.3),
        (1, "F", "2025-12-01", 1200, 5, 5, 0.3),
    ]
    a = players.baseline_probs(pg_rows(rows), ASOF, "20252026", slots((1, "C", 18.0)))
    b = players.baseline_probs(pg_rows(rows[:1]), ASOF, "20252026", slots((1, "C", 18.0)))
    assert a == b


def test_baseline_player_without_rows_uses_position_average():
    rows = [
        (1, "F", "2025-11-01", 3600, 2, 1, 0.3),
        (2, "F", "2025-11-01", 3600, 0, 1, 0.3),
        (3, "D", "2025-11-01", 3600, 0, 2, 0.3),
    ]
    base = players.baseline_probs(
        pg_rows(rows), ASOF, "20252026", slots((50, "L", 15.0), (51, "D", 20.0))
    )
    # forwards: 2 goals / 2 h = 1.0 per 60; 4 points / 2 h = 2.0 per 60; TOI from the slot
    assert base[50][1] == pytest.approx(1 - np.exp(-1.0 * 0.25))
    assert base[50][0] == pytest.approx(1 - np.exp(-2.0 * 0.25))
    assert base[51][0] == pytest.approx(1 - np.exp(-2.0 * (20 / 60)))
    assert base[51][1] == pytest.approx(0.0)


def test_baseline_without_any_rows_uses_prior_fallback():
    sl = slots((1, "C", 18.0), (2, "D", 20.0))
    base = players.baseline_probs(pg_rows([]), ASOF, "20252026", sl)
    assert base[1][0] == pytest.approx(1 - np.exp(-2.1 * 0.3))
    assert base[1][1] == pytest.approx(1 - np.exp(-0.85 * 0.3))
    assert base[2][1] == pytest.approx(1 - np.exp(-0.25 * (20 / 60)))


def test_load_player_params_overlay_and_unknown_key(root):
    assert config.load_player_params() == PlayerParams()
    assert config.load_player_gate() is None
    f = root / "model" / "player_params.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"prior_toi_h": 10, "gate": {"passed": True}}), encoding="utf-8")
    assert config.load_player_params().prior_toi_h == 10
    assert config.load_player_params().xg_blend == 0.5
    assert config.load_player_gate() == {"passed": True}
    f.write_text(json.dumps({"x": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        config.load_player_params()
