"""Period-level Monte Carlo simulation of one match (spec 6.9)."""

from __future__ import annotations

import numpy as np

from pipeline.config import N_SIMS, TOTAL_LINES, ModelParams

MAX_SCORE = 7  # final scores are clipped to 0..7 in the matrix
GOAL_DIST_MAX = 10  # regulation goal distribution: 0..9 and 10+
TOP_N = 5


def _lognormal_mean_one(rng: np.random.Generator, sigma: float, n: int) -> np.ndarray:
    return rng.lognormal(0.0, sigma, n) / np.exp(sigma**2 / 2)


def _wmean(mask: np.ndarray, w: np.ndarray) -> float:
    return float(np.sum(w * mask) / np.sum(w))


def _goal_dist(goals: np.ndarray, w: np.ndarray) -> list[float]:
    g = np.minimum(goals, GOAL_DIST_MAX)
    dist = np.bincount(g, weights=w, minlength=GOAL_DIST_MAX + 1) / w.sum()
    return [round(float(x), 4) for x in dist]


def simulate(
    lam_home_p: np.ndarray,
    lam_away_p: np.ndarray,
    sp: dict,
    p: ModelParams,
    seed: int,
    n: int = N_SIMS,
) -> dict:
    rng = np.random.default_rng(seed)
    lam_h = np.asarray(lam_home_p, dtype=float)
    lam_a = np.asarray(lam_away_p, dtype=float)

    # 1. uncertainty: shared pace and per-team multipliers, each with mean one
    pace = _lognormal_mean_one(rng, p.sigma_pace, n)
    team_h = _lognormal_mean_one(rng, p.sigma_team, n)
    team_a = _lognormal_mean_one(rng, p.sigma_team, n)

    # 2. period goals
    gh_p = rng.poisson(lam_h[None, :] * (pace * team_h)[:, None])
    ga_p = rng.poisson(lam_a[None, :] * (pace * team_a)[:, None])
    gh = gh_p.sum(axis=1)
    ga = ga_p.sum(axis=1)

    # 3. empty-net goal for the leader when the margin is 1 or 2
    margin = gh - ga
    u = rng.random(n)
    for m in (1, 2):
        prob = sp["en_prob"][m]
        gh = gh + ((margin == m) & (u < prob))
        ga = ga + ((margin == -m) & (u < prob))
    reg_h, reg_a = gh, ga

    # 4. weights: tied regulation results get draw_kappa
    tied = reg_h == reg_a
    w = np.where(tied, p.draw_kappa, 1.0)

    # 5. overtime / shootout winner scores one goal
    lh, la = float(lam_h.sum()), float(lam_a.sum())
    p_home_ot = 0.5 + p.ot_slope * (lh / (lh + la) - 0.5)
    home_ot = rng.random(n) < p_home_ot
    fin_h = reg_h + (tied & home_ot)
    fin_a = reg_a + (tied & ~home_ot)

    # 6. results
    prob = {
        "home_win": round(_wmean(fin_h > fin_a, w), 4),
        "away_win": round(_wmean(fin_a > fin_h, w), 4),
        "reg_home": round(_wmean(reg_h > reg_a, w), 4),
        "reg_draw": round(_wmean(tied, w), 4),
        "reg_away": round(_wmean(reg_a > reg_h, w), 4),
    }

    ch = np.minimum(fin_h, MAX_SCORE)
    ca = np.minimum(fin_a, MAX_SCORE)
    size = MAX_SCORE + 1
    cells = np.bincount(ch * size + ca, weights=w, minlength=size * size)
    mat = cells.reshape(size, size) / w.sum()
    order = np.argsort(-mat, axis=None, kind="stable")[:TOP_N]
    top = [
        {"home": int(i // size), "away": int(i % size), "p": round(float(mat.flat[i]), 4)}
        for i in order
    ]

    total = fin_h + fin_a
    final_margin = fin_h - fin_a
    home_m15 = _wmean(final_margin >= 2, w)
    away_m15 = _wmean(final_margin <= -2, w)
    score = {
        "most_likely": dict(top[0]),
        "top": top,
        "matrix": [[round(float(x), 4) for x in row] for row in mat],
        "total": {
            "mean": round(float(np.sum(w * total) / w.sum()), 2),
            "over": {str(line): round(_wmean(total > line, w), 4) for line in TOTAL_LINES},
        },
        "spread": {
            "home_-1.5": round(home_m15, 4),
            "away_+1.5": round(1 - home_m15, 4),
            "away_-1.5": round(away_m15, 4),
            "home_+1.5": round(1 - away_m15, 4),
        },
    }

    periods = []
    for i in range(3):
        periods.append(
            {
                "n": i + 1,
                "xg_home": round(float(lam_h[i]), 2),
                "xg_away": round(float(lam_a[i]), 2),
                "p_home_wins_period": round(_wmean(gh_p[:, i] > ga_p[:, i], w), 4),
                "p_away_wins_period": round(_wmean(ga_p[:, i] > gh_p[:, i], w), 4),
                "p_draw": round(_wmean(gh_p[:, i] == ga_p[:, i], w), 4),
            }
        )

    return {
        "prob": prob,
        "score": score,
        "periods": periods,
        "goal_dist": {"home": _goal_dist(reg_h, w), "away": _goal_dist(reg_a, w)},
    }
