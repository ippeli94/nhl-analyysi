from datetime import date

import pandas as pd
import pytest

from pipeline import config, lineup, players
from pipeline.config import ModelParams

P = ModelParams()
ASOF = date(2025, 12, 31)
SEASON = "20252026"


def day(i: int) -> str:
    return (pd.Timestamp("2025-11-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")


def build(spec, n_games=12, team="AAA", others=()):
    """spec: pid -> (name, set of game indexes dressed, team override per index or None).

    Every game gives the team 3 goals and 3.0 xG; each dressed player gets ixg 0.3, pts 0.3
    on average via 1 point in every third dressed game."""
    rows, tgrows = [], []
    for i in range(n_games):
        gid = 100 + i
        tgrows.append({"game_id": gid, "team": team, "season": SEASON,
                       "date": pd.Timestamp(day(i)), "xg_reg": 3.0})
        for pid, (name, games, *_) in spec.items():
            if i in games:
                rows.append(_row(gid, i, team, pid, name, 0.3, i % 3 == 0))
    for pid, name, i, other in others:  # games for another team
        rows.append(_row(100 + i, i, other, pid, name, 0.3, False))
        tgrows.append({"game_id": 100 + i, "team": other, "season": SEASON,
                       "date": pd.Timestamp(day(i)), "xg_reg": 3.0})
    return pd.DataFrame(rows, columns=players.PG_COLUMNS), pd.DataFrame(tgrows)


def _row(gid, i, team, pid, name, ixg, point):
    return {"game_id": gid, "season": SEASON, "game_type": 2, "date": pd.Timestamp(day(i)),
            "team": team, "opp": "ZZZ", "player": pid, "name": name, "pos": "C", "grp": "F",
            "toi": 1200, "g": 0, "a": int(point), "pts": int(point), "ixg": ixg,
            "team_goals": 3}


def slot(pid, name):
    return {"id": pid, "name": name, "pos": "C", "toi_proj": 18.0}


def depth(extra=()):
    """Ten regular depth skaters present in every game."""
    d = {200 + k: (f"D. Depth{k}", set(range(12))) for k in range(10)}
    d.update(extra)
    return d


def lineup_of(spec, upto):
    return [slot(pid, n) for pid, (n, games, *_) in spec.items() if upto in games]


def run_team(pg, tg, lu, inj=(), rho=0.45):
    return lineup.team_lineup(pg, tg, "AAA", ASOF, P, SEASON, lu, list(inj), rho)


def test_star_absent_full_reduction():
    spec = depth({1: ("S. Star", set(range(11)))})  # missed only the last game
    pg, tg = build(spec)
    c = lineup.contributions(pg, tg, "AAA", ASOF, P, SEASON)
    res = run_team(pg, tg, lineup_of(spec, 11),
                   [{"name": "Sam Star", "short": "S. Star", "status": "out",
                     "detail": "x", "since": None}])
    ch = next(x for x in res["changes"] if x["player_id"] == 1)
    assert ch["kind"] == "out" and ch["source"] == "espn"
    pres = c[1].presence
    assert ch["impact_attack"] == pytest.approx(
        -c[1].share * (1 - config.LINEUP_RHO) * pres, abs=1e-3)
    assert ch["impact_attack"] < -0.005


def test_long_absence_near_zero():
    spec = depth({1: ("S. Star", {0})})  # dressed once, 11 games ago
    pg, tg = build(spec)
    res = run_team(pg, tg, lineup_of(spec, 11),
                   [{"name": "Sam Star", "short": "S. Star", "status": "out",
                     "detail": "x", "since": None}])
    ch = next(x for x in res["changes"] if x["player_id"] == 1)
    assert abs(ch["impact_attack"]) < 0.01


def test_returning_adds():
    spec = depth({1: ("R. Back", {0, 1, 2, 3, 4, 11})})
    pg, tg = build(spec)
    res = run_team(pg, tg, lineup_of(spec, 11))
    ch = [x for x in res["changes"] if x["player_id"] == 1]
    assert len(ch) == 1 and ch[0]["kind"] == "returning" and ch[0]["source"] == "nhl"
    assert ch[0]["impact_attack"] > 0.005


def test_traded_player_once():
    # A: AAA games 0..8, then BBB; B: BBB games 0..8, AAA from game 9 on
    spec = depth({1: ("A. Away", set(range(9))), 2: ("B. Come", set(range(9, 12)))})
    others = [(1, "A. Away", i, "BBB") for i in range(9, 12)]
    others += [(2, "B. Come", i, "BBB") for i in range(9)]
    pg, tg = build(spec, others=others)
    res = run_team(pg, tg, lineup_of(spec, 11))
    kinds = [(x["player_id"], x["kind"]) for x in res["changes"]
             if x["player_id"] in (1, 2)]
    assert sorted(kinds) == [(1, "traded_away"), (2, "acquired")]
    assert next(x for x in res["changes"] if x["player_id"] == 2)["impact_attack"] > 0


def test_espn_ambiguous_name():
    spec = depth({1: ("J. Smith", set(range(12))), 2: ("J. Smith", set(range(12)))})
    pg, tg = build(spec)
    res = run_team(pg, tg, lineup_of(spec, 11),
                   [{"name": "John Smith", "short": "J. Smith", "status": "out",
                     "detail": "knee", "since": "2025-12-20"}])
    ch = next(x for x in res["changes"] if x["player"] == "John Smith")
    assert ch["player_id"] is None and ch["impact_attack"] == 0 == ch["impact_defense"]
    assert res["espn_out_ids"] == []


def test_espn_unknown_team_skipped():
    raw = [{"displayName": "Nowhere Nobodies", "injuries": [
        {"status": "Out", "athlete": {"displayName": "A B", "shortName": "A. B"}}]}]
    assert lineup.parse_espn(raw) == {}


def test_parse_espn_status_and_names():
    raw = [{"displayName": "Montréal Canadiens", "injuries": [
        {"status": "Day-To-Day", "shortComment": "knee", "date": "2026-11-18T14:37Z",
         "athlete": {"displayName": "Cole Caufield", "shortName": "C. Caufield"}},
        {"status": "Injured Reserve", "shortComment": "hand",
         "athlete": {"displayName": "Kirby Dach", "shortName": "K. Dach"}}]}]
    out = lineup.parse_espn(raw)
    assert out["MTL"][0] == {"name": "Cole Caufield", "short": "C. Caufield",
                             "status": "doubtful", "detail": "Päivittäin arvioitava",
                             "since": "2026-11-18"}
    assert out["MTL"][1]["status"] == "out" and out["MTL"][1]["since"] is None


def test_clamp():
    spec = {1000 + k: (f"X. Star{k}", set(range(11))) for k in range(12)}
    pg, tg = build(spec)
    inj = [{"name": f"Star{k}", "short": f"X. Star{k}", "status": "out", "detail": "",
            "since": None} for k in range(12)]
    res = run_team(pg, tg, [], inj)
    assert res["m_att"] == pytest.approx(0.85) and res["m_def"] <= 1.15 + 1e-9
    assert len(res["changes"]) <= config.LINEUP_MAX_CHANGES


def test_inferred_out():
    spec = depth({1: ("I. Gone", set(range(10)))})  # games 0..9 of 12: missed last 2
    pg, tg = build(spec)
    res = run_team(pg, tg, lineup_of(spec, 11))
    ch = next(x for x in res["changes"] if x["player_id"] == 1)
    assert ch["kind"] == "out" and ch["source"] == "päätelty"


def test_changes_validate_against_schema_and_degrade():
    import json

    import jsonschema

    from pipeline import run

    spec = depth({1: ("S. Star", set(range(11)))})
    pg, tg = build(spec)
    res = run_team(pg, tg, lineup_of(spec, 11),
                   [{"name": "Sam Star", "short": "S. Star", "status": "out",
                     "detail": None, "since": "2025-12-20"}])
    schema = json.loads(config.path("pipeline", "schema", "latest.schema.json")
                        .read_text(encoding="utf-8"))
    sub = {"$ref": "#/$defs/lineup_change", "$defs": schema["$defs"]}
    for ch in res["changes"]:
        jsonschema.validate(ch, sub)
    assert res["espn_out_ids"] == [1]
    game = {"id": 1, "home": {"abbr": "AAA"}, "away": {"abbr": "BBB"}}
    assert run._lineup_block(None, tg, game, ASOF, P, SEASON, {}) == (None, None)
    empty, mults = run._lineup_block(pg.iloc[:0], tg, game, ASOF, P, SEASON, {})
    assert empty == [] and mults["home"]["m_att"] == 1.0


def test_defense_impact_uses_share_of_skater_time():
    spec = depth({1: ("M. Blue", set(range(11)))})
    pg, tg = build(spec)
    pg.loc[pg.player == 1, ["toi", "pos", "grp"]] = [1500, "D", "D"]
    res = run_team(pg, tg, lineup_of(spec, 11),
                   [{"name": "Mac Blue", "short": "M. Blue", "status": "out",
                     "detail": "Poissa", "since": None}])
    ch = next(x for x in res["changes"] if x["player_id"] == 1)
    c = lineup.contributions(pg, tg, "AAA", ASOF, P, SEASON)[1]
    assert ch["impact_defense"] == pytest.approx(0.5 * (1500 / 18000) * c.presence, abs=2e-3)
    assert 0.03 < ch["impact_defense"] < 0.045 and res["m_def"] < 1.15


def test_doubtful_expectation_half():
    spec = depth({1: ("S. Star", set(range(12)))})
    pg, tg = build(spec)
    c = lineup.contributions(pg, tg, "AAA", ASOF, P, SEASON)
    expect, entries = lineup.expectations(
        c, lineup_of(spec, 11),
        [{"name": "Sam Star", "short": "S. Star", "status": "doubtful", "detail": "x",
          "since": None}], pg, "AAA", ASOF, SEASON)
    assert expect[1] == 0.5 and entries[0]["kind"] == "doubtful"


def test_returning_only_within_last_three_games():
    spec = depth({1: ("R. Back", {0, 1, 2, 3, 4, 7, 8, 9, 10, 11})})  # back 5 games ago
    pg, tg = build(spec)
    res = run_team(pg, tg, lineup_of(spec, 11))
    assert not [x for x in res["changes"] if x["player_id"] == 1]


def test_espn_detail_finnish():
    raw = [{"displayName": "Boston Bruins", "injuries": [
        {"status": "Suspension", "shortComment": "x",
         "athlete": {"displayName": "A B", "shortName": "A. B"}}]}]
    assert lineup.parse_espn(raw)["BOS"][0]["detail"] == "Pelikielto"
