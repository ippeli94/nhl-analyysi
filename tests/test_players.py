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


def lineup_pg(games):
    recs = make_records(games, ROSTERS, seed=5)
    return players.player_games(recs, COEFS), recs


def test_lineup_is_last_game_dressed_sorted_by_toi(stub_score):
    pg, _ = lineup_pg([("2025-11-01", "AAA", "BBB"), ("2025-11-03", "BBB", "AAA")])
    slots = players.project_lineup(pg, "AAA", ASOF, "20252026")
    assert len(slots) == 10
    assert [s["toi_proj"] for s in slots] == sorted((s["toi_proj"] for s in slots), reverse=True)
    assert set(slots[0]) == {"id", "name", "pos", "toi_proj"}
    ids = [s["id"] for s in slots]
    assert set(ids) == {pid for pid, _ in ROSTERS["AAA"]}


def test_absent_from_last_game_excluded(stub_score):
    games = [(f"2025-11-{d:02d}", "AAA", "BBB") for d in range(1, 11)]
    pg, recs = lineup_pg(games)
    gone = ROSTERS["AAA"][0][0]
    last = recs[-1]["id"]
    pg = pg[~((pg.game_id == last) & (pg.player == gone))]
    slots = players.project_lineup(pg, "AAA", ASOF, "20252026")
    assert gone not in {s["id"] for s in slots} and len(slots) == 9


def test_toi_proj_mean_of_last_five_any_team():
    rows = [(1, "F", f"2025-11-0{i + 1}", (10 + i) * 60, 0, 0, 0.0) for i in range(6)]
    pg = pg_rows(rows)
    pg.loc[pg.date >= pd.Timestamp("2025-11-05"), ["team", "opp"]] = ["BBB", "AAA"]
    # last five rows: 11..15 min -> 13.0; AAA's last game is 2025-11-04 and he left afterwards
    assert players.project_lineup(pg, "BBB", ASOF, "20252026")[0]["toi_proj"] == 13.0


def test_traded_away_player_dropped():
    rows = [
        (1, "F", "2025-11-01", 1200, 0, 0, 0.0),
        (2, "F", "2025-11-01", 1000, 0, 0, 0.0),
        (1, "F", "2025-11-05", 1100, 0, 0, 0.0),
    ]
    pg = pg_rows(rows)
    pg.loc[pg.date == pd.Timestamp("2025-11-05"), ["team", "opp"]] = ["BBB", "AAA"]
    assert [s["id"] for s in players.project_lineup(pg, "AAA", ASOF, "20252026")] == [2]
    assert [s["id"] for s in players.project_lineup(pg, "BBB", ASOF, "20252026")] == [1]


def test_team_without_games_returns_empty():
    pg = pg_rows([(1, "F", "2025-11-01", 1200, 0, 0, 0.0)])
    assert players.project_lineup(pg, "ZZZ", ASOF, "20252026") == []
    assert players.project_lineup(pg_rows([]), "AAA", ASOF, "20252026") == []


def test_previous_season_used_when_no_current_games():
    pg = pg_rows([(1, "F", "2025-03-01", 1200, 0, 0, 0.0, "20242025")])
    slots = players.project_lineup(pg, "AAA", ASOF, "20252026")
    assert [s["id"] for s in slots] == [1] and slots[0]["toi_proj"] == 20.0


def test_only_rows_before_asof():
    pg = pg_rows([
        (1, "F", "2025-11-01", 1200, 0, 0, 0.0),
        (2, "F", "2025-12-01", 1200, 0, 0, 0.0),
    ])
    assert [s["id"] for s in players.project_lineup(pg, "AAA", ASOF, "20252026")] == [1]


def poisson_dist(mu, cap=10):
    import math
    pm = [math.exp(-mu) * mu**k / math.factorial(k) for k in range(cap)]
    return pm + [1 - sum(pm)]


DIST = poisson_dist(3.2)
PP = PlayerParams(unassigned=0.03)


def make_slots(n=18, base=100):
    return [{"id": base + i, "name": f"S{i}", "pos": "D" if i >= 12 else "C",
             "toi_proj": 18.0 - i * 0.5}
            for i in range(n)]


def make_rates(slots, g=0.8, p=2.0, ppg=2.7):
    r = players._fallback_rates()
    r.ppg = ppg
    for i, s in enumerate(slots):
        r.g60[s["id"]] = g + 0.02 * (18 - i)
        r.p60[s["id"]] = p + 0.04 * (18 - i)
    return r


def test_binom_tails_known_numbers():
    assert players.binom_tails([0, 0, 1] + [0] * 8, 0.5) == pytest.approx([0.75, 0.25, 0.0])


def test_binom_tails_is_poisson_thinning():
    t = players.binom_tails(poisson_dist(3.0), 0.1)
    assert t[0] == pytest.approx(1 - np.exp(-0.3), abs=1e-3)


def test_shares_sum_to_team_expectation():
    slots = make_slots()
    probs = players.player_probs(slots, make_rates(slots), DIST, PP)
    mean = sum(i * x for i, x in enumerate(DIST))
    assert sum(x["exp_goals"] for x in probs) == pytest.approx(0.97 * mean, rel=1e-6)
    assert sum(x["exp_points"] for x in probs) <= 3 * mean


def test_empty_slots():
    assert players.player_probs([], players._fallback_rates(), DIST, PP) == []


def test_tails_monotone_and_goals_below_points():
    slots = make_slots()
    rates = make_rates(slots)
    rates.g60[slots[0]["id"]] = 2.0  # sniper
    rates.p60[slots[0]["id"]] = 2.0  # a = 0
    dist = [0.0] * 10 + [1.0]
    probs = players.player_probs(slots, rates, dist, PP)
    for x in probs:
        assert x["p_points"][0] >= x["p_points"][1] >= x["p_points"][2]
        assert x["p_goals"][0] >= x["p_goals"][1] >= x["p_goals"][2]
        assert all(g <= p + 1e-12 for g, p in zip(x["p_goals"], x["p_points"], strict=True))
        assert all(0 <= v <= 1 for v in x["p_goals"] + x["p_points"])


def test_pi_capped_and_at_least_q():
    slots = make_slots()
    rates = make_rates(slots)
    rates.p60[slots[1]["id"]] = 500.0
    rates.g60[slots[2]["id"]] = 1.0
    rates.p60[slots[2]["id"]] = 0.1  # sniper: points share below goal share
    by = {x["id"]: x for x in players.player_probs(slots, rates, DIST, PP)}
    assert by[slots[1]["id"]]["pi"] == pytest.approx(0.95)
    assert by[slots[2]["id"]]["pi"] == pytest.approx(by[slots[2]["id"]]["q"])


def test_debutant_probabilities_finite():
    slots = make_slots()
    rates = make_rates(slots)
    new = {"id": 999, "name": None, "pos": "C", "toi_proj": 12.0}
    probs = players.player_probs(slots + [new], rates, DIST, PP)
    by = {x["id"]: x for x in probs}
    assert 0 < by[999]["p_points"][0] < probs[0]["p_points"][0]
    assert all(np.isfinite(v) for v in by[999]["p_points"] + by[999]["p_goals"])


def test_team_block_top_is_union_and_rounded():
    slots = make_slots()
    rates = make_rates(slots)
    d_id = slots[17]["id"]
    rates.p60[d_id] = 9.0  # D with high points, low goals
    rates.g60[d_id] = 0.05
    sniper = slots[10]["id"]
    rates.g60[sniper] = 3.0
    rates.p60[sniper] = 3.0
    probs = players.player_probs(slots, rates, DIST, PP)
    b = players.team_block("AAA", probs, DIST, PP)
    ids = {p["id"] for p in b["top"]}
    assert 10 <= len(b["top"]) <= 20 and b["dressed"] == 18
    assert all(p["odds"] is None and p["team"] == "AAA" for p in b["top"])
    assert d_id in ids and sniper in ids
    assert all(round(v, 4) == v for p in b["top"] for v in p["p_points"] + p["p_goals"])
    assert players.team_block("AAA", [], DIST, PP) is None


def make_pin(teams):
    slots = make_slots()
    rates = make_rates(slots)
    lineups = {t: (slots if t in teams else []) for t in ("AAA", "BBB")}
    base = {s["id"]: (0.3, 0.1) for s in slots}
    return players.PlayerInputs(rates, lineups, base, PP)


def test_game_players_one_team_missing():
    block, log = players.game_players("AAA", "BBB", make_pin({"AAA"}), {"AAA": DIST, "BBB": DIST})
    assert block["away"] is None and block["home"]["team"] == "AAA"
    assert block["lineup"] == "projected" and list(log["teams"]) == ["AAA"]


def test_game_players_none_when_both_missing():
    assert players.game_players("AAA", "BBB", make_pin(set()), {"AAA": DIST, "BBB": DIST}) == (
        None, None)


def test_log_payload_columns():
    block, log = players.game_players("AAA", "BBB", make_pin({"AAA", "BBB"}),
                                      {"AAA": DIST, "BBB": DIST})
    rows = log["teams"]["AAA"]["players"]
    assert len(rows) == 18
    assert all(len(r) == len(config.PLAYER_LOG_COLUMNS) for r in rows)
    assert rows[0][-2:] == [0.3, 0.1]
    assert log["teams"]["AAA"]["unassigned"] == 0.03


def test_project_lineup_normalises_unknown_pos(stub_score):
    pg, _ = lineup_pg([("2025-11-01", "AAA", "BBB"), ("2025-11-03", "BBB", "AAA")])
    target = pg[pg["team"] == "AAA"]["player"].iloc[0]
    pg.loc[pg["player"] == target, "pos"] = "F"
    slots = players.project_lineup(pg, "AAA", ASOF, "20252026")
    assert {s["pos"] for s in slots} <= {"C", "L", "R", "D"}
    assert next(s for s in slots if s["id"] == target)["pos"] == "C"


def test_playoff_players_get_the_overtime_goal_with_certainty():
    from pipeline.config import ModelParams
    from pipeline.simulate import simulate

    lam = np.array([0.3, 0.35, 0.35]) * 3.0
    sp = {"league_period_share": [0.3, 0.35, 0.35], "en_prob": {1: 0.3, 2: 0.15},
          "ot_goal_share": 0.6}
    pin = make_pin({"AAA", "BBB"})
    out = {}
    for gt in (2, 3):
        sim = simulate(lam, lam, sp, ModelParams(), seed=9, n=5000, game_type=gt)
        _, log = players.game_players("AAA", "BBB", pin, sim["credited_dist"])
        out[gt] = (sim, log["teams"]["AAA"]["goals_mean"])
    sim3, mean3 = out[3]
    mean_reg = sum(i * x for i, x in enumerate(sim3["goal_dist"]["home"]))
    p_ot_home = sim3["prob"]["home_win"] - sim3["prob"]["reg_home"]
    assert mean3 == pytest.approx(mean_reg + p_ot_home, abs=2e-3)  # share 1, not 0.6
    assert mean3 > out[2][1]
