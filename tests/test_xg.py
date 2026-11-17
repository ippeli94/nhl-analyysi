import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from pipeline import cache, xg

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TYPES = ["wrist", "snap", "slap", "backhand", "tip-in", "deflected", "wrap-around", "other"]
STRENGTHS = ["5v5", "ev", "pp", "sh"]


def load(gid: int, kind: str) -> dict:
    path = FIXTURES / f"gamecenter__{gid}__{kind}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def synthetic(n: int = 20000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dist = rng.uniform(3, 60, n)
    p = 1 / (1 + np.exp(-(-1.2 - 0.045 * dist)))
    return pd.DataFrame({
        "game_id": rng.integers(1, 500, n), "season": "20242025", "date": "2024-11-01",
        "team": "AAA", "is_home": rng.random(n) < 0.5, "p": 1,
        "ev": np.where(rng.random(n) < p, "goal", "sog"),
        "empty_net": rng.random(n) < 0.02, "goalie": 1, "shooter": 2,
        "dist": dist, "angle": rng.uniform(0, 80, n),
        "type": rng.choice(TYPES, n), "strength": rng.choice(STRENGTHS, n),
        "rebound": rng.random(n) < 0.1, "rush": rng.random(n) < 0.1,
    })


@pytest.fixture(scope="module")
def frames():
    df = synthetic()
    return df.iloc[:15000].copy(), df.iloc[15000:].copy()


def test_design_shape_and_baseline():
    df = synthetic(5)
    base = {"type": "wrist", "strength": "5v5", "rebound": False, "rush": False, "is_home": False}
    for col, value in base.items():
        df.loc[0, col] = value
    x = xg.design(df)
    assert x.shape == (5, 16) == (5, len(xg.FEATURES))
    onehot = [i for i, f in enumerate(xg.FEATURES) if f.startswith(("type_", "str_"))]
    assert not x[0, onehot].any()
    assert x[0, xg.FEATURES.index("dist_x_angle")] == df.loc[0, "dist"] * df.loc[0, "angle"]


def test_train_recovers_distance_effect(frames):
    c = xg.train(*frames)
    m = c["metrics"]
    assert m["logloss"] < m["logloss_const"] and m["auc"] > 0.6
    assert c["coef"][xg.FEATURES.index("dist")] < 0
    assert c["version"] == 1 and c["trained_on"] == ["20242025"]


def test_train_excludes_empty_net(frames):
    train_df, test_df = frames
    c = xg.train(train_df, test_df)
    assert c["metrics"]["n_train"] == int((~train_df["empty_net"]).sum())
    assert c["metrics"]["n_test"] == int((~test_df["empty_net"]).sum())


def test_score_matches_sklearn_predict_proba(frames):
    train_df, test_df = frames
    c = xg.train(train_df, test_df)
    tr = train_df[~train_df["empty_net"]]
    x = xg.design(tr)
    scaler = StandardScaler().fit(x)
    model = LogisticRegression(C=1.0, max_iter=1000).fit(scaler.transform(x), tr["ev"] == "goal")
    te = test_df[~test_df["empty_net"]]
    expected = model.predict_proba(scaler.transform(xg.design(te)))[:, 1]
    assert np.allclose(xg.score(te, c), expected, atol=1e-9)


def test_score_is_nan_for_empty_net(frames):
    train_df, test_df = frames
    c = xg.train(train_df, test_df)
    s = xg.score(test_df, c)
    assert np.isnan(s[test_df["empty_net"].to_numpy()]).all()
    assert not np.isnan(s[~test_df["empty_net"].to_numpy()]).any()


def test_score_real_fixture_in_plausible_range(frames, root):
    c = xg.train(*frames)
    for gid in (2026020286, 2026020298):
        pbp = load(gid, "play-by-play")
        box = load(gid, "boxscore")
        df = xg.shots_frame([cache.build_record(pbp, box)])
        assert len(df) > 0 and set(df["is_home"]) == {True, False}
        s = xg.score(df, c)
        ok = ~df["empty_net"].to_numpy()
        assert ((s[ok] > 0) & (s[ok] < 1)).all()


def test_shots_frame_empty():
    df = xg.shots_frame([])
    assert df.empty and "is_home" in df.columns


def test_load_coefs_roundtrip(frames, tmp_path):
    c = xg.train(*frames)
    f = tmp_path / "c.json"
    f.write_text(json.dumps(c), encoding="utf-8")
    assert xg.load_coefs(f)["features"] == xg.FEATURES


def test_cli_refuses_bad_model(root, monkeypatch):
    bad = {"metrics": {"auc": 0.6, "logloss": 0.2, "logloss_const": 0.3}}
    monkeypatch.setattr(xg, "train", lambda a, b: bad)
    monkeypatch.setattr(cache, "load_seasons", lambda s: [])
    out = root / "model" / "x.json"
    with pytest.raises(SystemExit) as e:
        xg.main(["train", "--train", "20232024", "--test", "20252026", "--out", str(out)])
    assert e.value.code == 1 and not out.exists()


def test_cli_refuses_worse_than_constant(root, monkeypatch):
    bad = {"metrics": {"auc": 0.8, "logloss": 0.4, "logloss_const": 0.3}}
    monkeypatch.setattr(xg, "train", lambda a, b: bad)
    monkeypatch.setattr(cache, "load_seasons", lambda s: [])
    out = root / "x.json"
    with pytest.raises(SystemExit):
        xg.main(["train", "--train", "1", "--test", "2", "--out", str(out)])
    assert not out.exists()
