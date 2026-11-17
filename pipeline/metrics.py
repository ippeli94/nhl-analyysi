"""Scoring metrics for win probabilities (spec chapter 8)."""

from __future__ import annotations

import numpy as np

BIN_WIDTH = 0.05
N_BINS = 20
CLIP = 1e-6


def _logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, CLIP, 1 - CLIP)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def score(p_home: np.ndarray, y_home: np.ndarray, base_rate: float) -> dict:
    p = np.asarray(p_home, dtype=float)
    y = np.asarray(y_home, dtype=float)
    n = len(p)
    if n == 0:
        return {"n": 0, "brier": None, "logloss": None, "accuracy": None,
                "brier_base": None, "logloss_base": None, "calibration": []}
    base = np.full(n, base_rate, dtype=float)
    idx = np.minimum((p / BIN_WIDTH).astype(int), N_BINS - 1)
    calibration = []
    for b in range(N_BINS):
        mask = idx == b
        if mask.any():
            calibration.append({
                "lo": round(b * BIN_WIDTH, 2), "hi": round((b + 1) * BIN_WIDTH, 2),
                "n": int(mask.sum()), "p_mean": float(p[mask].mean()),
                "y_mean": float(y[mask].mean()),
            })
    return {
        "n": n,
        "brier": float(np.mean((p - y) ** 2)),
        "logloss": _logloss(p, y),
        "accuracy": float(np.mean((p >= 0.5) == (y >= 0.5))),
        "brier_base": float(np.mean((base - y) ** 2)),
        "logloss_base": _logloss(base, y),
        "calibration": calibration,
    }
