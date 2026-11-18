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
EN_PROB_MIN_GAMES = 50

# Draw calibration (spec 15.3)
KAPPA_GRID = (0.80, 2.50, 0.005)
DRAW_GAP_MAX = 0.015
DRAW_GAP_CURRENT_MAX = 0.03

# Ratings (spec 6.2, 6.8)
LEAGUE_GOALS_FALLBACK = 3.0
MIN_FIT_ROWS = 20
LEAGUE_PERIOD_SHARE_FALLBACK = (1 / 3, 1 / 3, 1 / 3)

# Shot features (spec 5.1)
GOAL_X = 89.0
REBOUND_S = 3
RUSH_S = 4
RUSH_MAX_X = 25.0

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
MODEL_VERSION = "1.1.0"
SCHEMA_VERSION = 1

# Odds (The Odds API v4, phase 2)
ODDS_BASE = "https://api.the-odds-api.com/v4"
ODDS_SPORT = "icehockey_nhl"
ODDS_BOOKMAKERS = (
    "veikkaus_fi", "unibet_fi", "leovegas_fi", "pinnacle", "betsson",
    "coolbet", "nordicbet", "unibet_se", "betfair_ex_eu", "marathonbet",
)
ODDS_MARKETS = ("h2h", "totals", "spreads")
ODDS_FALLBACK_REGIONS = "eu,fi"
ODDS_ENV_KEY = "ODDS_API_KEY"
ODDS_LOOKAHEAD_H = 36
ODDS_NEXT_GAME_H = 24
ODDS_MIN_REMAINING = 30
ODDS_LOW_CREDITS = 60
ODDS_REUSE_HOURS = 36
ODDS_STALE_HOURS = 16
ODDS_MATCH_TOLERANCE_H = 6
ODDS_REF_BOOK = "pinnacle"
ODDS_REF_MIN_BOOKS = 2
ODDS_VIG_RANGE = (-0.01, 0.15)
ODDS_EV_MIN = 0.03
ODDS_EDGE_MIN = 0.02
ODDS_SUSPICIOUS_GAP = 0.10
ODDS_COMMISSION = {"betfair_ex_eu": 0.05}
VEIKKAUS_BOOK = "veikkaus_fi"
ODDS_FIXTURE = "odds__icehockey_nhl.json"
