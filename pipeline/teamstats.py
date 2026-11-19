"""League-wide team statistics that feed the insight rules."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from pipeline import config, context, xg

PERIODS = (1, 2, 3)


@dataclass
class TeamStats:
    """played_yesterday, games_last_4d and road_run are filled by the caller from schedule_load."""

    gp: int = 0
    period_xg_share: list[float | None] | None = None
    led_after2: int = 0
    led_after2_won: int = 0
    trailed_after2: int = 0
    trailed_after2_won: int = 0
    scored_first: int = 0
    scored_first_won: int = 0
    pp: float | None = None
    pk: float | None = None
    pp_rank: int | None = None
    pk_rank: int | None = None
    pim_rank: int | None = None
    home_pts_pct: float | None = None
    road_pts_pct: float | None = None
    home_gp: int = 0
    road_gp: int = 0
    pts_pct: float | None = None
    pts_pct_last: float | None = None
    gp_last: int = 0
    n_ranked: int = 0  # teams with gp >= INSIGHT_MIN_GAMES used in the ranks
    xgf_pct_season: float | None = None
    xgf_pct_last10: float | None = None
    luck_last10: float | None = None
    streak: str | None = None
    point_streak: int = 0
    played_yesterday: bool = False
    games_last_4d: int = 0
    road_run: int = 0


def _points(rows: pd.DataFrame) -> pd.Series:
    won = rows["won"].astype(bool)
    return won * 2 + ((~won) & rows["decided"].isin(["OT", "SO"])).astype(int)


def _pts_pct(rows: pd.DataFrame) -> float | None:
    if len(rows) == 0:
        return None
    return round(int(_points(rows).sum()) / (2 * len(rows)), 3)


def _rank(values: dict[str, float | None], eligible: set[str], best_high: bool = True):
    pool = {t: v for t, v in values.items() if t in eligible and v is not None}
    ranks = {}
    for t, v in pool.items():
        better = sum(1 for o in pool.values() if (o > v if best_high else o < v))
        ranks[t] = better + 1
    return ranks


def _period_xg(records: list[dict], coefs: dict) -> dict[tuple[int, str, int], float]:
    shots = xg.shots_frame(records)
    shots = shots[(shots["p"].isin(PERIODS)) & (~shots["empty_net"].astype(bool))].copy()
    if len(shots) == 0:
        return {}
    shots["xg"] = xg.score(shots, coefs)
    grouped = shots.groupby(["game_id", "team", "p"])["xg"].sum()
    return {(int(g), t, int(p)): float(v) for (g, t, p), v in grouped.items()}


def _score_states(rec: dict) -> dict[str, dict]:
    """Per side: led/trailed after two periods and who scored first."""
    goals = sorted(
        (s for s in rec.get("shots") or [] if s.get("ev") == "goal" and s["p"] <= 4),
        key=lambda s: (s["p"], s["t"]),
    )
    home, away = rec["home"]["abbr"], rec["away"]["abbr"]
    after2 = [s["team"] for s in goals if s["p"] <= 2]
    diff = after2.count(home) - after2.count(away)
    first = goals[0]["team"] if goals else None
    return {
        home: {"lead": diff > 0, "trail": diff < 0, "first": first == home},
        away: {"lead": diff < 0, "trail": diff > 0, "first": first == away},
    }


def league_stats(
    tg: pd.DataFrame, records: list[dict], coefs: dict, season: str, asof: date
) -> dict[str, TeamStats]:
    if tg is None or len(tg) == 0:
        return {}
    cut = pd.Timestamp(asof)
    cur = tg[(tg["season"] == season) & (tg["game_type"] == 2) & (tg["date"] < cut)]
    if len(cur) == 0:
        return {}
    recs = [r for r in records if context._date_ok(r, season, asof)]
    won_by = {(int(g), t): bool(w) for g, t, w in zip(cur["game_id"], cur["team"], cur["won"],
                                                      strict=True)}
    pxg = _period_xg(recs, coefs)
    prev = context._prev_season(season)

    out: dict[str, TeamStats] = {}
    mine: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    total: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    pens: dict[str, int] = defaultdict(int)
    for rec in recs:
        h, a = rec["home"]["abbr"], rec["away"]["abbr"]
        gid = rec["id"]
        for team, opp in ((h, a), (a, h)):
            for i, p in enumerate(PERIODS):
                m, o = pxg.get((gid, team, p), 0.0), pxg.get((gid, opp, p), 0.0)
                mine[team][i] += m
                total[team][i] += m + o
        for pen in rec.get("penalties") or []:
            if pen.get("min") in context.PP_MINUTES:
                pens[pen["team"]] += 1
        for team, st in _score_states(rec).items():
            if (gid, team) not in won_by:
                continue
            s = out.setdefault(team, TeamStats())
            won = won_by[(gid, team)]
            s.led_after2 += st["lead"]
            s.led_after2_won += st["lead"] and won
            s.trailed_after2 += st["trail"]
            s.trailed_after2_won += st["trail"] and won
            s.scored_first += st["first"]
            s.scored_first_won += st["first"] and won

    for team in sorted(cur["team"].unique()):
        s = out.setdefault(team, TeamStats())
        rows = context._rows(cur, team, asof, season)
        s.gp = len(rows)
        s.period_xg_share = [
            round(mine[team][i] / total[team][i], 3) if total[team][i] > 0 else None
            for i in range(3)
        ]
        s.pp, s.pk = context.special_teams(recs, team, season, asof)
        home_rows = rows[rows["is_home"].astype(bool)]
        road_rows = rows[~rows["is_home"].astype(bool)]
        s.home_gp, s.road_gp = len(home_rows), len(road_rows)
        s.home_pts_pct = _pts_pct(home_rows)
        s.road_pts_pct = _pts_pct(road_rows)
        s.pts_pct = _pts_pct(rows)
        last = context.season_line(tg, records, team, prev, context.FAR_FUTURE)
        if last:
            s.pts_pct_last, s.gp_last = last["pts_pct"], last["gp"]
        line = context.season_line(tg, records, team, season, asof)
        s.xgf_pct_season = line["xgf_pct"] if line else None
        f = context.form(cur, team, asof)
        if f:
            s.xgf_pct_last10, s.streak = f["xgf_pct"], f["streak"]
        tail = rows.tail(config.FORM_GAMES)
        if len(tail):
            s.luck_last10 = round(float((tail["goals_reg"] - tail["xg_reg"]).sum()) / len(tail), 3)
        for pts in reversed(_points(rows).tolist()):
            if pts < 1:
                break
            s.point_streak += 1

    eligible = {t for t, s in out.items() if s.gp >= config.INSIGHT_MIN_GAMES}
    pp_rank = _rank({t: s.pp for t, s in out.items()}, eligible)
    pk_rank = _rank({t: s.pk for t, s in out.items()}, eligible)
    pim_rank = _rank({t: pens[t] / s.gp for t, s in out.items() if s.gp}, eligible)
    for t, s in out.items():
        s.pp_rank, s.pk_rank, s.pim_rank = pp_rank.get(t), pk_rank.get(t), pim_rank.get(t)
        s.n_ranked = len(eligible)
    return out


def schedule_load(
    tg: pd.DataFrame, team: str, day: date, is_home: bool, season: str
) -> tuple[bool, int, int]:
    """(b2b, games in the last 4 days incl. tonight, consecutive away games incl. tonight)."""
    if tg is None or len(tg) == 0:
        return False, 1, 0 if is_home else 1
    rows = tg[(tg["team"] == team) & (tg["date"] < pd.Timestamp(day))]
    rows = rows.sort_values(["date", "game_id"], kind="stable")
    yesterday = pd.Timestamp(day - timedelta(days=1))
    b2b = bool((rows["date"] == yesterday).any())
    last4 = int((rows["date"] >= pd.Timestamp(day - timedelta(days=3))).sum()) + 1
    run = 0
    if not is_home:
        run = 1
        rows = rows[rows["season"] == season]
        for home in reversed(rows["is_home"].astype(bool).tolist()):
            if home:
                break
            run += 1
    return b2b, last4, run
