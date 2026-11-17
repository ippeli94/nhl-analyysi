"""Play-by-play -> normalized shot rows with features (spec 6.1).

`situationCode` digit order (verified against FIELDS.md): away goalie, away skaters,
home skaters, home goalie.
"""

from __future__ import annotations

import math
import statistics

from pipeline import config

SHOT_EVENTS = ("goal", "shot-on-goal", "missed-shot")
EV_NAME = {"goal": "goal", "shot-on-goal": "sog", "missed-shot": "miss"}
SHOT_TYPES = {
    "wrist": "wrist", "snap": "snap", "slap": "slap", "backhand": "backhand",
    "tip-in": "tip-in", "deflected": "deflected", "wrap-around": "wrap-around",
}
MIN_SKATERS = 3
FULL_STRENGTH = 5


def parse_situation(code: str, shooter_is_home: bool) -> tuple[str, bool, bool]:
    """Return (strength, empty_net, excluded) from the shooter's side."""
    away_g, away_s, home_s, home_g = (int(c) for c in code)
    own, opp = (home_s, away_s) if shooter_is_home else (away_s, home_s)
    defending_goalie = away_g if shooter_is_home else home_g
    excluded = own < MIN_SKATERS or opp < MIN_SKATERS
    if own > opp:
        strength = "pp"
    elif own < opp:
        strength = "sh"
    else:
        strength = "5v5" if own == FULL_STRENGTH else "ev"
    return strength, defending_goalie == 0, excluded


def normalize_xy(
    x: float, y: float, shooter_is_home: bool, home_defending_side: str | None,
    median_x: float | None,
) -> tuple[float, float]:
    """Flip so the attacked net is at +x. Side missing: infer from the median raw x."""
    if home_defending_side in ("left", "right"):
        attacks_positive = (home_defending_side == "left") == shooter_is_home
    else:
        attacks_positive = median_x is None or median_x >= 0
    return (x, y) if attacks_positive else (-x, -y)


def _seconds(clock: str) -> int:
    minutes, seconds = clock.split(":")
    return int(minutes) * 60 + int(seconds)


def _coords(play: dict) -> tuple[float, float] | None:
    details = play.get("details") or {}
    x, y = details.get("xCoord"), details.get("yCoord")
    if x is None or y is None:
        return None
    return float(x), float(y)


def _period(play: dict) -> tuple[int, str]:
    desc = play.get("periodDescriptor") or {}
    return int(desc.get("number", 0)), desc.get("periodType", "REG")


def _team_maps(pbp: dict) -> tuple[int, dict[int, str]]:
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    return home["id"], {home["id"]: home["abbrev"], away["id"]: away["abbrev"]}


def extract_shots(pbp: dict) -> list[dict]:
    home_id, abbr = _team_maps(pbp)
    plays = pbp["plays"]

    # Median raw x per (period, team), used only when homeTeamDefendingSide is missing.
    xs: dict[tuple[int, int], list[float]] = {}
    for play in plays:
        if play.get("typeDescKey") in SHOT_EVENTS:
            xy = _coords(play)
            owner = (play.get("details") or {}).get("eventOwnerTeamId")
            if xy and owner is not None:
                xs.setdefault((_period(play)[0], owner), []).append(xy[0])
    medians = {key: statistics.median(vals) for key, vals in xs.items()}

    rows: list[dict] = []
    last_shot_t: dict[tuple[int, int], int] = {}  # (period, team id) -> previous shot time
    prev: dict | None = None
    for play in plays:
        period, period_type = _period(play)
        prev_play, prev = prev, play
        if play.get("typeDescKey") not in SHOT_EVENTS:
            continue
        details = play.get("details") or {}
        owner = details.get("eventOwnerTeamId")
        if owner not in abbr or period_type == "SO" or "timeInPeriod" not in play:
            continue
        t = _seconds(play["timeInPeriod"])
        previous_shot_t = last_shot_t.get((period, owner))
        last_shot_t[(period, owner)] = t  # every unblocked attempt counts, even if dropped

        xy = _coords(play)
        code = play.get("situationCode")
        if xy is None or not code:
            continue
        is_home = owner == home_id
        strength, empty_net, excluded = parse_situation(code, is_home)
        if excluded:
            continue
        side = play.get("homeTeamDefendingSide")
        median = medians.get((period, owner))
        x, y = normalize_xy(xy[0], xy[1], is_home, side, median)

        rebound = previous_shot_t is not None and t - previous_shot_t < config.REBOUND_S
        rush = False
        if (prev_play is not None and _period(prev_play)[0] == period
                and "timeInPeriod" in prev_play):
            prev_xy = _coords(prev_play)
            if prev_xy and t - _seconds(prev_play["timeInPeriod"]) < config.RUSH_S:
                px, _ = normalize_xy(prev_xy[0], prev_xy[1], is_home, side, median)
                rush = px < config.RUSH_MAX_X

        row = {
            "p": period, "t": t, "team": abbr[owner],
            "shooter": details.get("shootingPlayerId", details.get("scoringPlayerId")),
            "goalie": details.get("goalieInNetId"),
            "x": x, "y": y,
            "dist": round(math.hypot(config.GOAL_X - x, y), 2),
            "angle": round(math.degrees(math.atan2(abs(y), config.GOAL_X - x)), 2),
            "type": SHOT_TYPES.get(details.get("shotType"), "other"),
            "sit": code, "strength": strength,
            "ev": EV_NAME[play["typeDescKey"]],
            "empty_net": empty_net, "rebound": rebound, "rush": rush,
        }
        if row["ev"] == "goal":
            row["a1"] = details.get("assist1PlayerId")
            row["a2"] = details.get("assist2PlayerId")
        rows.append(row)
    return rows


def extract_penalties(pbp: dict) -> list[dict]:
    _, abbr = _team_maps(pbp)
    rows = []
    for play in pbp["plays"]:
        if play.get("typeDescKey") != "penalty":
            continue
        details = play.get("details") or {}
        minutes = details.get("duration")
        owner = details.get("eventOwnerTeamId")
        period, period_type = _period(play)
        if not minutes or owner not in abbr or period_type == "SO" or "timeInPeriod" not in play:
            continue
        rows.append({"p": period, "t": _seconds(play["timeInPeriod"]),
                     "team": abbr[owner], "min": int(minutes)})
    return rows
