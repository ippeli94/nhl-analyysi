"""Lineup changes and lineup multipliers from box scores and ESPN injuries (spec 6.6)."""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from datetime import date

import pandas as pd

from pipeline import config, ratings, teams
from pipeline.config import ModelParams
from pipeline.players import Slot

logger = logging.getLogger(__name__)


def _norm(name) -> str:
    if not isinstance(name, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_ = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_.casefold().replace(".", "").split())


def parse_espn(raw: list[dict]) -> dict[str, list[dict]]:
    """ESPN injury payload -> abbr -> entries; teams that do not map to a club are skipped."""
    out: dict[str, list[dict]] = {}
    for team in raw or []:
        abbr = teams.abbr_from_name(str(team.get("displayName", "")))
        if abbr is None:
            continue
        for inj in team.get("injuries") or []:
            athlete = inj.get("athlete") or {}
            full = athlete.get("displayName")
            if not full:
                continue
            day_to_day = str(inj.get("status", "")).strip().lower() == "day-to-day"
            stamp = inj.get("date")
            out.setdefault(abbr, []).append({
                "name": full, "short": athlete.get("shortName") or full,
                "status": "doubtful" if day_to_day else "out",
                "detail": inj.get("shortComment") or None,
                "since": stamp[:10] if isinstance(stamp, str) and len(stamp) >= 10 else None,
            })
    return out


@dataclass
class Contrib:
    player: int
    name: str | None
    grp: str
    share: float
    presence: float
    toi_share: float


def _league_ppg(pg: pd.DataFrame, asof: date) -> float:
    before = pg[pg["date"] < pd.Timestamp(asof)]
    goals = float(before["g"].sum())
    return float(before["pts"].sum()) / goals if goals > 0 else config.LINEUP_PPG_FALLBACK


def contributions(
    pg: pd.DataFrame, tg: pd.DataFrame, team: str, asof: date, p: ModelParams, season: str,
    extra=(),
) -> dict[int, Contrib]:
    """Share of the team's attack, presence in its games and TOI share of its skaters.

    The universe is every player with presence > 0 this season plus `extra` (the lineup).
    Share uses all of a player's games this season, whichever team he dressed for; presence
    uses only this team's games."""
    ts = pd.Timestamp(asof)
    cur = pg[(pg["season"] == season) & (pg["date"] < ts)]
    games = tg[(tg["team"] == team) & (tg["season"] == season) & (tg["date"] < ts)]
    if games.empty:
        return {}
    gw = pd.Series(ratings.weights(games, asof, p, season), index=games["game_id"].to_numpy())
    total = float(gw.sum())
    if total <= 0:
        return {}
    mine = cur[cur["team"] == team]
    wm = mine["game_id"].map(gw).fillna(0.0)
    presence = wm.groupby(mine["player"]).sum() / total
    ids = {int(i) for i in presence[presence > 0].index} | {int(i) for i in extra}
    if not ids:
        return {}

    rows = cur[cur["player"].isin(ids)].merge(
        tg[["game_id", "team", "xg_reg"]], on=["game_id", "team"], how="left")
    rows["w"] = ratings.weights(rows, asof, p, season)
    ppg = _league_ppg(pg, asof)
    out: dict[int, Contrib] = {}
    for pid in sorted(ids):
        r = rows[rows["player"] == pid].sort_values(["date", "game_id"], kind="stable")
        name, grp, share, toi = None, "F", 0.0, 0.0
        if len(r):
            w = r["w"]
            xg_den = float((w * r["xg_reg"].fillna(0.0)).sum())
            pts_den = float((w * r["team_goals"]).sum()) * ppg
            xg_part = float((w * r["ixg"]).sum()) / xg_den if xg_den > 0 else 0.0
            pts_part = float((w * r["pts"]).sum()) / pts_den if pts_den > 0 else 0.0
            share = 0.5 * xg_part + 0.5 * pts_part
            toi = float(r["toi"].mean()) / 3600.0
            names = r["name"].dropna()
            name = str(names.iloc[-1]) if len(names) else None
            grp = str(r["grp"].iloc[-1])
        out[pid] = Contrib(pid, name, grp, share, float(presence.get(pid, 0.0)), toi)
    return out


def _team_history(pg: pd.DataFrame, team: str, asof: date):
    d = pg[(pg["team"] == team) & (pg["date"] < pd.Timestamp(asof))]
    order = d[["game_id", "date"]].drop_duplicates().sort_values(
        ["date", "game_id"], kind="stable")
    ids = [int(g) for g in order["game_id"]]
    dates = {int(g): dt for g, dt in zip(order["game_id"], order["date"], strict=True)}
    dressed = {int(g): set(map(int, s)) for g, s in d.groupby("game_id")["player"]}
    return ids, dates, dressed


def _entry(team, player, pid, kind, source, detail, since):
    return {"team": team, "player": player, "player_id": pid, "kind": kind, "source": source,
            "detail": detail, "since": since, "impact_attack": None, "impact_defense": None}


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def expectations(
    contrib: dict[int, Contrib], lineup: list[Slot], injuries: list[dict], pg: pd.DataFrame,
    team: str, asof: date,
) -> tuple[dict[int, float], list[dict]]:
    """Expected presence per player (0, 0.5 or 1) and the change entries (no impacts yet)."""
    in_lineup = {int(s["id"]) for s in lineup}
    expect = {pid: (1.0 if pid in in_lineup else 0.0) for pid in contrib}
    expect.update({pid: 1.0 for pid in in_lineup})
    names: dict[int, str | None] = {pid: c.name for pid, c in contrib.items()}
    names.update({int(s["id"]): s.get("name") for s in lineup})
    by_name: dict[str, list[int]] = {}
    for pid, n in names.items():
        by_name.setdefault(_norm(n), []).append(pid)

    entries: list[dict] = []
    espn_ids: set[int] = set()
    for inj in injuries:
        kind = "doubtful" if inj.get("status") == "doubtful" else "out"
        hits = by_name.get(_norm(inj.get("short") or inj.get("name")), [])
        pid = hits[0] if len(hits) == 1 else None
        if pid is not None:
            expect[pid] = 0.5 if kind == "doubtful" else 0.0
            espn_ids.add(pid)
        entries.append(_entry(team, inj.get("name") or inj.get("short"), pid, kind, "espn",
                              inj.get("detail"), inj.get("since")))

    order, dates, dressed = _team_history(pg, team, asof)
    if not order:
        return expect, entries
    last = order[-1]
    window = order[-config.LINEUP_WINDOW:]
    season = pg[pg["game_id"] == last]["season"].iloc[0]
    cur = pg[(pg["season"] == season) & (pg["date"] < pd.Timestamp(asof))]
    for pid in sorted(expect):
        if pid in espn_ids:
            continue
        rows = cur[cur["player"] == pid].sort_values(["date", "game_id"], kind="stable")
        if rows.empty:
            continue
        name = names.get(pid)
        here = rows[rows["team"] == team]
        other = rows[rows["team"] != team]
        if rows["team"].iloc[-1] != team:
            if contrib.get(pid) is not None and contrib[pid].presence > 0:
                new = rows["team"].iloc[-1]
                since = _iso(rows[rows["team"] == new]["date"].iloc[0])
                entries.append(_entry(team, name, pid, "traded_away", "nhl",
                                      f"Siirtynyt joukkueeseen {new}", since))
        elif pid in in_lineup:
            first_idx = order.index(int(here["game_id"].iloc[0]))
            if len(other) and first_idx >= len(order) - len(window):
                entries.append(_entry(team, name, pid, "acquired", "nhl",
                                      f"Siirtynyt joukkueesta {other['team'].iloc[-1]}",
                                      _iso(here["date"].iloc[0])))
                continue
            before = order[-1 - config.LINEUP_WINDOW:-1]
            seen = sum(1 for g in before if pid in dressed.get(g, ()))
            if len(before) > config.LINEUP_WINDOW // 2 and seen <= 5 and len(here) >= 2:
                entries.append(_entry(team, name, pid, "returning", "nhl",
                                      "Palaa kokoonpanoon", _iso(dates[last])))
        elif len(order) >= config.LINEUP_WINDOW:
            seen = sum(1 for g in window if pid in dressed.get(g, ()))
            missed_two = all(pid not in dressed.get(g, ()) for g in order[-2:])
            if seen >= 8 and missed_two:
                entries.append(_entry(team, name, pid, "out", "päätelty",
                                      "Ei pelannut kahdessa viimeisessä ottelussa",
                                      _iso(dates[order[-2]])))
    return expect, entries


def multipliers(
    contrib: dict[int, Contrib], expect: dict[int, float], rho: float
) -> tuple[float, float, dict[int, tuple[float, float]]]:
    """(m_att, m_def, per-player (impact_attack, impact_defense)); both multipliers clamped."""
    lo, hi = config.LINEUP_CLAMP
    m_att = m_def = 1.0
    per: dict[int, tuple[float, float]] = {}
    for pid, c in contrib.items():
        e = expect.get(pid, 0.0)
        att = c.share * (1.0 - rho) * (e - c.presence)
        dfn = (config.LINEUP_KAPPA.get(c.grp, config.LINEUP_KAPPA["F"]) * c.toi_share
               * (c.presence - e))
        m_att += att
        m_def += dfn
        per[pid] = (att, dfn)
    return min(max(m_att, lo), hi), min(max(m_def, lo), hi), per


def team_lineup(
    pg: pd.DataFrame, tg: pd.DataFrame, team: str, asof: date, p: ModelParams, season: str,
    lineup: list[Slot], injuries: list[dict], rho: float,
) -> dict:
    """{"m_att", "m_def", "changes", "espn_out_ids"} for one team in one game."""
    contrib = contributions(pg, tg, team, asof, p, season, [s["id"] for s in lineup])
    expect, entries = expectations(contrib, lineup, injuries, pg, team, asof)
    m_att, m_def, per = multipliers(contrib, expect, rho)
    changes = []
    for e in entries:
        pid = e["player_id"]
        att, dfn = per.get(pid, (0.0, 0.0)) if pid is not None else (0.0, 0.0)
        e["impact_attack"], e["impact_defense"] = round(att, 3) + 0.0, round(dfn, 3) + 0.0
        if e["source"] == "espn" or max(abs(att), abs(dfn)) >= config.LINEUP_MIN_IMPACT:
            changes.append(e)
    changes.sort(key=lambda e: -abs(e["impact_attack"]))
    espn_out = sorted(
        e["player_id"] for e in entries
        if e["source"] == "espn" and e["kind"] == "out" and e["player_id"] is not None)
    return {"m_att": m_att, "m_def": m_def, "changes": changes[:config.LINEUP_MAX_CHANGES],
            "espn_out_ids": espn_out}
