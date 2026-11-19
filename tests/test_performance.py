from datetime import UTC, datetime

import pandas as pd
import pytest

from pipeline import export, performance

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def result(home_goals=4, away_goals=2, reg=None, gid=1, date="2026-11-18"):
    rh, ra = reg if reg is not None else (home_goals, away_goals)
    return {"game_id": gid, "date": date, "home": "TOR", "away": "BOS",
            "home_goals": home_goals, "away_goals": away_goals,
            "reg_home_goals": rh, "reg_away_goals": ra,
            "decided": "REG", "home_won": home_goals > away_goals}


def pick(market="h2h", outcome="home", line=None, price=2.0, book="coolbet", flag=None,
         veikkaus=None):
    return {"market": market, "line": line, "outcome": outcome, "book": book, "price": price,
            "p_model": 0.5, "p_market": 0.5, "edge": 0.0, "ev": 0.0, "flag": flag,
            "veikkaus": veikkaus}


def pred(gid=1, ts="2026-11-18T10:00:00Z", start="2026-11-18T23:00:00Z", p=0.6, picks=None,
         market=None):
    return {"ts": ts, "model_version": "1.1.0", "game_id": gid, "nhl_date": "2026-11-18",
            "start_utc": start, "home": "TOR", "away": "BOS", "p_home_win": p,
            "p_reg": [0.4, 0.2, 0.4], "total_mean": 6.0, "market": market, "picks": picks}


def test_settle_totals_push():
    assert performance.settle(pick("totals", "over", 6.0), result(3, 3)) == 0.0
    assert performance.settle(pick("totals", "over", 5.5), result(3, 3)) == 1.0
    assert performance.settle(pick("totals", "under", 5.5), result(3, 3)) == -1.0


def test_settle_shootout_totals_counts_final():
    # 3-3 in regulation, 4-3 final after the shootout: totals settle on 7 goals
    res = result(4, 3, reg=(3, 3))
    assert performance.settle(pick("totals", "over", 6.5), res) == 1.0
    # 1x2 uses regulation: the draw wins
    assert performance.settle(pick("1x2", "draw", price=4.0), res) == 3.0
    assert performance.settle(pick("1x2", "home", price=2.0), res) == -1.0


def test_settle_betfair_commission():
    profit = performance.settle(pick(book="betfair_ex_eu", price=2.0), result(4, 2))
    assert profit == pytest.approx(0.95)
    assert performance.settle(pick(book="coolbet", price=2.0), result(4, 2)) == 1.0
    assert performance.settle(pick(book="betfair_ex_eu"), result(2, 4)) == -1.0


def test_settle_spread_minus_one_and_half():
    assert performance.settle(pick("spreads", "home", -1.5), result(4, 2)) == 1.0
    assert performance.settle(pick("spreads", "home", -1.5), result(3, 2)) == -1.0
    assert performance.settle(pick("spreads", "away", -1.5), result(2, 3)) == 1.0
    assert performance.settle(pick("spreads", "away", 1.5), result(2, 4)) == 1.0


def test_settle_unsettleable():
    assert performance.settle(pick("corners", "home"), result()) is None


def test_official_row_only():
    rows = [pred(ts="2026-11-18T10:00:00Z", p=0.6), pred(ts="2026-11-19T00:00:00Z", p=0.1)]
    perf = performance.build_performance(rows, [result()], [], None, NOW, None)
    assert perf["windows"]["season"]["n"] == 1
    assert perf["recent"][0]["p_home"] == 0.6


def test_clv_uses_last_row_before_start():
    odds = [
        {"ts": "2026-11-18T09:00:00Z", "game_id": 1, "start_utc": "2026-11-18T23:00:00Z",
         "ref": {"h2h": [0.4, 0.6]}},
        {"ts": "2026-11-18T20:00:00Z", "game_id": 1, "start_utc": "2026-11-18T23:00:00Z",
         "ref": {"h2h": [0.5, 0.5]}},
        {"ts": "2026-11-19T00:00:00Z", "game_id": 1, "start_utc": "2026-11-18T23:00:00Z",
         "ref": {"h2h": [0.9, 0.1]}},
    ]
    rows = [pred(picks=[pick(price=2.2)])]
    perf = performance.build_performance(rows, [result()], odds, None, NOW, None)
    w = perf["windows"]["season"]
    assert w["clv_n"] == 1
    assert w["clv_mean"] == pytest.approx(2.2 * 0.5 - 1)


def test_small_sample_flag():
    perf = performance.build_performance([pred()], [result()], [], None, NOW, None)
    assert perf["windows"]["season"]["small_sample"] is True


def test_picks_groups_and_veikkaus():
    vk = {"price": 2.5, "ev": 0.1, "value": True}
    rows = [pred(picks=[pick(veikkaus=vk), pick(flag="suspicious", price=3.0)])]
    perf = performance.build_performance(rows, [result()], [], None, NOW, None)
    picks = perf["windows"]["season"]["picks"]
    assert picks["all"]["n"] == 2 and picks["all"]["roi"] == pytest.approx(1.5)
    assert picks["no_flag"]["n"] == 1
    assert picks["veikkaus"]["n"] == 1 and picks["veikkaus"]["roi"] == pytest.approx(1.5)


def test_window_30d_excludes_old():
    rows = [pred(1), pred(2)]
    res = [result(gid=1, date="2026-11-18"), result(gid=2, date="2026-09-01")]
    perf = performance.build_performance(rows, res, [], None, NOW, None)
    assert perf["windows"]["30d"]["n"] == 1 and perf["windows"]["season"]["n"] == 2


def test_players_and_backtest_blocks():
    players = pd.DataFrame([{"game_id": 1, "player": 9, "team": "TOR", "p_pts1": 0.5,
                             "p_g1": 0.2, "base_pts1": 0.4, "base_g1": 0.1, "y_pts": 1,
                             "y_g": 0}])
    bt = {"n": 10, "brier": 0.24, "logloss": 0.67, "brier_base": 0.25, "logloss_base": 0.69}
    perf = performance.build_performance([pred()], [result()], [], players, NOW, bt)
    assert perf["windows"]["season"]["players"]["n"] == 1
    assert perf["backtest"]["label"] == "Backtest 2025–26"
    assert export.validate_performance(perf) == []


def test_empty_logs_validate():
    perf = performance.build_performance([], [], [], None, NOW, None)
    for w in perf["windows"].values():
        assert w["n"] == 0 and w["brier"] is None and w["logloss"] is None
        assert w["clv_mean"] is None and w["players"] is None
        assert w["picks"]["all"] == {"n": 0, "hits": 0, "hit_rate": None, "roi": None}
    assert perf["recent"] == [] and perf["backtest"] is None
    assert export.validate_performance(perf) == []
