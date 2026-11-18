import copy

import pytest

from pipeline import config, value

TS = "2026-11-17T21:37:40Z"
# P(final total = k) for k = 0..14 (last bucket is 14+); sums to 1
DIST = [0.01, 0.04, 0.09, 0.14, 0.17, 0.17, 0.14, 0.10, 0.07, 0.04, 0.02,
        0.006, 0.003, 0.0009, 0.0001]
SPREAD = {"home_-1.5": 0.31, "away_+1.5": 0.69, "away_-1.5": 0.22, "home_+1.5": 0.78}


def make_game(home_win=0.642, with_internal=True):
    game = {
        "prob": {"home_win": home_win, "away_win": round(1 - home_win, 4),
                 "reg_home": 0.53, "reg_draw": 0.22, "reg_away": 0.25},
        "score": {"spread": dict(SPREAD)},
    }
    if with_internal:
        game["_internal"] = {"total_dist": list(DIST)}
    return game


def bm(h2h=None, h2h3=None, totals=None, spreads=None):
    return {"h2h": h2h, "h2h3": h2h3, "totals": totals or [], "spreads": spreads or []}


def market(odds, key, line=None):
    return next(m for m in odds["markets"] if m["key"] == key and m["line"] == line)


def outcome(m, key):
    return next(o for o in m["outcomes"] if o["key"] == key)


def over(line):
    return sum(p for k, p in enumerate(DIST) if k > line)


def test_novig_known_numbers():
    p, vig = value.novig([1.58, 2.52])
    assert vig == pytest.approx(1 / 1.58 + 1 / 2.52 - 1)
    assert p[0] == pytest.approx((1 / 1.58) / (1 / 1.58 + 1 / 2.52))
    assert sum(p) == pytest.approx(1)


def test_clv():
    assert value.clv(1.66, 0.62) == pytest.approx(0.0292)


def test_effective_price():
    assert value.effective_price("coolbet", 1.66) == 1.66
    assert value.effective_price("betfair_ex_eu", 1.66) == pytest.approx(1 + 0.66 * 0.95)


def test_ev_and_edge_known_numbers():
    books = {"pinnacle": bm(h2h=[1.58, 2.52]), "coolbet": bm(h2h=[1.66, 2.30])}
    o = outcome(market(value.game_odds(make_game(), books, TS), "h2h"), "home")
    assert o["best"] == {"book": "coolbet", "price": 1.66}
    assert o["ev"] == pytest.approx(0.642 * 1.66 - 1, abs=1e-4)
    assert o["edge"] == pytest.approx(0.642 - 0.6146, abs=1e-4)
    assert o["p_market"] == pytest.approx(0.6146, abs=1e-4)
    assert o["value"] is True and o["flag"] is None


def test_value_needs_both_thresholds():
    books = {"pinnacle": bm(h2h=[1.9, 1.9]), "coolbet": bm(h2h=[2.0388, 1.9])}
    o = outcome(market(value.game_odds(make_game(0.515), books, TS), "h2h"), "home")
    assert o["ev"] == pytest.approx(0.05, abs=2e-4) and o["edge"] == pytest.approx(0.015)
    assert o["value"] is False  # edge too small
    books = {"pinnacle": bm(h2h=[1.9, 1.9]), "coolbet": bm(h2h=[1.934, 1.9])}
    o = outcome(market(value.game_odds(make_game(0.53), books, TS), "h2h"), "home")
    assert o["ev"] == pytest.approx(0.025, abs=2e-4) and o["edge"] == pytest.approx(0.03)
    assert o["value"] is False  # EV too small


def test_suspicious_flag_over_10pp():
    books = {"pinnacle": bm(h2h=[1.62, 2.35]), "coolbet": bm(h2h=[1.70, 2.30])}
    odds = value.game_odds(make_game(0.75), books, TS)
    o = outcome(market(odds, "h2h"), "home")
    assert o["flag"] == "suspicious" and o["value"] is True
    assert [p["outcome"] for p in odds["picks"]] == ["home"]
    assert odds["picks"][0]["flag"] == "suspicious"


def test_picks_sorted_unflagged_by_ev_then_flagged():
    books = {
        "pinnacle": bm(h2h=[1.62, 2.35], totals=[[5.5, 2.45, 1.66]]),
        "coolbet": bm(h2h=[1.70, 2.30], totals=[[5.5, 2.3, 1.70]]),
    }
    odds = value.game_odds(make_game(0.75), books, TS)
    # the flagged h2h pick has the higher EV and comes first in market order, still sorts last
    assert [(p["market"], p["flag"]) for p in odds["picks"]] == [
        ("totals", None), ("h2h", "suspicious")]
    assert odds["picks"][0]["ev"] == pytest.approx(0.62 * 1.70 - 1, abs=1e-3)
    assert odds["picks"][0]["ev"] < odds["picks"][1]["ev"]


def test_reference_falls_back_to_median():
    books = {
        "coolbet": bm(h2h=[1.66, 2.30]),
        "unibet_fi": bm(h2h=[1.70, 2.20]),
        "betsson": bm(h2h=[1.62, 2.40]),
    }
    m = market(value.game_odds(make_game(), books, TS), "h2h")
    assert m["ref"] == "median"
    homes = sorted(value.novig(p)[0][0] for p in ([1.66, 2.30], [1.70, 2.20], [1.62, 2.40]))
    med = homes[1]
    awys = sorted(value.novig(p)[0][1] for p in ([1.66, 2.30], [1.70, 2.20], [1.62, 2.40]))
    s = med + awys[1]
    assert outcome(m, "home")["p_market"] == pytest.approx(med / s, abs=1e-4)
    assert outcome(m, "away")["p_market"] == pytest.approx(awys[1] / s, abs=1e-4)
    assert (outcome(m, "home")["p_market"] + outcome(m, "away")["p_market"]
            == pytest.approx(1, abs=1e-3))


def test_single_book_gives_no_reference_and_no_pick():
    odds = value.game_odds(make_game(), {"coolbet": bm(h2h=[1.66, 2.30])}, TS)
    m = market(odds, "h2h")
    o = outcome(m, "home")
    assert m["ref"] is None
    assert o["p_market"] is None and o["edge"] is None and o["ev"] is None
    assert o["value"] is False and o["best"] == {"book": "coolbet", "price": 1.66}
    assert odds["picks"] == []


def test_invalid_vig_excluded_from_best_and_reference():
    books = {
        "pinnacle": bm(h2h=[1.58, 2.52]),
        "coolbet": bm(h2h=[1.66, 2.30]),
        "unibet_fi": bm(h2h=[1.80, 1.55]),  # vig 0.20
    }
    m = market(value.game_odds(make_game(), books, TS), "h2h")
    assert outcome(m, "home")["best"]["book"] == "coolbet"
    by_book = {b["book"]: b for b in m["books"]}
    assert by_book["unibet_fi"]["valid"] is False
    assert by_book["unibet_fi"]["vig"] == pytest.approx(0.2007, abs=1e-3)
    assert by_book["pinnacle"]["valid"] is True
    assert m["ref"] == "pinnacle"


def test_invalid_vig_not_in_median():
    books = {
        "coolbet": bm(h2h=[1.66, 2.30]),
        "unibet_fi": bm(h2h=[1.80, 1.55]),  # invalid
    }
    m = market(value.game_odds(make_game(), books, TS), "h2h")
    assert m["ref"] is None  # only one valid book


def test_betfair_commission_lowers_ev():
    books = {"pinnacle": bm(h2h=[1.58, 2.52]), "betfair_ex_eu": bm(h2h=[1.66, 2.40])}
    bf = outcome(market(value.game_odds(make_game(), books, TS), "h2h"), "home")
    assert bf["best"] == {"book": "betfair_ex_eu", "price": 1.66}
    assert bf["ev"] == pytest.approx(0.642 * (1 + 0.95 * 0.66) - 1, abs=1e-4)
    books = {"pinnacle": bm(h2h=[1.58, 2.52]), "coolbet": bm(h2h=[1.66, 2.40])}
    cb = outcome(market(value.game_odds(make_game(), books, TS), "h2h"), "home")
    assert bf["ev"] < cb["ev"]


def test_best_tie_goes_to_config_order():
    books = {"coolbet": bm(h2h=[1.66, 2.3]), "unibet_fi": bm(h2h=[1.66, 2.3])}
    o = outcome(market(value.game_odds(make_game(), books, TS), "h2h"), "home")
    assert o["best"]["book"] == "unibet_fi"  # earlier in ODDS_BOOKMAKERS


def test_whole_line_total_uses_push():
    books = {"pinnacle": bm(totals=[[6.0, 1.9, 1.95]]), "coolbet": bm(totals=[[6.0, 2.0, 1.85]])}
    m = market(value.game_odds(make_game(), books, TS), "totals", 6.0)
    o = outcome(m, "over")
    p_over, p_push = over(6.0), DIST[6]
    assert o["best"] == {"book": "coolbet", "price": 2.0}
    assert o["ev"] == pytest.approx(p_over * 2.0 + p_push - 1, abs=1e-4)
    assert o["p_model"] == pytest.approx(p_over / (1 - p_push), abs=1e-4)
    under = outcome(m, "under")
    assert under["p_model"] == pytest.approx(sum(DIST[:6]) / (1 - p_push), abs=1e-4)


def test_half_line_outside_exported_lines():
    books = {
        "pinnacle": bm(totals=[[7.5, 2.1, 1.75], [5.0, 1.5, 2.7]]),
        "coolbet": bm(totals=[[7.5, 2.1, 1.75], [5.0, 1.5, 2.7]]),
    }
    odds = value.game_odds(make_game(), books, TS)
    assert outcome(market(odds, "totals", 7.5), "over")["p_model"] == pytest.approx(
        over(7.5), abs=1e-4)
    five = market(odds, "totals", 5.0)
    assert outcome(five, "over")["p_model"] == pytest.approx(over(5) / (1 - DIST[5]), abs=1e-4)
    assert [m["line"] for m in odds["markets"]] == [5.0, 7.5]


def test_quarter_line_has_no_model_prob():
    books = {"pinnacle": bm(totals=[[6.25, 1.9, 1.95]]), "coolbet": bm(totals=[[6.25, 2.1, 1.8]])}
    m = market(value.game_odds(make_game(), books, TS), "totals", 6.25)
    o = outcome(m, "over")
    assert o["p_model"] is None and o["ev"] is None and o["edge"] is None
    assert o["value"] is False
    assert o["p_market"] is not None and len(m["books"]) == 2


def test_spreads_map_both_point_signs():
    books = {
        "pinnacle": bm(spreads=[[-1.5, 2.4, 1.6], [1.5, 1.35, 3.1]]),
        "coolbet": bm(spreads=[[-1.5, 2.5, 1.55], [1.5, 1.33, 3.2]]),
    }
    odds = value.game_odds(make_game(), books, TS)
    minus = market(odds, "spreads", -1.5)
    plus = market(odds, "spreads", 1.5)
    assert outcome(minus, "home")["p_model"] == 0.31
    assert outcome(minus, "away")["p_model"] == 0.69
    assert outcome(plus, "home")["p_model"] == 0.78
    assert outcome(plus, "away")["p_model"] == 0.22
    assert [m["line"] for m in odds["markets"]] == [-1.5, 1.5]


def test_other_spread_point_has_no_model_prob():
    books = {"pinnacle": bm(spreads=[[-2.5, 3.0, 1.4]]), "coolbet": bm(spreads=[[-2.5, 3.1, 1.4]])}
    o = outcome(market(value.game_odds(make_game(), books, TS), "spreads", -2.5), "home")
    assert o["p_model"] is None and o["value"] is False


def test_three_way_h2h_becomes_1x2_market():
    books = {
        "pinnacle": bm(h2h3=[2.2, 4.3, 3.0]),
        "coolbet": bm(h2h3=[2.3, 4.0, 3.1]),
    }
    odds = value.game_odds(make_game(), books, TS)
    m = market(odds, "1x2")
    assert [o["key"] for o in m["outcomes"]] == ["home", "draw", "away"]
    assert outcome(m, "draw")["p_model"] == 0.22
    assert outcome(m, "home")["p_model"] == 0.53
    assert outcome(m, "home")["best"] == {"book": "coolbet", "price": 2.3}
    assert [m["key"] for m in odds["markets"]] == ["1x2"]


def test_market_order():
    books = {
        "pinnacle": bm(h2h=[1.58, 2.52], h2h3=[2.2, 4.3, 3.0],
                       totals=[[6.5, 2.0, 1.85], [5.5, 1.6, 2.3]],
                       spreads=[[1.5, 1.35, 3.1], [-1.5, 2.4, 1.6]]),
    }
    odds = value.game_odds(make_game(), books, TS)
    assert [(m["key"], m["line"]) for m in odds["markets"]] == [
        ("h2h", None), ("1x2", None), ("totals", 5.5), ("totals", 6.5),
        ("spreads", -1.5), ("spreads", 1.5)]


def test_veikkaus_block_and_value_flag():
    books = {
        "pinnacle": bm(h2h=[1.58, 2.52]),
        "coolbet": bm(h2h=[1.70, 2.30]),
        "veikkaus_fi": bm(h2h=[1.55, 2.50]),
    }
    o = outcome(market(value.game_odds(make_game(), books, TS), "h2h"), "home")
    assert o["veikkaus"] == {"price": 1.55, "ev": pytest.approx(0.642 * 1.55 - 1, abs=1e-4),
                             "value": False}
    assert o["value"] is True  # the best price qualifies even though Veikkaus does not
    books["veikkaus_fi"] = bm(h2h=[1.66, 2.50])
    odds = value.game_odds(make_game(), books, TS)
    o = outcome(market(odds, "h2h"), "home")
    assert o["veikkaus"]["value"] is True
    assert o["veikkaus"]["ev"] == pytest.approx(0.642 * 1.66 - 1, abs=1e-4)
    assert odds["picks"][0]["veikkaus"] == o["veikkaus"]


def test_veikkaus_absent_is_null():
    books = {"pinnacle": bm(h2h=[1.58, 2.52]), "coolbet": bm(h2h=[1.70, 2.30])}
    o = outcome(market(value.game_odds(make_game(), books, TS), "h2h"), "home")
    assert o["veikkaus"] is None


def test_no_books_gives_none():
    assert value.game_odds(make_game(), {}, TS) is None


def test_game_without_internal_is_unpriced():
    books = {
        "pinnacle": bm(h2h=[1.58, 2.52], totals=[[5.5, 1.9, 1.95]],
                       spreads=[[-1.5, 2.4, 1.6]]),
        "coolbet": bm(h2h=[1.70, 2.30], totals=[[5.5, 2.0, 1.85]], spreads=[[-1.5, 2.5, 1.55]]),
    }
    odds = value.game_odds(make_game(with_internal=False), books, TS)
    assert odds["picks"] == []
    for m in odds["markets"]:
        for o in m["outcomes"]:
            assert o["p_model"] is None and o["ev"] is None and o["value"] is False
            assert o["p_market"] is not None and o["best"] is not None


def test_model_prob_directly():
    g = make_game()
    assert value.model_prob(g, "h2h", None, "away") == (0.358, 0.0)
    assert value.model_prob(g, "totals", 6.0, "over") == pytest.approx((over(6.0), DIST[6]))
    assert value.model_prob(g, "totals", 14.5, "over") is None
    assert value.model_prob(g, "spreads", 2.5, "home") is None
    assert value.model_prob(make_game(with_internal=False), "h2h", None, "home") is None


def test_market_summary_and_ref_summary_shapes():
    books = {
        "pinnacle": bm(h2h=[1.58, 2.52], totals=[[5.5, 1.9, 1.95]], spreads=[[-1.5, 2.4, 1.6]]),
        "coolbet": bm(h2h=[1.66, 2.30]),
    }
    odds = value.game_odds(make_game(), books, TS)
    ms = value.market_summary(odds)
    assert ms["fetched_at"] == TS and ms["ref"] == "pinnacle" and ms["1x2"] is None
    assert ms["h2h"] == [0.6146, 0.3854]
    assert ms["best_h2h"] == {"home": {"book": "coolbet", "price": 1.66},
                              "away": {"book": "pinnacle", "price": 2.52}}
    assert value.market_summary(None) is None

    rs = value.ref_summary(books)
    p_over = value.novig([1.9, 1.95])[0][0]
    p_home = value.novig([2.4, 1.6])[0][0]
    assert rs == {"source": "pinnacle", "h2h": [0.6146, 0.3854], "1x2": None,
                  "totals": {"5.5": round(p_over, 4)}, "spreads": {"-1.5": round(p_home, 4)}}
    assert value.ref_summary({"coolbet": bm(h2h=[1.66, 2.30])}) == {
        "source": None, "h2h": None, "1x2": None, "totals": {}, "spreads": {}}


def test_closing_line_is_last_row_before_start():
    start = "2026-11-18T00:00:00Z"

    def row(ts, tag):
        return {"ts": ts, "game_id": 7, "start_utc": start, "ref": {"tag": tag}}

    rows = [row("2026-11-17T15:17:00Z", "a"), row("2026-11-17T21:37:00Z", "b"),
            row("2026-11-18T00:30:00Z", "late")]
    assert value.closing_lines(rows) == {7: {"tag": "b"}}
    assert value.closing_lines([rows[2]]) == {}


def test_game_odds_does_not_mutate_inputs():
    books = {"pinnacle": bm(h2h=[1.58, 2.52]), "coolbet": bm(h2h=[1.66, 2.30])}
    before = copy.deepcopy(books)
    value.game_odds(make_game(), books, TS)
    assert books == before
    assert config.ODDS_EV_MIN == 0.03


def test_frontend_ev_threshold_matches_config():
    import re

    text = (config.ROOT / "site" / "format.js").read_text(encoding="utf-8")
    found = re.search(r"VALUE_EV_MIN = ([0-9.]+)", text)
    assert found and float(found.group(1)) == config.ODDS_EV_MIN
