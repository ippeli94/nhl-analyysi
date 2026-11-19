"""Pre-game context: recent form, season comparison and head-to-head (Finnish UI data)."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from pipeline import config
from pipeline.ratings import Ratings, base_lambda

logger = logging.getLogger(__name__)

FAR_FUTURE = date(2999, 1, 1)
PP_MINUTES = (2, 4, 5)


def _prev_season(season: str) -> str:
    return f"{int(season[:4]) - 1}{int(season[4:]) - 1}"


def _rows(tg: pd.DataFrame, team: str, asof: date, season: str | None = None) -> pd.DataFrame:
    """The team's regular-season rows before `asof`, oldest first."""
    if tg is None or len(tg) == 0:
        return tg.iloc[0:0] if tg is not None else pd.DataFrame()
    m = (tg["team"] == team) & (tg["game_type"] == 2) & (tg["date"] < pd.Timestamp(asof))
    if season is not None:
        m &= tg["season"] == season
    return tg[m].sort_values(["date", "game_id"], kind="stable")


def _letter(row) -> str:
    if bool(row["won"]):
        return "V"
    return "J" if row["decided"] in ("OT", "SO") else "H"


def _goals(df: pd.DataFrame) -> tuple[int, int]:
    """(gf, ga): regulation goals, plus one for the OT winner; shootout adds nothing."""
    gf = int(df["score_reg"].sum())
    ga = int(df["opp_score_reg"].sum())
    ot = df["decided"] == "OT"
    gf += int((ot & df["won"].astype(bool)).sum())
    ga += int((ot & ~df["won"].astype(bool)).sum())
    return gf, ga


def _xgf_pct(df: pd.DataFrame, tg: pd.DataFrame) -> float | None:
    if len(df) == 0:
        return None
    opp = tg[["game_id", "team", "xg_reg"]].rename(columns={"team": "opp", "xg_reg": "opp_xg"})
    merged = df[["game_id", "opp", "xg_reg"]].merge(opp, on=["game_id", "opp"], how="inner")
    if len(merged) == 0:
        return None
    mine, theirs = float(merged["xg_reg"].sum()), float(merged["opp_xg"].sum())
    return round(mine / (mine + theirs), 3) if mine + theirs > 0 else None


def form(tg: pd.DataFrame, team: str, asof: date, n: int = config.FORM_GAMES) -> dict | None:
    rows = _rows(tg, team, asof).tail(n)
    if len(rows) == 0:
        return None
    rows = rows.iloc[::-1]  # newest first
    seq = [_letter(r) for _, r in rows.iterrows()]
    w, l_, otl = seq.count("V"), seq.count("H"), seq.count("J")
    run = 1
    while run < len(seq) and seq[run] == seq[0]:
        run += 1
    gf, ga = _goals(rows)
    return {
        "seq": seq, "last10": f"{w}-{l_}-{otl}", "streak": f"{seq[0]}{run}",
        "gf": gf, "ga": ga, "xgf_pct": _xgf_pct(rows, tg),
    }


def _date_ok(rec: dict, season: str, asof: date) -> bool:
    return (rec.get("season") == season and rec.get("game_type") == 2
            and rec["date"] < asof.isoformat())


def special_teams(
    records: list[dict], team: str, season: str, asof: date
) -> tuple[float | None, float | None]:
    """(pp, pk). Coincidental minors are not netted off (decision 29)."""
    opps = short = pp_for = pp_against = 0
    for rec in records:
        teams = (rec["home"]["abbr"], rec["away"]["abbr"])
        if not _date_ok(rec, season, asof) or team not in teams:
            continue
        for pen in rec.get("penalties") or []:
            if pen.get("min") in PP_MINUTES:
                if pen["team"] == team:
                    short += 1
                else:
                    opps += 1
        for s in rec.get("shots") or []:
            if s.get("ev") == "goal" and s.get("strength") == "pp":
                if s["team"] == team:
                    pp_for += 1
                else:
                    pp_against += 1
    pp = round(pp_for / opps, 3) if opps else None
    pk = round(1 - pp_against / short, 3) if short else None
    return pp, pk


def season_line(tg, records, team: str, season: str, asof: date) -> dict | None:
    rows = _rows(tg, team, asof, season)
    gp = len(rows)
    if gp == 0:
        return None
    won = rows["won"].astype(bool)
    pts = int((won * 2).sum() + ((~won) & rows["decided"].isin(["OT", "SO"])).sum())
    gf, ga = _goals(rows)
    pp, pk = special_teams(records, team, season, asof)
    return {
        "gp": gp, "pts_pct": round(pts / (2 * gp), 3), "gf_pg": round(gf / gp, 2),
        "ga_pg": round(ga / gp, 2), "xgf_pct": _xgf_pct(rows, tg), "pp": pp, "pk": pk,
    }


def season_compare(tg, records, team: str, season: str, asof: date) -> dict | None:
    now = season_line(tg, records, team, season, asof)
    last = season_line(tg, records, team, _prev_season(season), FAR_FUTURE)
    if now is None and last is None:
        return None
    return {"now": now, "last": last}


H2H_GAME_TYPES = (2, 3)


def _meetings(items, key, pair: set[str], asof: date) -> list:
    """Newest-first meetings of `pair` before `asof` (regular season and playoffs), at most
    H2H_MAX_GAMES. key(item) -> (teams, game_type, iso date, game id)."""
    keyed = []
    for item in items:
        teams, game_type, day, gid = key(item)
        if set(teams) == pair and game_type in H2H_GAME_TYPES and day < asof.isoformat():
            keyed.append(((day, gid), item))
    keyed.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in keyed[: config.H2H_MAX_GAMES]]


def _record_key(r: dict):
    return (r["home"]["abbr"], r["away"]["abbr"]), r.get("game_type"), r["date"], r["id"]


def h2h(tg, records, home: str, away: str, asof: date) -> dict | None:
    meetings = _meetings(records, _record_key, {home, away}, asof)
    if not meetings:
        return None
    xg = {}
    if tg is not None and len(tg):
        sub = tg[tg["game_id"].isin([r["id"] for r in meetings])]
        xg = {
            (int(a), b): float(c)
            for a, b, c in zip(sub["game_id"], sub["team"], sub["xg_reg"], strict=True)
        }
    games, wins = [], {home: 0, away: 0}
    for r in meetings:
        h, a = r["home"]["abbr"], r["away"]["abbr"]
        hs, as_ = r["score"]["home"], r["score"]["away"]
        wins[h if hs > as_ else a] += 1
        hx, ax = xg.get((r["id"], h)), xg.get((r["id"], a))
        games.append({
            "date": r["date"], "home": h, "away": a, "score": f"{hs}-{as_}",
            "xg": f"{hx:.1f}-{ax:.1f}" if hx is not None and ax is not None else None,
        })
    n = len(meetings)
    hw, aw = wins[home], wins[away]
    if hw == aw:
        summary = f"Tasan {hw}–{aw} viimeisissä {n} kohtaamisessa"
    else:
        lead, w, l_ = (home, hw, aw) if hw > aw else (away, aw, hw)
        summary = f"{lead} {w}–{l_} viimeisissä {n} kohtaamisessa"
    return {"summary": summary, "games": games}


def h2h_mult(tg: pd.DataFrame, r: Ratings, team: str, opp: str, asof: date) -> float:
    """Shrunk head-to-head scoring ratio of `team` against `opp` (spec 6.7)."""
    if tg is None or len(tg) == 0:
        return 1.0
    mine = tg[(tg["team"] == team) & (tg["opp"] == opp)]
    rows = _meetings(
        list(mine.itertuples(index=False)),
        lambda x: ((x.team, x.opp), int(x.game_type), pd.Timestamp(x.date).date().isoformat(),
                   int(x.game_id)),
        {team, opp}, asof)
    n = len(rows)
    if n == 0:
        return 1.0
    expected = n * base_lambda(r, team, opp)
    if expected <= 0:
        return 1.0
    ratio = sum(float(x.goals_reg) for x in rows) / expected
    m = 1 + (ratio - 1) * n / (n + config.H2H_PRIOR_GAMES)
    lo, hi = config.H2H_CLAMP
    return min(max(m, lo), hi)


def game_context(tg, records, home: str, away: str, season: str, asof: date) -> dict:
    """The three context blocks; a failing block becomes None and never fails the game."""
    def safe(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - context must never fail a prediction
            logger.warning("context %s failed for %s-%s: %s", name, home, away, type(exc).__name__)
            return None

    return {
        "form": safe("form", lambda: {"home": form(tg, home, asof), "away": form(tg, away, asof)}),
        "season_compare": safe("season_compare", lambda: {
            "home": season_compare(tg, records, home, season, asof),
            "away": season_compare(tg, records, away, season, asof),
        }),
        "h2h": safe("h2h", lambda: h2h(tg, records, home, away, asof)),
    }
