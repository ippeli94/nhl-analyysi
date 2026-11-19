from datetime import date

import pandas as pd

from pipeline import context

ASOF = date(2026, 12, 1)


def tg_rows(team, outcomes, season="20262027", start=date(2026, 10, 10), opp="Z", xg=(3.0, 2.0)):
    """outcomes: 'W' win, 'O' OT loss, 'L' regulation loss (oldest first)."""
    rows = []
    for i, o in enumerate(outcomes):
        d = pd.Timestamp(start) + pd.Timedelta(days=i)
        won = o == "W"
        sr, osr = (3, 2) if won else ((2, 2) if o == "O" else (1, 3))
        decided = "REG" if o in "WL" else "OT"
        gid = 1000 + i
        rows.append(dict(game_id=gid, season=season, game_type=2, date=d, team=team, opp=opp,
                         is_home=True, xg_reg=xg[0], score_reg=sr, opp_score_reg=osr,
                         decided=decided, won=won))
        rows.append(dict(game_id=gid, season=season, game_type=2, date=d, team=opp, opp=team,
                         is_home=False, xg_reg=xg[1], score_reg=osr, opp_score_reg=sr,
                         decided=decided, won=not won))
    return pd.DataFrame(rows)


def test_form_letters_and_streak():
    f = context.form(tg_rows("A", list("WLWWLOWLWW")), "A", ASOF)
    assert f["seq"] == list("VVHVJHVVHV")
    assert f["last10"] == "6-3-1"
    assert f["streak"] == "V2"


def test_form_none_without_games():
    assert context.form(tg_rows("A", []), "A", ASOF) is None
    assert context.form(pd.DataFrame(), "A", ASOF) is None


def test_form_short_history():
    f = context.form(tg_rows("A", list("WLO")), "A", ASOF)
    w, lo, otl = (int(x) for x in f["last10"].split("-"))
    assert w + lo + otl == 3
    assert f["seq"] == ["J", "H", "V"]


def test_form_goals_ot_winner_and_asof():
    tg = tg_rows("A", ["W"])
    f = context.form(tg, "A", ASOF)
    assert (f["gf"], f["ga"]) == (3, 2)
    assert f["xgf_pct"] == 0.6
    assert context.form(tg, "A", date(2026, 10, 10)) is None  # strictly before asof


def rec(i, home, away, d, score, pens=(), goals=(), game_type=2, season="20262027"):
    return {"id": i, "season": season, "game_type": game_type, "date": d,
            "home": {"id": 1, "abbr": home}, "away": {"id": 2, "abbr": away},
            "score": {"home": score[0], "away": score[1]},
            "penalties": [{"p": 1, "t": 100, "team": t, "min": m} for t, m in pens],
            "shots": [{"p": 1, "t": 200, "team": t, "ev": "goal", "strength": s} for t, s in goals]}


def test_special_teams_counts():
    recs = [rec(1, "A", "B", "2026-10-10", (3, 2),
                pens=[("B", 2), ("B", 2), ("B", 2), ("B", 2), ("A", 2)],
                goals=[("A", "pp"), ("A", "ev"), ("B", "pp")])]
    pp, pk = context.special_teams(recs, "A", "20262027", ASOF)
    assert pp == 0.25
    assert pk == 0.0
    assert context.special_teams([rec(1, "A", "B", "2026-10-10", (1, 0))], "A", "20262027", ASOF) \
        == (None, None)


def test_season_line_points_pct():
    tg = tg_rows("A", list("WWOL"))
    line = context.season_line(tg, [], "A", "20262027", ASOF)
    assert line["gp"] == 4 and line["pts_pct"] == 5 / 8
    assert context.season_line(tg, [], "A", "20252026", ASOF) is None


def test_season_compare_last_none_when_not_cached():
    tg = tg_rows("A", list("WW"))
    sc = context.season_compare(tg, [], "A", "20262027", ASOF)
    assert sc["now"]["gp"] == 2 and sc["last"] is None


def test_h2h_order_limit_summary():
    recs = [rec(i, "A" if i % 2 else "B", "B" if i % 2 else "A", f"2026-10-{10 + i:02d}",
                (3, 1)) for i in range(10)]
    tg = tg_rows("A", ["W"] * 3, opp="B")
    out = context.h2h(tg, recs, "A", "B", ASOF)
    assert len(out["games"]) == 8
    dates = [g["date"] for g in out["games"]]
    assert dates == sorted(dates, reverse=True)
    assert out["games"][0]["score"] in ("3-1", "1-3")
    assert out["summary"] == "Tasan 4–4 viimeisissä 8 kohtaamisessa"
    assert context.h2h(tg, [], "A", "B", ASOF) is None
    lead = [rec(i, "A", "B", f"2026-10-{10 + i:02d}", (3, 1)) for i in range(3)]
    assert context.h2h(tg, lead, "A", "B", ASOF)["summary"] == "A 3–0 viimeisissä 3 kohtaamisessa"


def test_game_context_isolates_failures(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(context, "h2h", boom)
    out = context.game_context(tg_rows("A", list("WL"), opp="B"), [], "A", "B", "20262027", ASOF)
    assert out["h2h"] is None
    assert out["form"]["home"]["last10"] == "1-1-0"


def test_drop_invalid_nulls_only_that_field():
    from pipeline import run

    games = [
        {"id": 1, "start_utc": "a", "form": {"x": 1}, "h2h": {"y": 1}},
        {"id": 2, "start_utc": "b", "form": {"x": 1}},
    ]
    assert run._drop_invalid(games, ["$.games[1].form.home: bad"]) is True
    assert games[1]["form"] is None and games[0]["form"] == {"x": 1}
    assert run._drop_invalid(games, ["$.games[0].xg: bad"]) is False
    assert run._drop_invalid(games, []) is False
