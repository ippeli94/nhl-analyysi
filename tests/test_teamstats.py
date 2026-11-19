from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline import teamstats, xg

SEASON = "20262027"
ASOF = date(2026, 12, 1)


@pytest.fixture(autouse=True)
def stub_score(monkeypatch):
    monkeypatch.setattr(
        xg, "score",
        lambda df, coefs: np.where(df["empty_net"].to_numpy(dtype=bool), np.nan, 0.1),
    )


def shot(team, p, t, ev="goal", strength="ev", empty_net=False):
    return {"p": p, "t": t, "team": team, "shooter": 1, "goalie": 9, "x": 70.0, "y": 0.0,
            "dist": 19.0, "angle": 0.0, "type": "wrist", "sit": "1551", "strength": strength,
            "ev": ev, "empty_net": empty_net, "rebound": False, "rush": False}


def rec(gid, day, home, away, shots, score, decided="REG", penalties=None):
    return {"id": gid, "season": SEASON, "game_type": 2, "date": day,
            "home": {"abbr": home}, "away": {"abbr": away},
            "score": {"home": score[0], "away": score[1]},
            "score_reg": {"home": score[0], "away": score[1]},
            "decided": decided, "shots": shots, "penalties": penalties or []}


def tg_from(records):
    rows = []
    for r in records:
        for side, other in (("home", "away"), ("away", "home")):
            gf, ga = r["score"][side], r["score"][other]
            goals = sum(1 for s in r["shots"] if s["ev"] == "goal" and s["team"] == r[side]["abbr"])
            rows.append(dict(
                game_id=r["id"], season=r["season"], game_type=2, date=pd.Timestamp(r["date"]),
                team=r[side]["abbr"], opp=r[other]["abbr"], is_home=side == "home",
                goals_reg=goals, xg_reg=1.0, score_reg=gf, opp_score_reg=ga,
                decided=r["decided"], won=gf > ga))
    return pd.DataFrame(rows)


def test_league_stats_empty():
    assert teamstats.league_stats(pd.DataFrame(), [], {}, SEASON, ASOF) == {}


def test_period_share():
    shots = [shot("A", 1, 10, "sog"), shot("A", 1, 20, "sog"), shot("B", 1, 30, "sog"),
             shot("B", 2, 10, "sog")]
    records = [rec(1, "2026-10-10", "A", "B", shots, (0, 0))]
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert st["A"].period_xg_share[0] == pytest.approx(2 / 3, abs=1e-3)
    assert st["A"].period_xg_share[1] == pytest.approx(0.0)
    assert st["A"].period_xg_share[2] is None
    assert st["B"].period_xg_share[0] == pytest.approx(1 / 3, abs=1e-3)


def test_led_after_two_counts():
    shots = [shot("A", 1, 10), shot("A", 2, 10), shot("B", 2, 500),
             shot("B", 3, 10), shot("B", 3, 600)]
    records = [rec(1, "2026-10-10", "A", "B", shots, (2, 3))]
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert (st["A"].led_after2, st["A"].led_after2_won) == (1, 0)
    assert (st["B"].trailed_after2, st["B"].trailed_after2_won) == (1, 1)
    assert (st["A"].trailed_after2, st["B"].led_after2) == (0, 0)


def test_first_goal():
    shots = [shot("B", 1, 100), shot("A", 1, 50), shot("A", 3, 5)]
    records = [rec(1, "2026-10-10", "A", "B", shots, (2, 1))]
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert (st["A"].scored_first, st["A"].scored_first_won) == (1, 1)
    assert (st["B"].scored_first, st["B"].scored_first_won) == (0, 0)


def test_ranks_require_min_games():
    records = []
    for i in range(10):
        pens = [{"p": 1, "t": 5, "team": "B", "min": 2}]
        shots = [shot("A", 1, 10, strength="pp")] if i % 2 == 0 else []
        records.append(rec(i, f"2026-10-{10 + i}", "A", "B", shots, (1 if i % 2 == 0 else 0, 0),
                           penalties=pens))
    records.append(rec(50, "2026-11-01", "C", "A", [], (0, 0)))
    for i in range(5):
        records.append(rec(60 + i, f"2026-11-{10 + i}", "C", "D", [], (0, 0)))
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert st["A"].gp == 11
    assert st["A"].pp_rank == 1
    assert st["D"].gp == 5
    assert st["D"].pp_rank is None and st["D"].pk_rank is None and st["D"].pim_rank is None
    assert st["B"].pim_rank == 1


def test_point_streak_and_split():
    records = [rec(1, "2026-10-10", "A", "B", [], (1, 0)),
               rec(2, "2026-10-11", "B", "A", [], (2, 1), decided="OT"),
               rec(3, "2026-10-12", "A", "B", [], (0, 1))]
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert st["A"].point_streak == 0
    assert st["B"].point_streak == 2
    assert st["A"].home_pts_pct == pytest.approx(0.5)
    assert st["A"].road_pts_pct == pytest.approx(0.5)


def test_schedule_load_road_run():
    records = [rec(1, "2026-11-10", "X", "A", [], (1, 0)),
               rec(2, "2026-11-11", "Y", "A", [], (1, 0)),
               rec(3, "2026-11-07", "A", "Z", [], (1, 0))]
    tg = tg_from(records)
    b2b, last4, run = teamstats.schedule_load(tg, "A", date(2026, 11, 12), False)
    assert (b2b, last4, run) == (True, 3, 3)
    assert teamstats.schedule_load(tg, "A", date(2026, 11, 12), True) == (True, 3, 0)
    assert teamstats.schedule_load(tg, "A", date(2026, 11, 20), False)[:2] == (False, 1)


def _season_records():
    records = []
    for i in range(10):
        records.append(rec(i, f"2026-10-{10 + i}", "A", "B", [], (2, 1)))
    for i in range(10):
        records.append(rec(100 + i, f"2026-11-{10 + i}", "C", "D", [], (1, 2)))
    return records


def test_season_leak_excluded():
    old = rec(900, "2025-12-01", "A", "B", [], (0, 3))
    old["season"] = "20252026"
    records = [old, rec(1, "2026-10-10", "A", "B", [], (2, 1))]
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert st["A"].streak == "V1"
    assert st["A"].gp == 1


def test_n_ranked_and_pk_direction():
    records = []
    for i in range(10):
        pens = [{"p": 1, "t": 5, "team": "A", "min": 2}]
        shots = [shot("B", 1, 10, strength="pp")] if i < 5 else []
        records.append(rec(i, f"2026-10-{10 + i}", "A", "B", shots, (0, len(shots)),
                           penalties=pens))
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert st["A"].n_ranked == st["B"].n_ranked == 2
    assert st["A"].pk == pytest.approx(0.5)
    assert st["A"].pim_rank == 1


def test_pk_rank_highest_is_best():
    records = []
    for i in range(10):
        for tm, opp, gid in (("A", "B", i), ("C", "D", 100 + i)):
            pens = [{"p": 1, "t": 5, "team": tm, "min": 2}]
            shots = [shot(opp, 1, 10, strength="pp")] if tm == "C" and i < 5 else []
            records.append(rec(gid, f"2026-10-{10 + i}", tm, opp, shots, (0, len(shots)),
                               penalties=pens))
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert st["A"].pk == 1.0 and st["C"].pk == pytest.approx(0.5)
    assert st["A"].pk_rank == 1 and st["C"].pk_rank == 2


def test_luck_last10():
    shots = [shot("A", 1, 10), shot("A", 1, 20)]
    records = [rec(1, "2026-10-10", "A", "B", shots, (2, 0))]
    st = teamstats.league_stats(tg_from(records), records, {}, SEASON, ASOF)
    assert st["A"].luck_last10 == pytest.approx(1.0)
    assert st["B"].luck_last10 == pytest.approx(-1.0)


def test_road_run_stops_at_season_boundary():
    old = rec(1, "2025-04-01", "X", "A", [], (1, 0))
    old["season"] = "20242025"
    records = [old, rec(2, "2026-11-10", "X", "A", [], (1, 0))]
    assert teamstats.schedule_load(tg_from(records), "A", date(2026, 11, 12), False)[2] == 2
