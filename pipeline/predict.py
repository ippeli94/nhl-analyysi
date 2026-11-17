"""One scheduled game -> exported game object (spec 4.5, 6.3)."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline import config
from pipeline.adjust import GoalieRatings, goalie_block, goalie_mult
from pipeline.config import ModelParams
from pipeline.ratings import (
    PeriodShares,
    Ratings,
    base_lambda,
    home_mult,
    rest_mult,
    split_lambda,
)
from pipeline.simulate import simulate
from pipeline.sources.nhl import ScheduledGame
from pipeline.teams import display_name

NULL_KEYS = ("lineup_changes", "players", "form", "season_compare", "h2h", "insights", "odds")


def _clamp(x: float) -> float:
    lo, hi = config.FACTOR_PRODUCT_CLAMP
    return min(max(x, lo), hi)


def _team_block(t: dict) -> dict:
    return {
        "id": t["id"],
        "abbr": t["abbr"],
        "name": display_name(t["abbr"], t.get("name") or t["abbr"]),
        "logo": t.get("logo"),
        "logo_dark": t.get("logo_dark"),
    }


def _factor(key: str, label: str, home: float, away: float, unit: str) -> dict:
    return {"key": key, "label": label, "home": home, "away": away, "unit": unit}


def predict_game(
    game: ScheduledGame,
    r: Ratings,
    ps: PeriodShares,
    sp: dict,
    p: ModelParams,
    last_played: dict[str, date],
    gr: GoalieRatings,
    starters: dict[str, list[tuple[int, float]]],
) -> dict:
    home, away = game["home"]["abbr"], game["away"]["abbr"]
    yesterday = date.fromisoformat(game["nhl_date"]) - timedelta(days=1)
    home_b2b = last_played.get(home) == yesterday
    away_b2b = last_played.get(away) == yesterday
    st_home, st_away = starters.get(home, []), starters.get(away, [])

    base_h, base_a = base_lambda(r, home, away), base_lambda(r, away, home)
    ice_h = home_mult(r)
    rest_h, rest_a = rest_mult(r, home_b2b, away_b2b), rest_mult(r, away_b2b, home_b2b)
    gk_h = goalie_mult(st_away, away, gr, p)  # the away goalie's effect on the home lambda
    gk_a = goalie_mult(st_home, home, gr, p)

    lam_h = base_h * _clamp(ice_h * rest_h * gk_h)
    lam_a = base_a * _clamp(rest_a * gk_a)

    sim = simulate(
        split_lambda(lam_h, home, away, ps, p.use_periods),
        split_lambda(lam_a, away, home, ps, p.use_periods),
        sp,
        p,
        seed=game["id"],
    )
    low = min(r.n_games.get(home, 0), r.n_games.get(away, 0)) < config.LOW_CONFIDENCE_MIN_GAMES
    prob = dict(sim["prob"], confidence="low" if low else "normal")

    return {
        "id": game["id"],
        "start_utc": game["start_utc"],
        "nhl_date": game["nhl_date"],
        "state": "upcoming",
        "home": _team_block(game["home"]),
        "away": _team_block(game["away"]),
        "prob": prob,
        "xg": {"home": round(lam_h, 2), "away": round(lam_a, 2)},
        "score": sim["score"],
        "periods": sim["periods"],
        "factors": [
            _factor("base", "Joukkuevahvuus", base_h, base_a, "goals"),
            _factor("home_ice", "Kotietu", ice_h, 1.0, "mult"),
            _factor("rest", "Lepo / back-to-back", rest_h, rest_a, "mult"),
            _factor("goalie", "Maalivahti", gk_h, gk_a, "mult"),
        ],
        "goalies": {
            "home": goalie_block(st_home, home, gr, p),
            "away": goalie_block(st_away, away, gr, p),
        },
        **{k: None for k in NULL_KEYS},
    }
