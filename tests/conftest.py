import pytest
import requests

from pipeline import config


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError("network access in tests")

    monkeypatch.setattr(requests.Session, "request", blocked)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    return tmp_path
