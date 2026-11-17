"""Expected-goals model: logistic regression trained with scikit-learn, scored with numpy only.

Coefficients live in a JSON file (spec 6.1, 17.1); cache files never hold xG.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import cache, config

COEFS_VERSION = 1
SHOT_TYPES = ["snap", "slap", "backhand", "tip-in", "deflected", "wrap-around", "other"]
STRENGTHS = ["ev", "pp", "sh"]  # baseline is 5v5
FEATURES: list[str] = (
    ["dist", "angle", "dist_x_angle"]
    + [f"type_{t}" for t in SHOT_TYPES]
    + [f"str_{s}" for s in STRENGTHS]
    + ["rebound", "rush", "is_home"]
)
FRAME_COLUMNS = [
    "game_id", "season", "date", "team", "is_home", "p", "ev", "empty_net", "goalie",
    "shooter", "dist", "angle", "type", "strength", "rebound", "rush",
]


def shots_frame(records: list[dict]) -> pd.DataFrame:
    """One row per cached shot, with game context from the record."""
    rows = []
    for rec in records:
        for s in rec["shots"]:
            rows.append({
                "game_id": rec["id"], "season": rec["season"], "date": rec["date"],
                "team": s["team"], "is_home": s["team"] == rec["home"]["abbr"],
                "p": s["p"], "ev": s["ev"], "empty_net": s["empty_net"],
                "goalie": s["goalie"], "shooter": s["shooter"],
                "dist": s["dist"], "angle": s["angle"], "type": s["type"],
                "strength": s["strength"], "rebound": s["rebound"], "rush": s["rush"],
            })
    return pd.DataFrame(rows, columns=FRAME_COLUMNS)


def design(df: pd.DataFrame) -> np.ndarray:
    """Unstandardized n x 16 design matrix; baseline is a wrist shot at 5v5."""
    dist = df["dist"].to_numpy(dtype=float)
    angle = df["angle"].to_numpy(dtype=float)
    cols = [dist, angle, dist * angle]
    cols += [(df["type"] == t).to_numpy(dtype=float) for t in SHOT_TYPES]
    cols += [(df["strength"] == s).to_numpy(dtype=float) for s in STRENGTHS]
    cols += [df[c].to_numpy(dtype=float) for c in ("rebound", "rush", "is_home")]
    return np.column_stack(cols)


def _training_rows(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    rows = df[~df["empty_net"].astype(bool)]
    return design(rows), (rows["ev"] == "goal").to_numpy(dtype=int)


def train(train_df: pd.DataFrame, test_df: pd.DataFrame) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss, roc_auc_score
    from sklearn.preprocessing import StandardScaler

    x_tr, y_tr = _training_rows(train_df)
    x_te, y_te = _training_rows(test_df)
    scaler = StandardScaler().fit(x_tr)
    model = LogisticRegression(C=1.0, max_iter=1000).fit(scaler.transform(x_tr), y_tr)
    p_te = model.predict_proba(scaler.transform(x_te))[:, 1]
    base_rate = float(y_tr.mean())
    return {
        "version": COEFS_VERSION,
        "features": list(FEATURES),
        "mean": scaler.mean_.tolist(),
        "std": scaler.scale_.tolist(),
        "coef": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "trained_on": sorted(train_df["season"].unique().tolist()),
        "metrics": {
            "auc": float(roc_auc_score(y_te, p_te)),
            "logloss": float(log_loss(y_te, p_te)),
            "logloss_const": float(log_loss(y_te, np.full(len(y_te), base_rate))),
            "n_train": int(len(y_tr)),
            "n_test": int(len(y_te)),
        },
    }


def score(df: pd.DataFrame, coefs: dict) -> np.ndarray:
    """Goal probability per shot (numpy only). Empty-net shots get nan."""
    if list(coefs["features"]) != FEATURES:
        raise ValueError("xG coefficients do not match the current feature list")
    z = (design(df) - np.asarray(coefs["mean"])) / np.asarray(coefs["std"])
    logit = z @ np.asarray(coefs["coef"]) + coefs["intercept"]
    xg = 1.0 / (1.0 + np.exp(-logit))
    xg[df["empty_net"].to_numpy(dtype=bool)] = np.nan
    return xg


def load_coefs(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _check(coefs: dict) -> str | None:
    m = coefs["metrics"]
    if m["logloss"] >= m["logloss_const"]:
        return f"log loss {m['logloss']:.4f} is not better than constant {m['logloss_const']:.4f}"
    if m["auc"] < config.XG_AUC_MIN:
        return f"AUC {m['auc']:.3f} is below {config.XG_AUC_MIN}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.xg")
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--train", nargs="+", required=True)
    t.add_argument("--test", nargs="+", required=True)
    t.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    train_df = shots_frame(cache.load_seasons(args.train))
    test_df = shots_frame(cache.load_seasons(args.test))
    coefs = train(train_df, test_df)
    problem = _check(coefs)
    if problem:
        print(f"xG model rejected: {problem}", file=sys.stderr)
        raise SystemExit(1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(coefs, indent=2), encoding="utf-8")
    print(f"wrote {out}: {json.dumps(coefs['metrics'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
