"""Central configuration: paths, model parameters and constants (spec chapter 6)."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent


def path(*parts: str) -> Path:
    """Resolve a path relative to the project root. All file access goes through here."""
    return ROOT.joinpath(*parts)


@dataclass
class ModelParams:
    half_life_days: float = 40.0
    offseason_gap_days: int = 60
    prev_season_weight: float = 0.7
    xg_weight: float = 0.6
    alpha: float = 0.05
    sigma_pace: float = 0.08
    sigma_team: float = 0.08
    draw_kappa: float = 1.0
    ot_slope: float = 0.3
    use_periods: bool = True
    period_prior_games: int = 20
    goalie_k: float = 60.0
    goalie_weight: float = 0.6
    use_goalie: bool = True


def _read_params_file() -> dict:
    params_file = path("model", "params.json")
    if not params_file.exists():
        return {}
    return json.loads(params_file.read_text(encoding="utf-8"))


def load_params() -> ModelParams:
    """Defaults overlaid with model/params.json. The `gate` key belongs to load_gate()."""
    overrides = {k: v for k, v in _read_params_file().items() if k != "gate"}
    known = {f.name for f in fields(ModelParams)}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f"unknown keys in model/params.json: {sorted(unknown)}")
    return replace(ModelParams(), **overrides)


def load_gate() -> dict | None:
    """The `gate` object of model/params.json, or None if there is none."""
    return _read_params_file().get("gate")


# NHL API (spec 5.1)
NHL_BASE = "https://api-web.nhle.com/v1"
REQUESTS_PER_S = 3
RETRY_BACKOFF_S = (1, 3, 9)
TIMEOUT_S = 20
OFFLINE_DIR: Path | None = None

# Seasons and game types
BACKFILL_SEASONS = ("20232024", "20242025", "20252026", "20262027")
CACHED_GAME_TYPES = (2, 3)
TRAIN_GAME_TYPES = (2,)

# Simulation and markets
N_SIMS = 10000
TOTAL_LINES = (5.5, 6.5)
EN_PROB_FALLBACK = {1: 0.30, 2: 0.15}

# Shot features (spec 5.1)
GOAL_X = 89.0
REBOUND_S = 3
RUSH_S = 4

# xG model and confidence
XG_AUC_MIN = 0.70
LOW_CONFIDENCE_MIN_GAMES = 10

# Goalie adjustment
GOALIE_MULT_CLAMP = (0.90, 1.10)
FACTOR_PRODUCT_CLAMP = (0.80, 1.25)
STARTER_PROB = 0.7
STARTER_LOOKBACK = 10

# Freshness and versioning
STALE_HOURS = 14
MODEL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
