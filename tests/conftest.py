import os

# Full pytest crashed intermittently on Windows with a native access violation in pandas/numpy
# (about 3/7 runs); single-threaded BLAS/OpenMP ran 0/8.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import pytest  # noqa: E402
import requests  # noqa: E402

from pipeline import config  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError("network access in tests")

    monkeypatch.setattr(requests.Session, "request", blocked)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    return tmp_path
