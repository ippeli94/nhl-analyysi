import pytest  # noqa: F401

from pipeline import insights, teams, teamstats
from pipeline.teamstats import TeamStats


def ts(**kw):
    base = dict(gp=30, n_ranked=32)
    base.update(kw)
    return TeamStats(**base)


def game(home="CAR", away="SEA", **kw):
    g = {
        "id": 1,
        "home": {"abbr": home, "name": "Carolina Hurricanes"},
        "away": {"abbr": away, "name": "Seattle Kraken"},
        "factors": [{"key": "goalie", "label": "Maalivahti", "home": 1.0, "away": 1.0}],
        "goalies": {"home": None, "away": None},
        "odds": None, "h2h": None, "lineup_changes": None,
    }
    g.update(kw)
    return g


def run(stats, **kw):
    return insights.game_insights(game(**kw), stats)


def ids(out):
    return [i["id"] for i in out]


def test_b2b_positive():
    out = run({"CAR": ts(), "SEA": ts(played_yesterday=True)})
    hit = next(i for i in out if i["id"] == "b2b")
    assert hit["team"] == "SEA" and hit["severity"] == 2
    assert hit["text"] == "Seattle pelaa toisena iltana peräkkäin, Carolina levänneenä."


def test_b2b_negative_when_both_played():
    out = run({"CAR": ts(played_yesterday=True), "SEA": ts(played_yesterday=True)})
    assert "b2b" not in ids(out)


def test_streak_positive_and_negative():
    out = run({"CAR": ts(streak="V5"), "SEA": ts(streak="H3")})
    hit = [i for i in out if i["id"] == "streak"]
    assert len(hit) == 1 and hit[0]["team"] == "CAR"
    assert "voittanut 5 peräkkäin" in hit[0]["text"]
    out = run({"CAR": ts(streak="V3", point_streak=5), "SEA": ts(streak="H3")})
    assert "streak" not in ids(out)
    out = run({"CAR": ts(streak="H1", point_streak=6), "SEA": ts()})
    assert "streak" in ids(out)


def test_period_swing_and_suppression():
    out = run({"CAR": ts(period_xg_share=[0.60, 0.40, 0.52]), "SEA": ts()})
    assert "period_swing" in ids(out)
    assert "period_strength" not in ids(out) and "period_weakness" not in ids(out)


def test_period_swing_negative_gives_strength_only():
    out = run({"CAR": ts(period_xg_share=[0.56, 0.50, 0.50]), "SEA": ts()})
    assert "period_swing" not in ids(out)
    assert "period_strength" in ids(out) and "period_weakness" not in ids(out)
    assert "1. erää" in next(i for i in out if i["id"] == "period_strength")["text"]


def test_period_weakness_positive():
    out = run({"CAR": ts(period_xg_share=[0.50, 0.44, 0.50]), "SEA": ts()})
    assert "period_weakness" in ids(out)


def test_period_matchup():
    out = run({"CAR": ts(period_xg_share=[0.50, 0.50, 0.57]),
               "SEA": ts(period_xg_share=[0.50, 0.50, 0.43])})
    hit = next(i for i in out if i["id"] == "period_matchup")
    assert hit["team"] == "CAR" and hit["severity"] == 1
    assert hit["text"] == "Seattlen heikoin erä (3.) on Carolinan vahvin."
    out = run({"CAR": ts(period_xg_share=[0.57, 0.50, 0.50]),
               "SEA": ts(period_xg_share=[0.50, 0.50, 0.43])})
    assert "period_matchup" not in ids(out)


def test_third_period_collapse():
    out = run({"CAR": ts(led_after2=9, led_after2_won=5),
               "SEA": ts(led_after2=9, led_after2_won=7)})
    hit = [i for i in out if i["id"] == "third_period_collapse"]
    assert [h["team"] for h in hit] == ["CAR"]
    out = run({"CAR": ts(led_after2=5, led_after2_won=1), "SEA": ts()})
    assert "third_period_collapse" not in ids(out)


def test_pp_vs_pk():
    out = run({"CAR": ts(pp=0.26, pp_rank=4), "SEA": ts(pk=0.74, pk_rank=26)})
    hit = next(i for i in out if i["id"] == "pp_vs_pk")
    assert hit["team"] == "CAR"
    assert hit["text"] == "Carolinan ylivoima (26 %, 4.) kohtaa Seattlen alivoiman (74 %, 26.)."
    out = run({"CAR": ts(pp=0.26, pp_rank=4), "SEA": ts(pk=0.80, pk_rank=24)})
    assert "pp_vs_pk" not in ids(out)  # 24 is not in the bottom 8 of 32


def test_model_vs_market():
    odds = {"fetched_at": "x", "picks": [], "markets": [
        {"key": "h2h", "line": None, "outcomes": [{"key": "home", "flag": "suspicious"}]}]}
    out = run({}, odds=odds)
    hit = next(i for i in out if i["id"] == "model_vs_market")
    assert hit["team"] is None and hit["severity"] == 3
    assert hit["text"] == ("Malli ja markkina ovat eri mieltä yli 10 prosenttiyksikköä "
                           "— tarkista poissaolot.")
    odds["markets"][0]["outcomes"][0]["flag"] = None
    assert "model_vs_market" not in ids(run({}, odds=odds))
    odds["markets"][0]["key"] = "totals"
    odds["markets"][0]["outcomes"][0]["flag"] = "suspicious"
    assert "model_vs_market" not in ids(run({}, odds=odds))


def change(team, kind, impact, **kw):
    d = {"team": team, "player": "Jesper Bratt", "player_id": 1, "kind": kind,
         "source": "x", "detail": None, "since": None,
         "impact_attack": impact, "impact_defense": 0.0}
    d.update(kw)
    return d


def test_key_absence():
    out = run({}, lineup_changes=[change("SEA", "out", -0.03)])
    hit = next(i for i in out if i["id"] == "key_absence")
    assert hit["team"] == "SEA" and hit["severity"] == 3
    assert "−3 %" in hit["text"] and "Jesper Bratt" in hit["text"]
    for c in (change("SEA", "out", -0.01), change("SEA", "returning", -0.05),
              change("SEA", "out", None)):
        assert "key_absence" not in ids(run({}, lineup_changes=[c]))
    assert "key_absence" not in ids(run({}, lineup_changes=None))


def test_return_or_trade():
    out = run({}, lineup_changes=[change("CAR", "acquired", 0.025)])
    hit = next(i for i in out if i["id"] == "return_or_trade")
    assert hit["team"] == "CAR" and hit["severity"] == 2
    assert "return_or_trade" not in ids(run({}, lineup_changes=[change("CAR", "acquired", 0.01)]))


def meetings(winners):
    games = []
    for w in winners:
        score = "4-2" if w == "CAR" else "2-4"
        games.append({"date": "2026-01-01", "home": "CAR", "away": "SEA", "score": score,
                      "xg": None})
    return {"games": games, "summary": "x"}


def test_h2h_dominance():
    out = run({}, h2h=meetings(["CAR"] * 5 + ["SEA"]))
    hit = next(i for i in out if i["id"] == "h2h_dominance")
    assert hit["team"] == "CAR" and "(pieni otos)" in hit["text"]
    assert "5 viimeisestä 6" in hit["text"]
    assert "h2h_dominance" not in ids(run({}, h2h=meetings(["CAR"] * 4 + ["SEA"] * 2)))
    assert "h2h_dominance" not in ids(run({}, h2h=meetings(["CAR"] * 4)))
    assert "h2h_dominance" not in ids(run({}, h2h=None))


def test_goalie_edge_direction():
    goalie = [{"key": "goalie", "label": "x", "home": 1.05, "away": 1.0}]
    hit = next(i for i in run({}, factors=goalie) if i["id"] == "goalie_edge")
    assert hit["team"] == "CAR"
    goalie = [{"key": "goalie", "label": "x", "home": 0.95, "away": 1.0}]
    hit = next(i for i in run({}, factors=goalie) if i["id"] == "goalie_edge")
    assert hit["team"] == "SEA"
    goalie = [{"key": "goalie", "label": "x", "home": 1.02, "away": 1.0}]
    assert "goalie_edge" not in ids(run({}, factors=goalie))


def test_min_sample():
    loud = dict(gp=5, streak="V6", point_streak=8, period_xg_share=[0.7, 0.3, 0.5],
                led_after2=9, led_after2_won=3, scored_first=12, scored_first_won=12,
                pp=0.3, pp_rank=1, pk=0.6, pk_rank=32, pim_rank=1, luck_last10=1.0,
                xgf_pct_last10=0.7, xgf_pct_season=0.5, pts_pct=0.8, pts_pct_last=0.4,
                home_pts_pct=0.9, road_pts_pct=0.2, trailed_after2=8, trailed_after2_won=5)
    assert run({"CAR": ts(**loud), "SEA": ts(**loud)}) == []


def test_schedule_and_form_rules():
    out = run({"CAR": ts(road_run=1), "SEA": ts(road_run=4, games_last_4d=1)})
    hit = next(i for i in out if i["id"] == "schedule_load")
    assert hit["team"] == "SEA" and "neljäs peräkkäinen vierasottelu" in hit["text"]
    out = run({"CAR": ts(xgf_pct_last10=0.58, xgf_pct_season=0.52, luck_last10=0.7,
                         scored_first=12, scored_first_won=10),
               "SEA": ts(pts_pct=0.68, pts_pct_last=0.55, home_pts_pct=0.8, road_pts_pct=0.5,
                         trailed_after2=8, trailed_after2_won=4)})
    assert {"form_vs_season", "luck", "vs_last_season", "home_road_split",
            "first_goal", "comeback"} <= set(ids(out))


def test_caps_and_order():
    many = dict(streak="V6", period_xg_share=[0.60, 0.40, 0.50], led_after2=9, led_after2_won=4,
                pp=0.26, pp_rank=2, pk=0.70, pk_rank=30, pim_rank=2, luck_last10=0.8,
                xgf_pct_last10=0.6, xgf_pct_season=0.5, scored_first=12, scored_first_won=11)
    stats = {"CAR": ts(**many, played_yesterday=False), "SEA": ts(**many, played_yesterday=True)}
    odds = {"fetched_at": "x", "picks": [], "markets": [
        {"key": "1x2", "line": None, "outcomes": [{"key": "home", "flag": "suspicious"}]}]}
    out = run(stats, odds=odds, lineup_changes=[change("SEA", "out", -0.04)])
    assert len(out) == 6
    sev = [i["severity"] for i in out]
    assert sev == sorted(sev, reverse=True)
    for team in ("CAR", "SEA"):
        assert sum(1 for i in out if i["team"] == team) <= 3
    assert out[0]["severity"] == 3


def test_texts_use_decimal_comma():
    out = run({"CAR": ts(luck_last10=0.8), "SEA": ts()})
    luck = next(i for i in out if i["id"] == "luck")
    assert "0,8" in luck["text"] and "0.8" not in luck["text"]


def test_small_sample_note():
    out = run({"CAR": ts(gp=12, luck_last10=0.8), "SEA": ts()})
    assert next(i for i in out if i["id"] == "luck")["text"].endswith("(12 ottelua).")


def test_missing_stats_are_skipped():
    assert run({}) == []


def test_short_names():
    assert teams.short_name("CAR", "x") == "Carolina"
    assert teams.short_name("TOR", "x") == "Toronto"
    assert teams.short_name("VGK", "x") == "Vegas"
    assert teams.short_name("TBL", "x") == "Tampa Bay"
    assert teams.short_name("NYR", "x") == "NY Rangers"
    assert teams.short_name("ZZZ", "Zed") == "Zed"
    assert teams.genitive("Carolina") == "Carolinan"
    assert teams.genitive("Boston") == "Bostonin"


def test_schedule_stats_copies_not_mutates(monkeypatch):
    monkeypatch.setattr(teamstats, "schedule_load", lambda tg, t, d, h, s: (True, 3, 4))
    shared = {"CAR": ts(), "SEA": ts()}
    g = game(nhl_date="2026-12-01")
    out = insights.schedule_stats(shared, None, g, "20262027")
    assert out["CAR"].played_yesterday and out["CAR"].road_run == 4
    assert shared["CAR"].road_run == 0 and out["CAR"] is not shared["CAR"]
