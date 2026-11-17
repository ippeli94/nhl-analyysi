"""Synthetic team_games tables for tests (shared test infrastructure)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

PERIOD_SPLIT = (0.30, 0.35, 0.35)
BASE_GOALS = 3.0


def make_team_games(
    seasons=("20242025", "20252026"),
    n_teams: int = 8,
    games_per_team: int = 40,
    strengths: dict[str, float] | None = None,
    home_adv: float = 0.05,
    seed: int = 1,
) -> pd.DataFrame:
    """Two rows per game; goals ~ Poisson(exp(log(3) + att - def + home)).

    `strengths` maps team -> s: attack = s, defence = s / 3 (a strong team also
    concedes a little less), which keeps every pairing's lambda within 1.5..5.
    """
    rng = np.random.default_rng(seed)
    teams = [chr(ord("A") + i) for i in range(n_teams)]
    strengths = strengths or {}
    att = {t: strengths.get(t, 0.0) for t in teams}
    dfn = {t: att[t] / 3 for t in teams}
    rows = []
    game_id = 2000000000
    for season in seasons:
        day = date(int(season[:4]), 10, 10)
        last_day = None
        for _ in range(games_per_team):
            order = [str(t) for t in rng.permutation(teams)]
            b2b = last_day is not None and (day - last_day).days == 1
            for i in range(0, n_teams - 1, 2):
                home, away = order[i], order[i + 1]
                lam_h = np.exp(np.log(BASE_GOALS) + att[home] - dfn[away] + home_adv)
                lam_a = np.exp(np.log(BASE_GOALS) + att[away] - dfn[home])
                gh, ga = int(rng.poisson(lam_h)), int(rng.poisson(lam_a))
                game_id += 1
                decided = "REG" if gh != ga else "OT"
                home_won = gh > ga if gh != ga else bool(rng.integers(2))
                for team, opp, is_home, g, og, lam, won in (
                    (home, away, True, gh, ga, lam_h, home_won),
                    (away, home, False, ga, gh, lam_a, not home_won),
                ):
                    rows.append(
                        {
                            "game_id": game_id,
                            "season": season,
                            "game_type": 2,
                            "date": pd.Timestamp(day),
                            "team": team,
                            "opp": opp,
                            "is_home": is_home,
                            "b2b": b2b,
                            "opp_b2b": b2b,
                            "goals_reg": g,
                            "xg_reg": float(lam),
                            "target": float(g),
                            "target_p1": g * PERIOD_SPLIT[0],
                            "target_p2": g * PERIOD_SPLIT[1],
                            "target_p3": g * PERIOD_SPLIT[2],
                            "en_goals": 0,
                            "score_reg": g,
                            "opp_score_reg": og,
                            "decided": decided,
                            "won": won,
                        }
                    )
            last_day = day
            day = day + timedelta(days=int(rng.integers(1, 3)))
    return pd.DataFrame(rows)


def make_goalie_games(
    tg: pd.DataFrame,
    seed: int = 1,
    deltas: dict[str, tuple[float, float]] | None = None,
) -> pd.DataFrame:
    """Two goalies per team (ids team_index*10 + 1 and + 2); the first starts 70 % of games.

    xg_faced = 28, ga ~ Poisson(28 * (1 - delta)), toi = 3600 (the starter plays the game).
    `deltas` maps team -> (delta of goalie 1, delta of goalie 2).
    """
    rng = np.random.default_rng(seed)
    deltas = deltas or {}
    teams = sorted(tg["team"].unique())
    rows = []
    for r in tg.itertuples():
        idx = teams.index(r.team)
        slot = 0 if rng.random() < 0.7 else 1
        delta = deltas.get(r.team, (0.0, 0.0))[slot]
        rows.append({
            "game_id": r.game_id, "season": r.season, "game_type": r.game_type,
            "date": r.date, "team": r.team, "goalie": idx * 10 + 1 + slot,
            "name": f"G{idx * 10 + 1 + slot}", "started": True, "toi": 3600,
            "xg_faced": 28.0, "ga": int(rng.poisson(28 * (1 - delta))),
        })
    return pd.DataFrame(rows)
