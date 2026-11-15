import json

import pytest
import requests

from pipeline import config


def test_path_resolves_against_root(root):
    assert config.path("data", "state") == root / "data" / "state"


def test_load_params_defaults_without_file(root):
    p = config.load_params()
    assert (p.half_life_days, p.xg_weight, p.sigma_pace, p.draw_kappa) == (40.0, 0.6, 0.08, 1.0)


def test_load_params_overlays_file_and_ignores_gate_key(root):
    model = root / "model"
    model.mkdir()
    (model / "params.json").write_text(
        json.dumps({"alpha": 0.2, "gate": {"passed": True}}), encoding="utf-8"
    )
    assert config.load_params().alpha == 0.2
    assert config.load_gate() == {"passed": True}


def test_load_gate_is_none_without_file(root):
    assert config.load_gate() is None


def test_load_params_rejects_unknown_key(root):
    model = root / "model"
    model.mkdir()
    (model / "params.json").write_text(json.dumps({"alhpa": 0.2}), encoding="utf-8")
    with pytest.raises(ValueError):
        config.load_params()


def test_network_is_blocked():
    with pytest.raises(RuntimeError):
        requests.get("https://example.com")
