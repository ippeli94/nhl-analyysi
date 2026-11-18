import numpy as np
import pytest

from pipeline import metrics


def test_binary_vs_base_known_numbers():
    out = metrics.binary_vs_base(np.array([0.6, 0.2]), np.array([1, 0]), np.array([0.5, 0.5]))
    assert out["n"] == 2
    assert out["brier"] == pytest.approx(0.1)
    assert out["brier_base"] == pytest.approx(0.25)
    assert out["logloss_base"] == pytest.approx(np.log(2))
    assert out["logloss"] < out["logloss_base"]


def test_binary_vs_base_without_base():
    out = metrics.binary_vs_base(np.array([0.6, 0.2]), np.array([1, 0]), None)
    assert out["brier_base"] is None and out["logloss_base"] is None


def test_score_calibration_unchanged():
    p = np.array([0.1, 0.12, 0.55, 0.9])
    y = np.array([0, 1, 1, 1])
    assert metrics.score(p, y, 0.5)["calibration"] == metrics.calibration_bins(p, y)
    assert metrics.binary_vs_base(p, y, None)["calibration"] == metrics.calibration_bins(p, y)
