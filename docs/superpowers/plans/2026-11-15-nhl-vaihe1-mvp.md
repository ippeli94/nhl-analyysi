# NHL-analyysi, Phase 1 (MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fully automatic pipeline that fetches NHL data into a per-game cache, fits an xG model and a team-strength model, simulates upcoming games at period level, logs every prediction, exports `latest.json`, and publishes a Finnish mobile site (game list + game detail) on GitHub Pages, gated by a backtest.

**Architecture:** A Python package `pipeline/` run by GitHub Actions cron. Finished games are stored once as immutable gzip JSON files committed to the repo; everything else (xG, team tables, ratings, simulation) is recomputed from that cache on every run. The run writes `site/data/latest.json` (deployed as a Pages artifact, never committed), appends to JSONL history logs, and `tools/review.py` writes a problems-only report that can block the deploy.

**Tech Stack:** Python 3.12, requests, numpy, pandas, scikit-learn, jsonschema, pytest, ruff. Frontend: plain HTML/CSS/ES modules, no build. CI: GitHub Actions on `ubuntu-latest`, GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-11-15-nhl-analyysi-design.md` (sections 15 and 16 override earlier sections where they differ).

## Scope

In: spec 15.5 step 1 — data fetch, cache, backfill (2023-24, 2024-25, 2025-26, current), xG model, team model, goalie adjustment (spec 6.5, added to the MVP by the user), period-level simulation, prediction log, `latest.json`, site (list + detail + status), `tools/review.py`, backtest with go-live gate, workflows.

Out (later phases; do not build): odds/value (`value.py`, `sources/odds.py`), player probabilities (`players.py`), insights/H2H (`insights.py`), performance page and `performance.json`, lineup/injury and H2H multipliers (spec 6.6, 6.7), a confirmed-starter source (`sources/goalies.py`), and the lineup part of backtest variant (b) of spec 15.2.

Plan decisions that deviate from or complete the spec (spec 15.4 lets the builder decide; record these in the spec when executing Task 1):

1. **xG is not stored in cache files.** Cache files hold raw shot features only and are never rewritten; xG is scored at load time from `model/xg_coefs.json`. (Spec 5.1 stores xG; that conflicts with immutable files and with retraining.)

2. **`official` is derived, not stored:** the official prediction of a game is the last log row with `ts < start_utc`.

3. **Started games** are shown frozen from `data/state/last_games.json` (the previous run's exported game objects), never recomputed.

4. **Source status enum** is `ok | stale | failed | unavailable`. `unavailable` = optional source that does not feed the model in this phase and could not be fetched (ESPN injuries). It never turns the banner red.

5. **Tuned parameters** live in `model/params.json` (committed), overriding `config.py` defaults. It also holds the gate result; `pipeline.run` refuses to run online unless `gate.passed` is true.

6. **Tuning season is 2024-25, gate season is 2025-26** with parameters frozen, so the gate is out-of-sample.

7. `train_xg.yml` is manual only (training seasons do not change mid-season).

8. Goal rows in the cache carry assist ids (`a1`, `a2`) so later phases can derive power-play points without a new backfill.

9. **The goalie adjustment is in the MVP; the starter is inferred.** Without a confirmed-starter source every goalie status is `projected` ("Arvio") and the multiplier is the expectation over the two likely starters. `confirmed` and `likely` stay in the contract for a later source. The backtest runs three goalie modes — none (15.2 a), actual boxscore starter (15.2 b, used to tune the goalie constant) and projected (what production does) — and the gate is judged on the projected mode.

## Global Constraints
- Python `>=3.12`. Pinned deps (requirements.txt): `requests==2.32.3`, `numpy==2.1.3`, `pandas==2.2.3`, `scikit-learn==1.5.2`, `jsonschema==4.23.0`; dev: `pytest==8.3.3`, `ruff==0.7.4`. No other dependencies. No database, no frontend framework, no build step.
- Tests never touch the network. `tests/conftest.py` has an autouse fixture that makes `requests.Session.request` raise. Fixtures live in `tests/fixtures/`.
- Every path goes through `config.path(*parts)`, which resolves against `config.ROOT`; tests monkeypatch `config.ROOT` to `tmp_path`.
- Every number from spec chapter 6 lives in `pipeline/config.py` (`ModelParams` defaults or module constants). No magic numbers in other modules.
- NHL API: at most 3 requests/s, 3 retries with backoff 1 s / 3 s / 9 s, timeout 20 s, every response validated for required fields; a validation failure is a source failure.
- Core source (NHL) failure aborts the run with a non-zero exit and publishes nothing. Optional source failure never aborts.
- JSON contract: probabilities are decimals 0–1; missing information is `null`, never an invented value; field names English, user-visible text Finnish; `schema_version` is `1`.
- UI language Finnish; times in `Europe/Helsinki`; decimal comma; times as `02.00`, dates as `su 15.11.`.
- Simulation is deterministic: same inputs and same game id give identical output.
- No API key or key-shaped string in the repo, logs or generated data.
- Commits: one per task step group as written; messages in English, conventional prefix (`feat:`, `test:`, `chore:`).
- A modelling ambiguity not settled here is returned as a question, not invented (spec 12.4).
- Run commands as `python -m pytest ...` (Windows and Linux).

## Review Focus

1. A team with no games before `asof` (season opener, new franchise): prediction uses league-average strength, `confidence` is `"low"`, no exception. Test in Task 7 and Task 10.

2. No games scheduled (summer, All-Star break): run exits 0, `games` is `[]`, `next_game_date` is set or `null`, site shows the next game date. Tests in Task 12 and Task 15.

3. A finished game whose play-by-play is missing, empty or 404 (postponed or cancelled game): backfill and daily caching skip it with a logged warning and continue. Test in Task 4.

4. A shot without coordinates, or a period without `homeTeamDefendingSide`: the shot is dropped, or direction is inferred from the median x. Tests in Task 3.

5. A cron run that starts late, after puck drop: the started game keeps its earlier exported object with `state: "started"`, no new log row is written for it, and the official prediction stays the last pre-start row. Tests in Task 10 and Task 12.

## Shared Contracts

Give this section, Global Constraints, the task itself and the spec sections the task cites to every implementer.

**`CachedGame`** (one gzip JSON file, `data/cache/games/{season}/{id}.json.gz`):

```jsonc
{"v": 1, "id": 2025020321, "season": "20252026", "game_type": 2,
 "date": "2025-11-15", "start_utc": "2025-11-16T00:00:00Z",
 "home": {"id": 12, "abbr": "CAR"}, "away": {"id": 55, "abbr": "SEA"},
 "score": {"home": 4, "away": 2}, "score_reg": {"home": 4, "away": 2}, "decided": "REG",   // REG | OT | SO
 "shots": [{"p": 1, "t": 312, "team": "CAR", "shooter": 8478427, "goalie": 8475831,   // goalie null = empty net
            "x": 71.0, "y": -12.0, "dist": 21.6, "angle": 33.7, "type": "wrist",
            "sit": "1551", "strength": "5v5", "ev": "goal", "empty_net": false,
            "rebound": false, "rush": false, "a1": 8480039, "a2": null}],            // a1/a2 only on goals
 "skaters": [{"id": 0, "name": "", "team": "CAR", "pos": "C", "toi": 1164, "g": 1, "a": 0, "sog": 3}],
 "goalies": [{"id": 0, "name": "", "team": "CAR", "started": true, "toi": 3600, "sa": 27, "saves": 25, "ga": 2}],
 "penalties": [{"p": 2, "t": 95, "team": "SEA", "min": 2}]}
```

`p` is the period number (4 = overtime; shootout excluded). `t` is seconds elapsed in the period. `x`,`y` are normalized so the attacked net is at (89, 0). `strength` ∈ `5v5 | ev | pp | sh` from the shooter's side. `ev` ∈ `goal | sog | miss`. `type` ∈ `wrist | snap | slap | backhand | tip-in | deflected | wrap-around | other`. `score_reg` for an OT/SO game is the tie at the loser's score.

**`ScheduledGame`** (from `sources/nhl.py`):

`{"id": int, "season": str, "game_type": int, "nhl_date": "YYYY-MM-DD", "start_utc": iso, "state": str, "home": {"id","abbr","name","logo","logo_dark"}, "away": {...}}`. `state` is the raw NHL `gameState`. Finished = `OFF` or `FINAL`; started = `LIVE` or `CRIT`, or `start_utc <= now` and not finished.

**`team_games`** DataFrame (two rows per cached game, one per team as attacker):

`game_id, season, game_type, date (datetime64), team, opp, is_home, b2b, opp_b2b, goals_reg, xg_reg, target, target_p1, target_p2, target_p3, en_goals, score_reg, opp_score_reg, decided, won`.

`goals_reg` and `xg_reg` cover periods 1–3 without empty-net shots. `target = xg_weight*xg_reg + (1-xg_weight)*goals_reg`; `target_pN` is the same per period. `en_goals` = regulation empty-net goals scored. `b2b` = team played on `date - 1 day`. `won` includes OT/SO.

**`ModelParams`** (`pipeline/config.py`, dataclass with these defaults; `load_params()` overlays `model/params.json`):

`half_life_days=40.0, offseason_gap_days=60, prev_season_weight=0.7, xg_weight=0.6, alpha=0.05, sigma_pace=0.08, sigma_team=0.08, draw_kappa=1.0, ot_slope=0.3, use_periods=True, period_prior_games=20, goalie_k=60.0, goalie_weight=0.6, use_goalie=True`.

**`SimParams`**: `{"league_period_share": [f, f, f], "en_prob": {1: f, 2: f}}`.

**Game object in `latest.json`** — spec 4.5, with these phase-1 rules: `prob`, `xg`, `score`, `periods`, `factors`, `goalies` are filled; `lineup_changes, players, form, season_compare, h2h, insights, odds` are present and `null`. Additions to the spec draft: top-level `next_game_date` (string or null); `home.logo_dark`; `periods[].p_away_wins_period`; `score.spread` has four keys `home_-1.5, away_+1.5, away_-1.5, home_+1.5`; each factor has `"unit": "goals" | "mult"`. Phase-1 factors, in order: `base` (unit `goals`: λ from intercept + attack + defense only), `home_ice` (`mult`), `rest` (`mult`), `goalie` (`mult`; the `home` value is the effect of the away goalie on the home team's λ). `goalies` is `{"home": G, "away": G}` where `G` is `null` (no goalie data for the team) or `{"id", "name", "status": "confirmed" | "likely" | "projected", "gsax_per60", "p_start", "mult"}`: the most probable starter, his start probability, and the multiplier his team's goaltending puts on the opponent's λ.

**History rows:**
- `data/history/predictions/{season}.jsonl`: `{"ts", "model_version", "game_id", "nhl_date", "start_utc", "home", "away", "p_home_win", "p_reg": [home, draw, away], "lam_home", "lam_away", "total_mean", "most_likely": [h, a], "confidence", "goalie_home", "goalie_away", "market": null, "picks": null}` (goalie ids or null)
- `data/history/results/{season}.jsonl`: `{"game_id", "date", "home", "away", "home_goals", "away_goals", "reg_home_goals", "reg_away_goals", "decided", "home_won"}`
- `data/state/sources.json`: `{"<id>": {"status", "fetched_at", "last_ok_at", "note"}}`

## File Structure

```
pyproject.toml, requirements.txt, requirements-dev.txt, .gitignore, .gitattributes, README.md
pipeline/
  __init__.py
  config.py            constants, ModelParams, load_params(), path()
  sources/__init__.py
  sources/nhl.py       HTTP + validation + offline mode (core source)
  sources/injuries.py  optional ESPN probe (status only in phase 1)
  shots.py             play-by-play -> normalized shot rows with features
  cache.py             CachedGame build/read/write, caching of finished games
  backfill.py          resumable history download (CLI)
  xg.py                design matrix, training (CLI), numpy scoring
  features.py          cache -> team_games table, SimParams
  ratings.py           weighted Poisson regression, base lambda, period split
  adjust.py            goalie ratings, starter projection, goalie multiplier (lineup and H2H added later)
  simulate.py          Monte Carlo
  predict.py           one scheduled game -> exported game object
  metrics.py           Brier, log loss, accuracy, calibration bins
  tracking.py          prediction and result logs, official prediction
  export.py            latest.json build + schema validation
  schema/latest.schema.json
  run.py               orchestration (CLI)
  backtest.py          day-by-day backtest, tuning, gate (CLI)
  teams.py             abbreviation -> Finnish display name map (extended in phase 2 for Odds API)
model/xg_coefs.json, model/xg_coefs_bt.json, model/params.json   (produced in Task 14)
tools/record_fixtures.py, tools/review.py
site/index.html, app.js, format.js, styles.css, manifest.webmanifest, icon.svg, tests/format.test.js
tests/conftest.py, tests/synth.py, tests/fixtures/*, tests/test_*.py
.github/workflows/ci.yml, pipeline.yml, backfill.yml, train_xg.yml
```

Reserved for later phases, not created now: `players.py`, `value.py`, `insights.py`, `sources/odds.py`, `sources/goalies.py`, `site/data/performance.json`.

---

### Task 1: Project scaffold and config  — model: Haiku

**Files:**
- Create: `.gitignore`, `.gitattributes`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `pipeline/__init__.py`, `pipeline/sources/__init__.py`, `pipeline/config.py`, `tests/conftest.py`, `tests/test_config.py`
- Modify: the spec file — append section "17. Toteutussuunnitelman päätökset" listing the nine plan decisions above, in Finnish.

**Interfaces:**
- Produces: `config.ROOT: Path`; `config.path(*parts: str) -> Path`; `config.ModelParams` (dataclass, defaults in Shared Contracts); `config.load_params() -> ModelParams`; `config.load_gate() -> dict | None` (the `gate` object of `model/params.json`); constants `NHL_BASE="https://api-web.nhle.com/v1"`, `REQUESTS_PER_S=3`, `RETRY_BACKOFF_S=(1, 3, 9)`, `TIMEOUT_S=20`, `BACKFILL_SEASONS=("20232024","20242025","20252026","20262027")`, `CACHED_GAME_TYPES=(2, 3)`, `TRAIN_GAME_TYPES=(2,)`, `N_SIMS=10000`, `TOTAL_LINES=(5.5, 6.5)`, `GOAL_X=89.0`, `REBOUND_S=3`, `RUSH_S=4`, `XG_AUC_MIN=0.70`, `LOW_CONFIDENCE_MIN_GAMES=10`, `GOALIE_MULT_CLAMP=(0.90, 1.10)`, `FACTOR_PRODUCT_CLAMP=(0.80, 1.25)`, `STARTER_PROB=0.7`, `STARTER_LOOKBACK=10`, `STALE_HOURS=14`, `MODEL_VERSION="1.0.0"`, `SCHEMA_VERSION=1`, `EN_PROB_FALLBACK={1: 0.30, 2: 0.15}` (start values, used below 50 qualifying games), `OFFLINE_DIR: Path | None = None`.

- [ ] **Step 1: Initialise the repo.** `git init -b main`. `.gitignore`: `__pycache__/`, `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `site/data/`, `*.egg-info/`. `.gitattributes`: `* text=auto eol=lf` and `*.gz binary`.

- [ ] **Step 2: Write `pyproject.toml`** (project `nhl-analyysi`, `requires-python = ">=3.12"`; ruff `line-length = 100`, `select = ["E","F","I","B","UP"]`; pytest `testpaths = ["tests"]`, `addopts = "-q"`) and the two requirements files with the pinned versions from Global Constraints (`requirements-dev.txt` starts with `-r requirements.txt`).

- [ ] **Step 3: Create a venv and install.** `python -m venv .venv`, then `.venv\Scripts\python -m pip install -r requirements-dev.txt`. Expected: exit 0.

- [ ] **Step 4: Write `tests/conftest.py`** with the autouse fixture `no_network` (monkeypatches `requests.Session.request` to raise `RuntimeError("network access in tests")`) and fixture `root(tmp_path, monkeypatch)` that sets `config.ROOT` to `tmp_path` and returns it.

- [ ] **Step 5: Write failing tests** in `tests/test_config.py`:

```python
def test_path_resolves_against_root(root):
    assert config.path("data", "state") == root / "data" / "state"
def test_load_params_defaults_without_file(root):
    p = config.load_params()
    assert (p.half_life_days, p.xg_weight, p.sigma_pace, p.draw_kappa) == (40.0, 0.6, 0.08, 1.0)
def test_load_params_overlays_file_and_ignores_gate_key(root):
    # model/params.json = {"alpha": 0.2, "gate": {"passed": true}}
    assert config.load_params().alpha == 0.2 and config.load_gate() == {"passed": True}
def test_network_is_blocked():
    with pytest.raises(RuntimeError): requests.get("https://example.com")
```

- [ ] **Step 6: Run** `python -m pytest tests/test_config.py` — expected FAIL (no `pipeline.config`).

- [ ] **Step 7: Implement `pipeline/config.py`** per the Interfaces block.

- [ ] **Step 8: Run** `python -m pytest` and `python -m ruff check .` — expected: 4 passed, no findings.

- [ ] **Step 9: Append spec section 17 and commit.** `git add -A && git commit -m "chore: scaffold project, config and test harness"`

---

### Task 2: NHL source and recorded fixtures  — model: Haiku

**Files:**
- Create: `pipeline/sources/nhl.py`, `tools/record_fixtures.py`, `tests/fixtures/*.json`, `tests/fixtures/FIELDS.md`, `tests/test_nhl_source.py`

**Interfaces:**
- Consumes: `config` constants.
- Produces:
  - `class SourceError(Exception)`
  - `get_json(path: str) -> dict` — `path` like `/schedule/2026-11-10`. Online: rate-limited GET with retries; raises `SourceError` on final failure or non-JSON. Offline (`config.OFFLINE_DIR` set): reads `OFFLINE_DIR / fixture_name(path)`; missing file raises `SourceError`.
  - `fixture_name(path: str) -> str` — strip the leading `/`, replace `/` with `__`, append `.json` (`/gamecenter/2025020321/play-by-play` → `gamecenter__2025020321__play-by-play.json`).
  - `require(payload: dict, dotted_paths: list[str]) -> None` — raises `SourceError` naming the first missing path.
  - `fetch_schedule_week(date: str) -> tuple[list[ScheduledGame], str | None]` — second element is `nextStartDate`.
  - `fetch_pbp(game_id: int) -> dict`, `fetch_boxscore(game_id: int) -> dict` — raw, validated payloads.

- [ ] **Step 1: Write `tools/record_fixtures.py`** (the only code allowed to hit the network; uses `requests` directly, 0.4 s sleep between calls). `python tools/record_fixtures.py --date 2026-11-10` saves, compact JSON, named with `fixture_name`: the schedule week of `--date`; the schedule week 7 days later (upcoming games); play-by-play and boxscore of the first finished game in the week whose `gameOutcome.lastPeriodType` is `REG` and of the first with `SO` (fall back to `OT`; step back a week until both are found). It also saves `/gamecenter/{id}/landing` of one upcoming game. It prints the two game ids.

- [ ] **Step 2: Run it once** and commit the fixtures. Expected: 7 files, each play-by-play under 600 KB.

- [ ] **Step 3: Write `tests/fixtures/FIELDS.md`** from the recorded files: the actual key paths for schedule team name/logo fields, play-by-play (`plays[].typeDescKey`, `details.xCoord`, `yCoord`, `shotType`, `shootingPlayerId`, `scoringPlayerId`, `assist1PlayerId`, `assist2PlayerId`, `goalieInNetId`, `eventOwnerTeamId`, `situationCode`, `homeTeamDefendingSide`, `periodDescriptor.number/periodType`, `timeInPeriod`), boxscore skater and goalie rows (TOI, goals, assists, shots, starter flag, saves/shots against, goals against), the two game ids with their final scores, and one hand-read example of `situationCode` during a power play. Also note whether the upcoming game's `landing` payload names starting goalies (if it does, report it to the controller as a possible `confirmed` source; this plan does not use it). If any path differs from this plan, FIELDS.md wins; note the difference there (spec 13.1, 13.5).

- [ ] **Step 4: Write failing tests** (`OFFLINE_DIR` = fixtures dir via monkeypatch):

```python
def test_fixture_name(): assert nhl.fixture_name("/gamecenter/1/play-by-play") == "gamecenter__1__play-by-play.json"
def test_schedule_week_parses_games():
    games, nxt = nhl.fetch_schedule_week(FIXTURE_DATE)
    g = games[0]
    assert set(g) == {"id","season","game_type","nhl_date","start_utc","state","home","away"}
    assert g["start_utc"].endswith("Z") and len(g["home"]["abbr"]) == 3 and g["home"]["logo"].startswith("https://")
    assert isinstance(g["season"], str) and len(g["season"]) == 8 and nxt
def test_require_names_missing_path():
    with pytest.raises(nhl.SourceError, match="a.b"): nhl.require({"a": {}}, ["a.b"])
def test_pbp_validated(): assert nhl.fetch_pbp(REG_GAME_ID)["plays"]
def test_missing_offline_file_is_source_error():
    with pytest.raises(nhl.SourceError): nhl.fetch_pbp(1)
def test_online_retries_then_raises(monkeypatch):
    # OFFLINE_DIR None; Session.request stub returns HTTP 500; time.sleep stubbed
    with pytest.raises(nhl.SourceError): nhl.get_json("/schedule/now")
    assert calls == 4          # 1 try + 3 retries
```

- [ ] **Step 5: Run** `python -m pytest tests/test_nhl_source.py` — expected FAIL.

- [ ] **Step 6: Implement `pipeline/sources/nhl.py`.** One module-level `requests.Session` with a generic `User-Agent`; a monotonic-clock limiter enforcing `1/REQUESTS_PER_S` between calls; HTTP 404 raises `SourceError` immediately without retry.

- [ ] **Step 7: Run** `python -m pytest` — expected all pass. **Commit:** `feat: NHL source with validation, offline mode and recorded fixtures`

---

### Task 3: Shot extraction and features  — model: Sonnet

Spec: 6.1 (observations, coordinate normalization, features, `situationCode`).

**Files:**
- Create: `pipeline/shots.py`, `tests/test_shots.py`

**Interfaces:**
- Consumes: raw play-by-play dict (Task 2), `tests/fixtures/FIELDS.md`.
- Produces:
  - `parse_situation(code: str, shooter_is_home: bool) -> tuple[str, bool, bool]` → `(strength, empty_net, excluded)`. Digits are away goalie, away skaters, home skaters, home goalie. `empty_net` = defending goalie digit is 0. `excluded` = either skater count below 3 (penalty shot / shootout).
  - `normalize_xy(x: float, y: float, shooter_is_home: bool, home_defending_side: str | None, median_x: float | None) -> tuple[float, float]`
  - `extract_shots(pbp: dict) -> list[dict]` — the `shots` rows of `CachedGame`.
  - `extract_penalties(pbp: dict) -> list[dict]` — the `penalties` rows.

Rules the tests pin: home attacks +x when `homeTeamDefendingSide == "left"`; flip `(x, y) → (-x, -y)` otherwise; with the side missing, flip when the team's median raw x in that period is negative. `dist = hypot(89 - x, y)`; `angle = degrees(atan2(|y|, 89 - x))`. `rebound` = previous shot by the same team in the same period less than 3 s earlier. `rush` = previous play of any type in the same period less than 4 s earlier with coordinates whose x, normalized to the shooter's direction, is below 25. Dropped: blocked shots, period type `SO`, `excluded` situations, shots with a missing coordinate. Empty-net shots are kept with `empty_net: true`.

- [ ] **Step 1: Write failing tests:**

```python
@pytest.mark.parametrize("code,home,exp", [
    ("1551", True,  ("5v5", False, False)), ("1441", False, ("ev", False, False)),
    ("1451", True,  ("pp", False, False)),  ("1451", False, ("sh", False, False)),
    ("0651", True,  ("sh", True, False)),   ("0651", False, ("pp", False, False)),  # away goalie pulled
    ("1560", False, ("sh", True, False)),                                           # home goalie pulled
])
def test_parse_situation(code, home, exp): assert shots.parse_situation(code, home) == exp
def test_penalty_shot_situation_is_excluded(): assert shots.parse_situation("0101", True)[2] is True
def test_normalize_home_defending_left_keeps_sign():  assert shots.normalize_xy(70, 10, True, "left", None) == (70, 10)
def test_normalize_home_defending_right_flips():      assert shots.normalize_xy(-70, 10, True, "right", None) == (70, -10)
def test_normalize_away_is_opposite_of_home():        assert shots.normalize_xy(-70, 10, False, "left", None) == (70, -10)
def test_normalize_infers_from_median_when_side_missing(): assert shots.normalize_xy(-60, 5, True, None, -55.0) == (60, -5)
def test_real_game_shots_point_at_the_net():
    rows = shots.extract_shots(load("pbp", REG_GAME_ID))
    assert np.mean([r["x"] > 0 for r in rows]) > 0.85
    assert np.median([r["dist"] for r in rows if r["ev"] == "goal"]) < 40
def test_goal_count_matches_final_score():      # non-shootout goals per team == score (REG fixture), incl. empty net
def test_shootout_attempts_are_excluded():      # SO fixture: no row with p == 5, goals == score_reg sum
def test_shot_without_coordinates_is_dropped(): # hand-built play lacking xCoord -> []
def test_rebound_and_rush_flags():              # hand-built 3-play sequence: second shot 2 s later -> rebound True;
                                                # shot 3 s after a neutral-zone faceoff -> rush True
def test_goal_rows_carry_assists():             assert "a1" in goal_row and "a1" not in sog_row
def test_extract_penalties_minutes():           assert all(p["min"] in (2, 4, 5, 10) for p in pens) and pens
```

Verify every row of the table against the hand-read power-play example in FIELDS.md before implementing; if the digit order differs, stop and report.

- [ ] **Step 2: Run** `python -m pytest tests/test_shots.py` — expected FAIL.

- [ ] **Step 3: Implement `pipeline/shots.py`.** Map raw `shotType` strings to the eight `type` values; unknown or missing → `other`.

- [ ] **Step 4: Run** `python -m pytest` — expected pass. **Commit:** `feat: shot extraction with normalized coordinates and features`

---

### Task 4: Game cache and backfill  — model: Haiku

Spec: 5.1.

**Files:**
- Create: `pipeline/cache.py`, `pipeline/backfill.py`, `tests/test_cache.py`

**Interfaces:**
- Consumes: `nhl.fetch_schedule_week`, `nhl.fetch_pbp`, `nhl.fetch_boxscore`, `nhl.SourceError`, `shots.extract_shots`, `shots.extract_penalties`.
- Produces:
  - `build_record(pbp: dict, box: dict) -> dict` (`CachedGame`; raises `ValueError` when `plays` is empty)
  - `game_path(season: str, game_id: int) -> Path`; `write(record: dict) -> Path` (refuses to overwrite: returns the existing path untouched); `read(path: Path) -> dict`
  - `cached_ids(season: str) -> set[int]`; `load_seasons(seasons: list[str]) -> list[dict]` (sorted by `date`, `id`)
  - `cache_finished(games: list[ScheduledGame]) -> tuple[list[int], list[int]]` → `(written_ids, skipped_ids)`; only finished games of `CACHED_GAME_TYPES` not yet cached; a `SourceError` or `ValueError` for one game is logged and the game is skipped.
  - `backfill.season_dates(season: str) -> tuple[str, str]` → `("{start_year}-09-25", "{end_year}-06-30")`
  - `backfill.run(seasons: list[str], limit: int | None = None) -> dict` → `{"written": n, "skipped": n, "already": n}`; walks schedule weeks from the start date following `nextStartDate` until past the end date or `None`. CLI: `python -m pipeline.backfill --seasons 20232024 20242025 [--limit N]`.

- [ ] **Step 1: Write failing tests** (offline fixtures, `root` fixture):

```python
def test_build_record_shape():
    r = cache.build_record(pbp, box)
    assert r["v"] == 1 and r["decided"] == "REG" and r["score"] == r["score_reg"] == KNOWN_SCORE
    assert len(r["skaters"]) >= 36 and sum(g["started"] for g in r["goalies"]) == 2
    assert all(isinstance(s["toi"], int) for s in r["skaters"])
def test_shootout_game_reg_score_is_tie():
    r = cache.build_record(so_pbp, so_box)
    assert r["decided"] == "SO" and r["score_reg"]["home"] == r["score_reg"]["away"] == min(r["score"].values())
def test_write_read_roundtrip_under_20kb():
    p = cache.write(r); assert cache.read(p) == r and p.stat().st_size < 20_000
def test_write_never_overwrites():           # write twice with a changed record -> file content is the first
def test_cache_finished_skips_cached_and_unfinished():
def test_cache_finished_skips_game_with_missing_pbp():      # Review Focus 3: unknown id in schedule -> in skipped, no raise
def test_build_record_rejects_empty_plays(): 
    with pytest.raises(ValueError): cache.build_record({**pbp, "plays": []}, box)
def test_backfill_is_resumable(monkeypatch):  # fetch_schedule_week stubbed to one week; second run -> written == 0, already == n
def test_season_dates(): assert backfill.season_dates("20252026") == ("2025-09-25", "2026-06-30")
```

- [ ] **Step 2: Run** `python -m pytest tests/test_cache.py` — expected FAIL.

- [ ] **Step 3: Implement.** Gzip with `mtime=0` and `json.dumps(..., separators=(",", ":"))` so identical input gives identical bytes. `decided` from `gameOutcome.lastPeriodType`. Boxscore paths per FIELDS.md.

- [ ] **Step 4: Run** `python -m pytest` — expected pass. **Commit:** `feat: immutable per-game cache and resumable backfill`

---

### Task 5: xG model  — model: Sonnet

Spec: 6.1, 15.1, 15.3 (AUC ≥ 0.70 is sufficient).

**Files:**
- Create: `pipeline/xg.py`, `tests/test_xg.py`

**Interfaces:**
- Consumes: `CachedGame.shots` rows; `cache.load_seasons`.
- Produces:
  - `FEATURES: list[str]` = `dist, angle, dist_x_angle, type_snap, type_slap, type_backhand, type_tip-in, type_deflected, type_wrap-around, type_other, str_ev, str_pp, str_sh, rebound, rush, is_home` (baseline: wrist, 5v5)
  - `shots_frame(records: list[dict]) -> pd.DataFrame` — one row per shot with `game_id, season, date, team, is_home, p, ev, empty_net, goalie, shooter` plus raw feature columns.
  - `design(df: pd.DataFrame) -> np.ndarray` (n × 16, unstandardized)
  - `train(train_df, test_df) -> dict` — coefs dict `{"version": 1, "features", "mean", "std", "coef", "intercept", "trained_on": [seasons], "metrics": {"auc", "logloss", "logloss_const", "n_train", "n_test"}}`. Training rows: `empty_net == False`. `LogisticRegression(C=1.0, max_iter=1000)` on standardized features.
  - `score(df: pd.DataFrame, coefs: dict) -> np.ndarray` — numpy only; empty-net rows get `nan`.
  - `load_coefs(path: Path) -> dict`
  - CLI `python -m pipeline.xg train --train 20232024 20242025 --test 20252026 --out model/xg_coefs_bt.json`; exits 1 and writes nothing when `logloss >= logloss_const` or `auc < XG_AUC_MIN`.

- [ ] **Step 1: Write failing tests** (synthetic shots: 20 000 rows, goal probability `sigmoid(-1.2 - 0.045*dist)`, seeded):

```python
def test_design_shape_and_baseline():   # wrist 5v5 row -> all one-hot columns 0; shape (n, 16)
def test_train_recovers_distance_effect():
    c = xg.train(train_df, test_df)
    assert c["metrics"]["logloss"] < c["metrics"]["logloss_const"] and c["metrics"]["auc"] > 0.6
    assert c["coef"][xg.FEATURES.index("dist")] < 0
def test_score_matches_sklearn_predict_proba():   # np.allclose(score(df, c), model.predict_proba(...)[:, 1], atol=1e-9)
def test_score_is_nan_for_empty_net():
def test_score_real_fixture_in_plausible_range():
    # coefs trained on synthetic data are not meaningful; assert only 0 < xg < 1 and no nan on non-empty-net rows
def test_cli_refuses_bad_model(root, monkeypatch):  # train() stubbed to return auc 0.6 -> SystemExit(1), no file
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Commit:** `feat: xG logistic model with numpy scoring`

---

### Task 6: Team tables and simulation parameters  — model: Sonnet

Spec: 6.2 (target), 6.8, 6.9 item 3.

**Files:**
- Create: `pipeline/features.py`, `tests/test_features.py`

**Interfaces:**
- Consumes: `xg.shots_frame`, `xg.score`, `CachedGame`.
- Produces:
  - `team_games(records: list[dict], coefs: dict, xg_weight: float) -> pd.DataFrame` (columns in Shared Contracts)
  - `sim_params(tg: pd.DataFrame) -> dict` (`SimParams`): `league_period_share[k]` = share of `target_p{k}` in the league sum; `en_prob[m]` = among games whose regulation margin without empty-net goals was `m`, the share where the leader scored at least one empty-net goal; below 50 such games use `EN_PROB_FALLBACK[m]`.
  - `last_played(tg: pd.DataFrame) -> dict[str, datetime.date]`

- [ ] **Step 1: Write failing tests** (hand-built `CachedGame` records, coefs stub giving every shot xG 0.1):

```python
def test_two_rows_per_game_and_columns():
def test_target_blends_xg_and_goals():       # 20 shots, 2 goals, xg_weight 0.6 -> target == 0.6*2.0 + 0.4*2
def test_target_excludes_empty_net_and_overtime():
def test_period_targets_sum_to_target():
def test_b2b_flags():                        # team plays on consecutive dates -> b2b 1 on the second, opp_b2b mirrors
def test_ot_game_reg_scores_are_equal_and_won_is_set():
def test_sim_params_shares_sum_to_one():     assert abs(sum(sp["league_period_share"]) - 1) < 1e-9
def test_en_prob_fallback_below_50_games():  assert sp["en_prob"] == {1: 0.30, 2: 0.15}
def test_en_prob_estimated():                # 100 one-goal games, 30 with a leader EN goal -> en_prob[1] == 0.30 exactly
def test_real_fixtures_build():              # two fixture games -> 4 rows, no nan in target
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Commit:** `feat: team-game table and simulation parameters`

---

### Task 7: Ratings and period split  — model: Sonnet

Spec: 6.2, 6.4, 6.8.

**Files:**
- Create: `pipeline/ratings.py`, `tests/synth.py`, `tests/test_ratings.py`

**Interfaces:**
- Consumes: `team_games` DataFrame, `ModelParams`.
- Produces:
  - `tests/synth.py`: `make_team_games(seasons=("20242025","20252026"), n_teams=8, games_per_team=40, strengths: dict[str, float] | None = None, home_adv=0.05, seed=1) -> pd.DataFrame` — Poisson goals from `exp(log(3.0) + att - def + home)`, `target = goals`, period targets split 0.30/0.35/0.35, all `team_games` columns filled.
  - `weights(tg: pd.DataFrame, asof: date, p: ModelParams) -> np.ndarray` — `asof_season` = the latest season with a row before `asof`. Same season: `age = (asof - date).days`. Previous season: `age = (asof - current_start).days + p.offseason_gap_days + (prev_end - date).days`, and the weight is multiplied by `p.prev_season_weight`; `current_start` = first date of `asof_season` (or `asof` if none), `prev_end` = last date of the previous season. Older seasons get weight 0. `w = 0.5 ** (age / p.half_life_days)`.
  - `@dataclass Ratings`: `intercept: float, attack: dict[str, float], defense: dict[str, float], home: float, b2b_att: float, b2b_def: float, n_games: dict[str, int]` (`n_games` = current-season games before `asof`)
  - `fit(tg: pd.DataFrame, asof: date, p: ModelParams) -> Ratings` — rows with `date < asof`, `game_type` in `TRAIN_GAME_TYPES`, weight > 0. `PoissonRegressor(alpha=p.alpha, max_iter=300)` with `sample_weight`; columns: attack one-hots, defense one-hots, `is_home`, `b2b`, `opp_b2b`. Fewer than 20 rows: return intercept `log(weighted mean target)` (or `log(3.0)` with no rows) and empty dicts.
  - `base_lambda(r: Ratings, team: str, opp: str) -> float` = `exp(intercept + attack.get(team, 0) + defense.get(opp, 0))`
  - `home_mult(r) -> float` = `exp(r.home)`; `rest_mult(r, b2b: bool, opp_b2b: bool) -> float` = `exp(r.b2b_att*b2b + r.b2b_def*opp_b2b)`
  - `@dataclass PeriodShares`: `league: list[float], scored: dict[str, list[float]], allowed: dict[str, list[float]]`
  - `period_shares(tg, asof, p) -> PeriodShares` — team shares shrunk to league: `(sum_target_pk + p.period_prior_games * league_mean_target * league_k) / (sum_target + p.period_prior_games * league_mean_target)`, current season rows before `asof`.
  - `split_lambda(lam: float, team: str, opp: str, ps: PeriodShares, use_periods: bool) -> np.ndarray` — raw `scored[team][k] * allowed[opp][k] / league[k]`, normalized to sum to `lam`; unknown team or `use_periods=False` → `lam * league`.

- [ ] **Step 1: Write `tests/synth.py`, then failing tests:**

```python
def test_weight_halves_every_40_days():      # same season, ages 0 and 40 -> ratio 0.5
def test_prev_season_weight_uses_fixed_gap_and_07():
    # asof 10 days into the season, game 5 days before prev season end -> 0.7 * 0.5 ** ((10 + 60 + 5) / 40)
def test_two_seasons_back_gets_zero_weight():
def test_fit_ignores_rows_on_or_after_asof():   # poison rows dated >= asof with target 50 -> ratings unchanged
def test_fit_recovers_strength_order():         # strengths A +0.3 attack, H -0.3 -> attack["A"] > attack["H"]; 0 < home < 0.15
def test_base_lambda_plausible():               # every pairing in 1.5 .. 5.0
def test_unknown_team_gets_league_average():    # Review Focus 1: base_lambda(r, "NEW", "A") == exp(intercept + defense["A"])
def test_fit_with_under_20_rows_returns_intercept_only():
def test_split_lambda_sums_to_lambda():         assert abs(split.sum() - 3.1) < 1e-9
def test_split_lambda_without_periods_is_league_share():
def test_period_shares_shrink_to_league_with_few_games():   # team with 2 games: each share within 0.02 of league
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Commit:** `feat: time-weighted Poisson ratings and period split`

---

### Task 8: Goalie ratings, starters and multiplier  — model: Sonnet

Spec: 6.5, 6.3 (clamps).

**Files:**
- Create: `pipeline/adjust.py`, `tests/test_adjust.py`
- Modify: `tests/synth.py` (add `make_goalie_games`)

**Interfaces:**
- Consumes: `CachedGame` (`shots[].goalie`, `goalies[]`), `xg.shots_frame`, `xg.score`, `ratings.weights`, `ModelParams`, `config.GOALIE_MULT_CLAMP`, `STARTER_PROB`, `STARTER_LOOKBACK`.
- Produces:
  - `goalie_games(records: list[dict], coefs: dict) -> pd.DataFrame` — one row per goalie per game: `game_id, season, game_type, date, team, goalie, name, started, toi, xg_faced, ga`. `xg_faced` and `ga` come from the shot rows whose `goalie` is that id (empty-net shots have no goalie and drop out).
  - `@dataclass GoalieRatings`: `delta: dict[int, float], team_delta: dict[str, float], gsax_per60: dict[int, float], names: dict[int, str]`
  - `goalie_ratings(gg: pd.DataFrame, asof: date, p: ModelParams) -> GoalieRatings` — rows with `date < asof`, weights `w` from `ratings.weights`. `delta[g] = (Σ w·xg_faced − Σ w·ga) / (Σ w·xg_faced + p.goalie_k)`. `team_delta[t]` = mean of `delta[g]` over the team's goalies, weighted by each goalie's `Σ w·xg_faced` for that team. `gsax_per60[g] = Σ w·(xg_faced − ga) / Σ w·toi × 3600`, rounded to 2.
  - `project_starters(gg: pd.DataFrame, team: str, nhl_date: date) -> list[tuple[int, float]]` — from the team's last `STARTER_LOOKBACK` games of the current season before `nhl_date`: primary = most starts (tie: most recent start), backup = second most. Result `[(primary, 0.7), (backup, 0.3)]`; `[(backup, 0.7), (primary, 0.3)]` when the team played on `nhl_date − 1 day` and the primary started that game; `[(primary, 1.0)]` with one goalie; `[]` with no games. Highest probability first.
  - `goalie_mult(starters: list[tuple[int, float]], team: str, gr: GoalieRatings, p: ModelParams) -> float` — `Σ prob × clamp(1 − p.goalie_weight × (delta.get(g, 0.0) − team_delta.get(team, 0.0)), 0.90, 1.10)`; `1.0` for an empty list or when `p.use_goalie` is false.
  - `goalie_block(starters, team: str, gr: GoalieRatings, p: ModelParams, status: str = "projected") -> dict | None` — the `G` object of Shared Contracts for `starters[0]`; `None` for an empty list.
  - `tests/synth.make_goalie_games(tg: pd.DataFrame, seed: int = 1, deltas: dict[str, tuple[float, float]] | None = None) -> pd.DataFrame` — two goalies per team (ids `team_index*10 + 1` and `+ 2`), the first starts 70 % of games, `xg_faced = 28`, `ga ~ Poisson(28 × (1 − delta))`, `toi = 3600`.

- [ ] **Step 1: Write failing tests:**

```python
def test_goalie_games_from_real_fixture():      # 2 starters per game, sum(ga) == non-empty-net goals, xg_faced > 0
def test_delta_formula_and_shrinkage():
    # one goalie, weights 1: xg 100, ga 90 -> delta == 10 / 160; xg 10, ga 9 -> 1 / 70
def test_delta_ignores_rows_on_or_after_asof():
def test_team_delta_is_xg_weighted_mean():      # deltas .05 (75 % of xG) and -.03 (25 %) -> 0.03
def test_mult_formula_and_direction():
    # delta .06, team_delta .02, weight .6 -> 1 - .6 * .04 == 0.976; a better goalie gives a smaller multiplier
def test_mult_is_clamped():                     # delta - team_delta = +0.5 -> 0.90; -0.5 -> 1.10
def test_mult_is_expectation_over_starters():   # [(a, .7), (b, .3)] -> .7 * m_a + .3 * m_b
def test_mult_is_one_without_data_or_when_disabled():
def test_unknown_goalie_counts_as_league_average():   # id not in delta -> delta 0
def test_projected_primary_is_most_starts_in_last_10():
def test_back_to_back_swaps_to_backup():        # primary started yesterday -> [(backup, .7), (primary, .3)]
def test_no_swap_when_backup_started_yesterday():
def test_single_goalie_gets_probability_one():
def test_goalie_block_fields():
    assert set(b) == {"id", "name", "status", "gsax_per60", "p_start", "mult"} and b["status"] == "projected"
def test_goalie_block_none_for_new_team():      # Review Focus 1
```

- [ ] **Step 2: Run** `python -m pytest tests/test_adjust.py` — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Commit:** `feat: goalie ratings, starter projection and goalie multiplier`

---

### Task 9: Monte Carlo simulation  — model: Sonnet

Spec: 6.9.

**Files:**
- Create: `pipeline/simulate.py`, `tests/test_simulate.py`

**Interfaces:**
- Consumes: `ModelParams`, `SimParams`, `config.N_SIMS`, `config.TOTAL_LINES`.
- Produces: `simulate(lam_home_p: np.ndarray, lam_away_p: np.ndarray, sp: dict, p: ModelParams, seed: int, n: int = N_SIMS) -> dict` with keys `prob`, `score`, `periods` exactly as in the `latest.json` game object (without `confidence`), plus `goal_dist: {"home": [P(0..10+)], "away": [...]}` (regulation goals incl. empty-net, for phase 3; not exported).

Algorithm, in this order (all vectorized, `np.random.default_rng(seed)`):

1. Per simulation, `pace ~ lognormal(0, p.sigma_pace)`, `team_h, team_a ~ lognormal(0, p.sigma_team)`, each divided by its theoretical mean `exp(σ²/2)`; period lambdas are multiplied by `pace * team`.

2. Period goals ~ Poisson. Period result probabilities come from these.

3. Regulation margin 1 or 2 → the leader gets one more goal with probability `sp["en_prob"][margin]`.

4. Weight = `p.draw_kappa` for simulations tied after step 3, else 1. Every reported number is a weighted mean.

5. Tied simulations: home wins with `0.5 + p.ot_slope * (λh / (λh + λa) - 0.5)` (λ = sum of the input period lambdas); the winner gets one goal in the final score.

6. `prob.reg_*` from regulation scores; `prob.home_win/away_win` from final scores. `score.matrix` is 8×8 over final scores `[home][away]` with goals clipped to 7; `top` = 5 largest cells; `most_likely` = `top[0]`; `total.mean` and `total.over[str(line)]` from final totals; `spread["home_-1.5"]` = P(final margin ≥ 2), `"away_+1.5"` = its complement, and symmetrically for the away side. Round probabilities to 4 decimals, `xg_*` and `mean` to 2.

- [ ] **Step 1: Write failing tests** (`sp = {"league_period_share": [.3,.35,.35], "en_prob": {1: .3, 2: .15}}`):

```python
def test_same_seed_same_result():        assert simulate(a, b, sp, P, 7) == simulate(a, b, sp, P, 7)
def test_different_seed_differs():
def test_probabilities_sum():
    r = simulate(...); pr = r["prob"]
    assert abs(pr["home_win"] + pr["away_win"] - 1) < 1e-3
    assert abs(pr["reg_home"] + pr["reg_draw"] + pr["reg_away"] - 1) < 1e-3
    assert abs(np.sum(r["score"]["matrix"]) - 1) < 1e-3 and np.shape(r["score"]["matrix"]) == (8, 8)
def test_equal_teams_are_symmetric():    # lam 3.0 vs 3.0 -> |home_win - 0.5| < 0.02; 0.18 < reg_draw < 0.28
def test_stronger_team_wins_more_but_not_absurdly():   # 3.6 vs 2.6 -> 0.58 < home_win < 0.72
def test_no_final_score_is_a_tie():      assert all(r["score"]["matrix"][i][i] == 0 for i in range(7))   # cell [7][7] is the clipped 7+ bucket
def test_kappa_raises_draw_share():      # draw_kappa 1.3 -> reg_draw higher than with 1.0, sums still 1
def test_uncertainty_pulls_towards_half():   # sigma 0.25 -> home_win closer to 0.5 than with sigma 0
def test_periods_block():                # 3 entries, n == 1,2,3, xg_home == input lambdas, three probs sum to 1
def test_spread_complements():           assert abs(s["home_-1.5"] + s["away_+1.5"] - 1) < 1e-3
def test_top_scores_sorted_and_most_likely_first():
def test_zero_en_prob_matches_plain_total():   # en_prob {1: 0, 2: 0}, sigma 0, kappa 1 -> |total.mean - (λh + λa + reg_draw)| < 0.05
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass; the whole file under 5 s.

- [ ] **Step 5: Commit:** `feat: period-level Monte Carlo simulation`

---

### Task 10: Game prediction, metrics and tracking  — model: Sonnet

Spec: 4.3 (official prediction), 6.3, 8 (storage), 4.5.

**Files:**
- Create: `pipeline/predict.py`, `pipeline/metrics.py`, `pipeline/tracking.py`, `pipeline/teams.py`, `tests/test_predict.py`, `tests/test_tracking.py`

**Interfaces:**
- Consumes: `Ratings`, `base_lambda`, `home_mult`, `rest_mult`, `PeriodShares`, `split_lambda` (Task 7); `GoalieRatings`, `goalie_mult`, `goalie_block` (Task 8); `simulate` (Task 9); `ScheduledGame`; `ModelParams`.
- Produces:
  - `teams.display_name(abbr: str, fallback: str) -> str`
  - `predict.predict_game(game: ScheduledGame, r: Ratings, ps: PeriodShares, sp: dict, p: ModelParams, last_played: dict[str, date], gr: GoalieRatings, starters: dict[str, list[tuple[int, float]]]) -> dict` — the full game object (Shared Contracts), `state: "upcoming"`. `λ_home = base_lambda(home, away) * M_home`, `M_home = clamp(home_mult * rest_mult(home_b2b, away_b2b) * goalie_mult(starters[away], away, gr, p), 0.80, 1.25)`; `λ_away = base_lambda(away, home) * M_away`, `M_away = clamp(rest_mult(away_b2b, home_b2b) * goalie_mult(starters[home], home, gr, p), 0.80, 1.25)`. `starters` is keyed by team abbreviation; a missing key means `[]`. `goalies` = `goalie_block` per team. `b2b` = `last_played[team] == nhl_date - 1 day`. `xg` = the two λ rounded to 2. `confidence` = `"low"` when either team has fewer than `LOW_CONFIDENCE_MIN_GAMES` in `r.n_games`. Seed = game id. Factor labels: `"Joukkuevahvuus"`, `"Kotietu"`, `"Lepo / back-to-back"`, `"Maalivahti"`.
  - `metrics.score(p_home: np.ndarray, y_home: np.ndarray, base_rate: float) -> dict` → `{"n", "brier", "logloss", "accuracy", "brier_base", "logloss_base", "calibration": [{"lo", "hi", "n", "p_mean", "y_mean"}]}` with 5-percentage-point bins, probabilities clipped to `[1e-6, 1-1e-6]` for log loss.
  - `tracking.log_row(game_obj: dict, ts: str) -> dict`
  - `tracking.append_predictions(game_objs: list[dict], ts: str) -> int` — only `state == "upcoming"`; file chosen by the game's season (`str(id)[:4]` + next year).
  - `tracking.record_results(records: list[dict]) -> int` — idempotent on `game_id`.
  - `tracking.read_log(kind: str, season: str) -> list[dict]` (`kind` ∈ `predictions | results`)
  - `tracking.official(rows: list[dict]) -> dict[int, dict]` — per game the row with the greatest `ts` strictly below `start_utc`.

- [ ] **Step 1: Write failing tests:**

```python
def test_predict_game_matches_contract_keys():
    g = predict_game(...)
    assert g["state"] == "upcoming" and [f["key"] for f in g["factors"]] == ["base", "home_ice", "rest", "goalie"]
    assert all(g[k] is None for k in ("lineup_changes","players","form","season_compare","h2h","insights","odds"))
    assert g["xg"]["home"] == round(expected_lambda_home, 2)
def test_factors_multiply_to_lambda():       # base.home * home_ice.home * rest.home == lambda_home (1e-6, unrounded internals)
def test_goalie_factor_scales_opponent_lambda():   # away goalie mult 0.95 -> factors goalie.home == 0.95, xg.home drops 5 %
def test_goalies_block_present_per_team():         # g["goalies"]["home"]["status"] == "projected"; None for a team without starters
def test_factor_product_is_clamped():              # multipliers 1.10 * 1.10 * 1.10 -> lambda == base * 1.25
def test_b2b_only_when_played_yesterday():   # last_played two days ago -> rest factor 1.0
def test_unknown_team_is_low_confidence_and_average():   # Review Focus 1
def test_predict_is_deterministic():
def test_metrics_perfect_and_coinflip():     # p == y -> brier 0; p all 0.5 -> brier 0.25, logloss ln 2
def test_metrics_baseline_uses_base_rate():  # base_rate 0.54 -> brier_base == mean((0.54 - y) ** 2)
def test_calibration_bins_cover_all_rows():  # sum of n over bins == n
def test_append_writes_one_row_per_upcoming_game_and_skips_started():
def test_record_results_is_idempotent():     assert record_results(recs) == 2 and record_results(recs) == 0
def test_official_is_last_row_before_start():
    # rows at 15:17, 21:37 before a 00:00 start and one at 00:30 after it (Review Focus 5) -> the 21:37 row
def test_log_rows_are_valid_single_line_json():
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Commit:** `feat: game prediction, scoring metrics and prediction log`

---

### Task 11: JSON schema and export  — model: Haiku

Spec: 4.5.

**Files:**
- Create: `pipeline/schema/latest.schema.json`, `pipeline/export.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: game objects (Task 10), source state dict.
- Produces:
  - `export.build_latest(games: list[dict], sources: dict, next_game_date: str | None, now: datetime) -> dict` — top-level keys `schema_version, generated_at, model_version, sources, review, games, next_game_date`; `sources` as a list `[{"id","status","fetched_at","note"}]`; `review` = `{"problems": 0, "worst": None}` (Task 13 overwrites it); games sorted by `start_utc`, then `id`.
  - `export.validate(obj: dict) -> list[str]` — jsonschema error messages with their JSON path, empty when valid.
  - `export.write_latest(obj: dict) -> Path` — raises `ValueError` listing the errors when invalid; writes `site/data/latest.json` atomically (temp file + replace).

Schema (draft 2020-12): top-level and game-level required keys as in Shared Contracts; `state` enum `upcoming | started`; source `status` enum `ok | stale | failed | unavailable`; every probability `number` in `[0, 1]`; `matrix` 8 arrays of 8 numbers; `periods` exactly 3 items; `factors` at least 1 item with `key, label, home, away, unit`; `goalies` an object with `home` and `away`, each `null` or the `G` object with `status` enum `confirmed | likely | projected` and `p_start` in `[0, 1]`; the seven later-phase fields typed `["object", "array", "null"]`; `additionalProperties: true` at game level so later phases can add fields.

- [ ] **Step 1: Write failing tests:**

```python
def test_build_latest_validates():            assert export.validate(export.build_latest([g1, g2], src, None, NOW)) == []
def test_games_sorted_by_start():
def test_empty_games_with_next_date_is_valid():        # Review Focus 2
def test_probability_out_of_range_is_reported():       # home_win 1.2 -> error mentioning "home_win"
def test_missing_required_field_is_reported():         # del g["xg"] -> error mentioning "xg"
def test_unknown_source_status_is_rejected():
def test_later_phase_fields_accept_null_and_objects():
def test_goalie_status_enum_and_null_goalie():      # status "maybe" rejected; goalies.home null accepted
def test_write_refuses_invalid(root):
    with pytest.raises(ValueError): export.write_latest(bad)
    assert not (root / "site" / "data" / "latest.json").exists()
def test_generated_at_is_utc_z():             assert obj["generated_at"].endswith("Z")
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Commit:** `feat: latest.json schema and export`

---

### Task 12: Pipeline orchestration  — model: Sonnet

Spec: 4.1, 4.3, 4.4, 11 (`--offline`), 15.5 (extra run).

**Files:**
- Create: `pipeline/run.py`, `pipeline/sources/injuries.py`, `tests/test_run.py`

**Interfaces:**
- Consumes: everything from Tasks 1–11.
- Produces:
  - `injuries.fetch() -> list[dict]` — one GET to `https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries`, requires top-level `injuries`; raises `SourceError` otherwise. The result is not used by the model in phase 1.
  - `run.optional_source(source_id: str, fn: Callable[[], object], state: dict, now: datetime, failed_status: str = "failed", note_on_fail: str | None = None) -> object | None` — never raises; updates `state[source_id]`.
  - `run.nhl_today(now: datetime) -> str` — date in `America/New_York`.
  - `run.select_games(week: list[ScheduledGame], today: str) -> tuple[list[ScheduledGame], str | None]` — unfinished games with `nhl_date` in `{today, today + 1 day}`; second element = earliest later `nhl_date` with a game when the list is empty.
  - `run.needs_extra_run(games: list[ScheduledGame], now: datetime) -> bool` — true when an upcoming game starts before today's `17:17 UTC`.
  - `run.main(argv: list[str] | None = None) -> int`. CLI: `python -m pipeline.run [--offline DIR] [--now ISO] [--extra-if-early]`.

`main` order: (1) online only: `config.load_gate()` must have `passed == True`, else print `Backtest-ehto ei ole täyttynyt — aja python -m pipeline.backtest` and return 2; (2) schedule weeks covering `today - 7 days` and `today` (core; `SourceError` → return 1, nothing written); (3) with `--extra-if-early` and `needs_extra_run` false → write `ran=false` to `$GITHUB_OUTPUT` when set, return 0; (4) `cache.cache_finished`, `tracking.record_results` for newly written records; (5) load the current and previous season from cache, `xg.load_coefs(model/xg_coefs.json)`, `features.team_games`, `ratings.fit`, `period_shares`, `sim_params`, `adjust.goalie_games`, `adjust.goalie_ratings`; (6) `adjust.project_starters` for both teams of every upcoming game, then `predict_game`; started games are copied from `data/state/last_games.json` with `state: "started"` and dropped when absent there; (7) `tracking.append_predictions`; (8) `optional_source("injuries", injuries.fetch, ..., failed_status="unavailable", note_on_fail="ESPN ei vastannut — ei vaikuta vaiheen 1 ennusteeseen")`, skipped offline with the same status; (9) `export.write_latest`, write `data/state/last_games.json` and `data/state/sources.json`, write `ran=true`. The current season is the `season` of the first selected schedule game, or the newest season directory in the cache when no game is selected; the previous season is the one before it. In offline mode `now` defaults to the schedule fixture's first date at `15:17Z`, and a missing `model/xg_coefs.json` falls back to `tests/fixtures/xg_coefs_stub.json` (create it in this task: 16 coefficients, distance coefficient negative).

- [ ] **Step 1: Write failing tests:**

```python
def test_offline_run_end_to_end(root):
    # root seeded with the two fixture games in cache; --offline tests/fixtures --now <upcoming-week date>T15:17:00Z
    assert run.main([...]) == 0
    latest = json.load(open(root / "site/data/latest.json"))
    assert export.validate(latest) == [] and len(latest["games"]) >= 1
    assert {s["id"]: s["status"] for s in latest["sources"]} == {"nhl": "ok", "injuries": "unavailable"}
    assert tracking.read_log("predictions", season) and (root / "data/state/last_games.json").exists()
    assert all(set(g["goalies"]) == {"home", "away"} for g in latest["games"])
def test_core_source_failure_publishes_nothing(root):     # empty offline dir -> return 1, no latest.json
def test_optional_source_failure_does_not_fail_run():     # fn raises SourceError -> returns None, state status "failed", last_ok_at kept
def test_no_games_gives_empty_list_and_next_date(root):   # Review Focus 2: --now on a date with no games in the fixture weeks
def test_started_game_is_frozen_and_not_logged(root):     # Review Focus 5: second run with --now after first puck drop ->
    # that game has state "started", identical prob to run 1, and no new prediction row for it
def test_started_game_without_previous_export_is_dropped(root):
def test_online_run_refuses_without_gate(root):           assert run.main([]) == 2
def test_extra_run_skips_when_no_early_game(root, monkeypatch):   # GITHUB_OUTPUT file contains "ran=false"; no latest.json
def test_nhl_today_uses_eastern_time():                   assert run.nhl_today(dt("2026-11-16T03:00:00Z")) == "2026-11-15"
def test_select_games_window():
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Run the pipeline by hand:** `python -m pipeline.run --offline tests/fixtures` — expected exit 0 and `site/data/latest.json` present (ignored by git).

- [ ] **Step 6: Commit:** `feat: pipeline orchestration with offline mode and optional sources`

---

### Task 13: Review script  — model: Haiku

Spec: 10 (phase-1 groups: LINT, TEST, SITE, PROB without player checks, ROSTER goalie check only, SCHED, FRESH, SOURCE, TRACK, XG, SECRET).

**Files:**
- Create: `tools/review.py`, `tests/test_review.py`
- Modify: `pipeline/run.py` (write `data/state/schedule_ids.json`: the ids returned by `select_games`), `tests/test_run.py` (assert the file exists after the offline run)

**Interfaces:**
- Consumes: `export.validate`, `tracking.read_log`, `cache`, `xg`, `config`.
- Produces:
  - `@dataclass Problem`: `level: str` (`"VIRHE" | "VAROITUS"`), `group: str, where: str, message: str, hint: str`
  - `CHECKS: list[Callable[[Ctx], list[Problem]]]` — one function per group, so later phases append `check_roster`, `check_odds`. `Ctx` holds `root, latest (dict | None), now, mode ("data" | "full")`.
  - `render(problems: list[Problem], now: datetime) -> str` — header `# Tarkistusraportti {iso minute}Z — {e} virhe(ttä), {w} varoitus(ta)`; one line per problem `[LEVEL] GROUP where — message — vihje: hint`; problems with the same `(level, group, hint)` beyond the first collapse into `(+N muuta samanlaista)`; at most 40 lines; no problems → exactly `OK — 0 ongelmaa`.
  - `main(argv) -> int` — `0` none, `1` warnings only, `2` any error. Writes `reports/review.md` and sets `latest.json["review"] = {"problems": n, "worst": "error" | "warning" | None}`. Flags `--skip-tests`, `--data-only` (skips LINT, TEST, SECRET over source files).

Check thresholds: PROB error — `home_win + away_win` and 1X2 sums within 0.001 of 1, matrix sum within 0.01 of 1, every probability in `[0, 1]`. PROB warning — `home_win > 0.80` or `< 0.20`; `xg` outside `1.5–5.0`; a goalie `mult` at `0.90` or `1.10`. ROSTER warning — an upcoming game where a team's `goalies` entry is `null`. SCHED error — an id in `data/state/schedule_ids.json` that is neither in `latest.json` nor finished. FRESH error — `generated_at` older than 1 h in data mode; warning — a game finished more than 24 h ago missing from the cache. SOURCE warning — status `failed` or `stale` (`unavailable` is not a problem). TRACK warning — newest prediction row older than 24 h while games exist; a game that ended more than 24 h ago without a result row. XG warning — last 7 days `sum(xG) / goals` outside `0.85–1.15` (skipped with fewer than 20 cached games in the window). SITE error — schema errors; `node --check site/app.js` when `node` is on PATH. SECRET error — regex `\b[0-9a-f]{32}\b` in tracked text files (skip `data/cache/`, `*.gz`, `tests/fixtures/`) and in `site/data/*.json`.

- [ ] **Step 1: Write failing tests** (`root` seeded with a valid `latest.json` from Task 11 helpers, mode data, `--skip-tests`):

```python
def test_clean_data_reports_ok(root):         assert review.main(ARGS) == 0 and report(root) == "OK — 0 ongelmaa\n"
def test_bad_probability_sum_is_error(root):  # home_win 0.7, away_win 0.4 -> exit 2, a line starting "[VIRHE] PROB"
def test_extreme_probability_is_warning(root):   # home_win 0.85 -> exit 1, "[VAROITUS] PROB", message contains "0.85"
def test_matrix_sum_tolerance(root):          # sum 0.995 passes, 0.97 is an error
def test_missing_goalie_is_warning(root):     # goalies.home None -> exit 1, "[VAROITUS] ROSTER"
def test_stale_generated_at_is_error(root):
def test_unavailable_source_is_not_a_problem(root):
def test_failed_source_is_warning(root):
def test_missing_scheduled_game_is_error(root):
def test_secret_shaped_string_is_error(root): # a tracked .py containing 32 hex chars -> exit 2, "[VIRHE] SECRET"
def test_similar_problems_are_grouped_and_report_is_capped(root):   # 60 problems -> <= 40 lines, contains "muuta samanlaista"
def test_review_field_is_written_back(root):  assert latest(root)["review"] == {"problems": 1, "worst": "warning"}
def test_missing_latest_json_is_error(root):
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest`, then `python tools/review.py --skip-tests` on the real tree — expected `OK — 0 ongelmaa` or only real findings; fix real findings.

- [ ] **Step 5: Commit:** `feat: problems-only review script`

---

### Task 14: Backtest, tuning and go-live gate  — model: Sonnet (escalate to the planner if the gate fails twice, spec 15.4)

Spec: 8 (backtest), 15.1, 15.2 (goalie part), 15.3.

**Files:**
- Create: `pipeline/backtest.py`, `tests/test_backtest.py`
- Produced by running it: `model/xg_coefs_bt.json`, `model/xg_coefs.json`, `model/params.json`, `model/backtest_20252026.json`, `reports/backtest.md`, `data/cache/games/**`

**Interfaces:**
- Consumes: `adjust.goalie_games`, `goalie_ratings`, `project_starters`, `goalie_mult`, `tests/synth.make_goalie_games` (Task 8); `ratings.fit`, `period_shares`, `split_lambda`, `base_lambda`, `home_mult`, `rest_mult`, `features.sim_params`, `simulate`, `metrics.score`, `tests/synth.make_team_games`.
- Produces:
  - `run_backtest(tg: pd.DataFrame, season: str, p: ModelParams, n_sims: int = 2000, gg: pd.DataFrame | None = None, goalie_mode: str = "none") -> dict` → `{"rows": DataFrame[game_id, date, p_home, y_home, p_reg_draw, y_reg_draw, total_mean, total_actual], "metrics": metrics.score(...)}`. For each game date of `season` (game type 2) in order: ratings, period shares and `SimParams` are computed from rows with `date < that date` only, once per date. `base_rate` = home win rate of the previous season. `goalie_mode`: `none` = no goalie multiplier (15.2 a); `actual` = each team's boxscore starter of that game with probability 1.0 (15.2 b, perfect information); `projected` = `project_starters` as of the game date. In every mode `goalie_ratings` uses rows with `date < that date` only.
  - `load_team_games(seasons: list[str], coefs_path: Path, xg_weight: float) -> pd.DataFrame` — cache → `features.team_games`; the CLI's only data entry point (tests monkeypatch it).
  - `load_goalie_games(seasons: list[str], coefs_path: Path) -> pd.DataFrame` — cache → `adjust.goalie_games` (tests monkeypatch it).
  - `kappa_from(rows: DataFrame) -> float` = `a(1-d) / (d(1-a))`, `a` = mean `y_reg_draw`, `d` = mean `p_reg_draw` (run with `draw_kappa=1`).
  - `tune(tg: pd.DataFrame, season: str, base: ModelParams, gg: pd.DataFrame | None = None) -> ModelParams` — grid `half_life_days ∈ {25, 40, 60}` × `alpha ∈ {0.005, 0.02, 0.05, 0.2}` with `n_sims=1000`, pick lowest log loss; then `sigma_pace = sigma_team ∈ {0.0, 0.08, 0.15}` by log loss; then `draw_kappa = kappa_from(...)`; these three steps run with `goalie_mode="none"`. Last, when `gg` is given: `goalie_weight ∈ {0.0, 0.3, 0.6, 0.9}` by log loss with `goalie_mode="actual"`.
  - `gate(m: dict) -> bool` = `m["brier"] < m["brier_base"] and m["logloss"] < m["logloss_base"]`
  - CLI `python -m pipeline.backtest --tune-season 20242025 --gate-season 20252026 [--variant full|no_xg|no_periods]`. `no_xg` sets `xg_weight=0`; `no_periods` sets `xg_weight=0, use_periods=False`. Uses `model/xg_coefs_bt.json`. On the gate season it runs all three goalie modes with the tuned parameters. The gate is judged on `projected`; when `projected` has a higher log loss than `none`, it writes `use_goalie: false` and judges the gate on `none`. Writes `model/params.json` (`ModelParams` fields + `"gate": {"passed", "variant", "season", "n", "brier", "brier_base", "logloss", "logloss_base", "accuracy", "goalie_mode", "goalie_modes": {"none": {"brier", "logloss"}, "actual": {...}, "projected": {...}}}`), `model/backtest_{season}.json` (metrics + calibration, for the phase-4 performance page), and `reports/backtest.md` (one table, under 30 lines). Exit 0 when the gate passes, 1 otherwise.

- [ ] **Step 1: Write failing tests** (synthetic league, strengths spread ±0.3, 3 seasons):

```python
def test_backtest_never_sees_the_future(monkeypatch):
    # wrap ratings.fit: assert tg rows passed in have date < asof, and asof == the date being predicted
def test_backtest_beats_baseline_on_synthetic_league():   assert gate(run_backtest(tg, "20252026", P)["metrics"])
def test_backtest_does_not_beat_baseline_on_noise():      # all strengths 0, home_adv 0 -> not clearly better: brier >= brier_base - 0.002
def test_rows_one_per_game():
def test_goalie_mode_none_equals_run_without_goalies():
def test_actual_mode_uses_boxscore_starter_with_probability_one(monkeypatch):   # capture goalie_mult's starters argument
def test_projected_mode_and_ratings_use_only_earlier_games(monkeypatch):        # gg seen by project_starters / goalie_ratings has date < game date
def test_tune_picks_goalie_weight_from_grid():
def test_cli_disables_goalie_when_projected_is_worse(root, monkeypatch):        # run_backtest stubbed -> params.json use_goalie false, gate.goalie_mode "none"
def test_kappa_from_closed_form():        # a = 0.22, d = 0.20 -> 0.22*0.80 / (0.20*0.78)
def test_tune_returns_grid_values():
def test_gate_requires_both_metrics():    assert not gate({"brier": .24, "brier_base": .25, "logloss": .70, "logloss_base": .69})
def test_cli_writes_params_with_gate(root, monkeypatch):   # load_team_games stubbed with synth data -> model/params.json has gate.passed bool, exit code matches it
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass. **Commit:** `feat: leak-free backtest, tuning and go-live gate`

- [ ] **Step 5: Backfill real data** (network, about 50 minutes, run in the background): `python -m pipeline.backfill --seasons 20232024 20242025 20252026 20262027`. Expected summary: about 1 312 written per finished season, `skipped` under 10 in total. Re-run once; expected `written: 0`. Check `du -sh data/cache` is under 100 MB. Commit per season: `git add data/cache/games/20232024 && git commit -m "data: cache 2023-24"` and so on.

- [ ] **Step 6: Train xG.** `python -m pipeline.xg train --train 20232024 20242025 --test 20252026 --out model/xg_coefs_bt.json`, then `python -m pipeline.xg train --train 20242025 20252026 --test 20262027 --out model/xg_coefs.json`. Expected for both: `logloss < logloss_const`, `auc >= 0.70`. If not, stop and report the metrics (do not loosen the threshold).

- [ ] **Step 7: Run the gate.** `python -m pipeline.backtest --tune-season 20242025 --gate-season 20252026`. Expected: exit 0 and `gate.passed: true`. Sanity limits to check in `reports/backtest.md`: accuracy 0.52–0.62, fitted home multiplier 1.01–1.10, mean λ 2.6–3.4, `actual` log loss not worse than `none` (if it is, the goalie multiplier has the wrong sign or leaks: stop and investigate), share of goalie multipliers at the clamp under 5 %. On failure: rerun with `--variant no_xg`, then `--variant no_periods`. If the second attempt also fails, stop and escalate; do not go live.

- [ ] **Step 8: Online smoke run.** `python -m pipeline.run` then `python tools/review.py --data-only`. Expected: exit 0, `latest.json` with tonight's games, every `home_win` within 0.20–0.80, review exit 0 or 1.

- [ ] **Step 9: Commit:** `git add model reports data/history data/state && git commit -m "feat: trained xG model, tuned parameters, gate passed"`

---

### Task 15: Mobile site  — model: Sonnet

Spec: 9 (top bar, view 1 card, view 2 sections 1, 2, 3, 5, view 5, error states), 1.3 (freshness), 13.9 (`noindex`).

**Files:**
- Create: `site/index.html`, `site/app.js`, `site/format.js`, `site/styles.css`, `site/manifest.webmanifest`, `site/icon.svg`, `site/tests/format.test.js`

**Interfaces:**
- Consumes: `site/data/latest.json` (Shared Contracts and spec 4.5); produce a local copy with `python -m pipeline.run --offline tests/fixtures`.
- Produces (`site/format.js`, ES module, pure functions, no DOM):
  - `fmtTime(iso) -> "02.00"`, `fmtDate(nhlDate) -> "su 15.11."` (both `fi-FI`, `Europe/Helsinki`; `fmtDate` formats the calendar date as given)
  - `fmtPct(p) -> "64 %"`, `fmtNum(x, digits = 1) -> "2,6"`, `fmtScore({home, away}) -> "3–2"` (home first)
  - `ageText(generatedAt, now) -> "12 min sitten" | "3 h sitten" | "2 pv sitten"`
  - `freshness(latest, now) -> "ok" | "warn" | "stale"` — `stale` when `generated_at` is more than 14 h old or the `nhl` source is not `ok`; `warn` when any other source is not `ok` or `review.worst === "error"`; else `ok`
  - `groupByDate(games) -> [{nhl_date, games}]` sorted ascending
  - `fmtSignedPct(mult) -> "−2,4 %" | "+1,0 %"` (from a multiplier; one decimal, true minus sign)
  - `SUPPORTED_SCHEMA = 1`

Structure and data binding (styling comes from the design system, Step 1):
- Hash routes: `#/` game list, `#/ottelu/{id}` game detail, `#/tila` source status. Bottom navigation is built from a `TABS` array; phase 1 has one entry (`Ottelut`), later phases add `Ylikertoimet` and `Malli`.
- Top bar on every view: `Päivitetty {fmtTime} ({ageText})` and a status dot (`ok` green, `warn` yellow, `stale` red) linking to `#/tila`. When `stale`: banner `Data on yli 14 tuntia vanhaa tai NHL-haku epäonnistui — ennusteet voivat olla vanhentuneita.`
- List: heading `Ensi yön ottelut · {fmtDate}` for the first date group, `{fmtDate}` for later groups. Card per game: start time; away abbr and `away_win` on the left, home abbr and `home_win` on the right, a probability bar between them; `xG {away} – {home}`; `todennäk. {most_likely}`; badge `Käynnissä — ennuste jäädytetty` when `state === "started"`; badge `Pieni otos` when `confidence === "low"`. The whole card links to the detail route.
- Detail: a `SECTIONS` array of `{id, title, render(game) -> string | null}`; a section returning `null` is not rendered, which is how the seven null later-phase fields stay invisible now. Phase-1 sections: `header` (teams with logos — `logo_dark` in dark theme —, time, the two win probabilities large, `Varsinainen peliaika: 1 {reg_home} · X {reg_draw} · 2 {reg_away}`, confidence badge); `score` (xG bars, most likely score, top 5 scores with percentages, 8×8 score matrix as an inline SVG or grid heat map with a `7+` edge label, `Maaleja keskimäärin {total.mean}`, `Yli 5,5` and `Yli 6,5`, `−1,5` lines for both teams); `factors` (title `Mistä ennuste koostuu`; `base` row in goals, `mult` rows as signed percentages); `periods` (per period xG for both and the three outcome probabilities); `goalies` (title `Maalivahdit`; per team: name, status badge `confirmed → Vahvistettu`, `likely → Todennäköinen`, `projected → Arvio`, `aloittaa {fmtPct(p_start)} todennäköisyydellä` when `p_start < 1`, `Torjunnat yli odotuksen / 60 min: {gsax_per60}` with sign, and `Vaikutus vastustajan maaliodotukseen: {fmtSignedPct(mult)}`; a `null` team shows `Ei maalivahtitietoa`).
- Status view: one row per source (Finnish name map `nhl → NHL`, `injuries → Loukkaantumiset (ESPN)`; status text `ok → Kunnossa`, `stale → Vanhentunut`, `failed → Epäonnistui`, `unavailable → Ei saatavilla`; `fetched_at`; `note`), `Tarkistusraportti: {review.problems} ongelmaa`, `Mallin versio {model_version}`.
- States: fetch fails → `Dataa ei saatu ladattua` with a `Yritä uudelleen` button; `games` empty → `Ei otteluita.` plus `Seuraava pelipäivä: {fmtDate(next_game_date)}` when set; `schema_version !== SUPPORTED_SCHEMA` → `Sivu ja data ovat eri versiota (skeema {n}). Päivitä sivu.`; unknown game id in the route → back to the list.
- `fetch("data/latest.json", {cache: "no-store"})`. No service worker. `<meta name="robots" content="noindex">`, `<html lang="fi">`. Manifest: `name: "NHL-analyysi"`, `display: "standalone"`, `start_url: "."`, `icon.svg`. Footer on every view: `Luvut ovat mallin arvioita, eivät takuita.`
- No external scripts, fonts or CSS; charts are inline SVG or CSS; logos are the only remote assets and have `alt` text and a text fallback on load error.

- [ ] **Step 1: Invoke the `ui-ux-pro-max` skill** for the design system: mobile-first sports analytics app, Finnish UI, dark theme default with light theme via `prefers-color-scheme`, plain HTML/CSS. Record the chosen tokens (colors, type scale, spacing, radii) as CSS custom properties at the top of `styles.css`. Content and structure above are fixed; the skill decides appearance only.

- [ ] **Step 2: Write failing tests** in `site/tests/format.test.js` (`node:test`, `node:assert`):

```js
test("fmtTime winter", () => assert.equal(fmtTime("2026-11-16T00:00:00Z"), "02.00"));
test("fmtTime summer DST", () => assert.equal(fmtTime("2026-04-12T23:00:00Z"), "02.00"));
test("fmtDate", () => assert.equal(fmtDate("2026-11-15"), "su 15.11."));
test("fmtPct", () => assert.equal(fmtPct(0.642), "64 %"));
test("fmtNum uses comma", () => assert.equal(fmtNum(2.61), "2,6"));
test("fmtSignedPct", () => { assert.equal(fmtSignedPct(0.976), "−2,4 %"); assert.equal(fmtSignedPct(1.01), "+1,0 %"); });
test("fmtScore", () => assert.equal(fmtScore({home: 3, away: 2}), "3–2"));
test("ageText", () => { /* 12 min -> "12 min sitten"; 3 h -> "3 h sitten"; 50 h -> "2 pv sitten" */ });
test("freshness stale after 14 h", ...); test("freshness stale when nhl source failed", ...);
test("freshness warn when injuries unavailable", ...); test("freshness ok", ...);
test("groupByDate sorts and groups", ...);
```

- [ ] **Step 3: Run** `node --test site/tests/` — expected FAIL. If `node` is not installed locally, skip the local run and rely on CI (Task 16); say so in the task report.

- [ ] **Step 4: Implement `format.js`, then `index.html`, `app.js`, `styles.css`, manifest and icon.** `app.js` imports `format.js` (`<script type="module">`) and escapes every string it inserts into HTML.

- [ ] **Step 5: Run** `node --test site/tests/` and `node --check site/app.js` — expected pass.

- [ ] **Step 6: Check in a browser.** `python -m pipeline.run --offline tests/fixtures`, then `python -m http.server 8000 -d site`. At 375 px width verify: list renders, a card opens the detail view, back works, `#/tila` renders, no horizontal scroll, light and dark both readable. Then verify the three error states by temporarily (a) renaming `site/data/latest.json`, (b) setting `games` to `[]` with `next_game_date`, (c) setting `schema_version` to 99. Restore the file.

- [ ] **Step 7: Commit:** `feat: mobile site with game list, game detail and source status`

---

### Task 16: GitHub Actions workflows and README  — model: Haiku

Spec: 2, 4.2, 4.3, 4.4, 15.5 (extra run).

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/pipeline.yml`, `.github/workflows/backfill.yml`, `.github/workflows/train_xg.yml`, `README.md`, `tests/test_workflows.py`

**Interfaces:**
- Consumes: `python -m pipeline.run [--extra-if-early]` (writes `ran=true|false` to `$GITHUB_OUTPUT`), `python tools/review.py --data-only` (exit 0/1/2), `python -m pipeline.backfill`, `python -m pipeline.xg train`.

Workflow requirements:
- All: `ubuntu-latest`, `actions/checkout@v4`, `actions/setup-python@v5` with `python-version: "3.12"` and `cache: pip`, `pip install -r requirements.txt` (`requirements-dev.txt` in `ci.yml`).
- `ci.yml`: on `push` and `pull_request`; steps `ruff check .`, `python -m pytest`, `node --test site/tests/`, `node --check site/app.js`.
- `pipeline.yml`: `on.schedule` crons `43 6 * * *`, `17 15 * * *`, `37 21 * * *`, `17 12 * * 0,6`, plus `workflow_dispatch`. `concurrency: {group: pipeline, cancel-in-progress: false}`. `permissions: {contents: write, pages: write, id-token: write}`. `timeout-minutes: 20`. Steps in order:
  1. `id: run` — `python -m pipeline.run` with `--extra-if-early` only when `github.event.schedule == '17 12 * * 0,6'`.
  2. `id: review`, `if: steps.run.outputs.ran == 'true'` — run the review, store its exit code in an output (`code`), never fail the step.
  3. `if: steps.run.outputs.ran == 'true'` — commit `data/` and `reports/` as `github-actions[bot]` with message `data: run {UTC timestamp}`, only when there is a diff; `git pull --rebase` before `git push`.
  4. `if: ran == 'true' && steps.review.outputs.code != '2'` — `actions/configure-pages@v5`, `actions/upload-pages-artifact@v3` with `path: site`, `actions/deploy-pages@v4`.
  5. `if: steps.review.outputs.code == '2'` — `exit 1` (GitHub then e-mails the owner; the previous site stays up).
  A failing step 1 fails the job before anything is committed or deployed. A skipped extra run deploys nothing.
- `backfill.yml`: `workflow_dispatch` with input `seasons` (default `20232024 20242025 20252026 20262027`); `timeout-minutes: 120`; `contents: write`; runs the backfill, commits `data/cache` as `data: backfill`, pushes.
- `train_xg.yml`: `workflow_dispatch` with inputs `train` (two seasons) and `test`; commits `model/xg_coefs.json`.
- No secret is referenced in phase 1.

`README.md` (Finnish, under 60 lines): what the project is, local setup, `python -m pipeline.run --offline tests/fixtures`, `python tools/review.py`, the three cron times in Finnish time, how to fix problems ("lue `reports/review.md` ja korjaa"), and one sentence on the 60-day rule: GitHub disables scheduled workflows only after 60 days without repository activity, and that does not apply here because every pipeline run commits `data/state/sources.json`, which counts as activity, so no manual re-enabling is needed.

- [ ] **Step 1: Write failing tests** (`tests/test_workflows.py` reads the files as text and asserts with regexes; PyYAML is not a dependency):

```python
def test_pipeline_has_four_crons():     # the four cron strings appear exactly once each
def test_pipeline_has_concurrency_and_permissions():
def test_deploy_is_guarded_by_ran_and_review_code():   # the upload-pages-artifact step's `if:` contains both conditions
def test_no_secret_reference_in_phase_1():             assert "secrets." not in text for all four files
def test_actions_are_pinned_to_major_versions():       # every `uses:` matches r"@v\d+$"
def test_ci_runs_ruff_pytest_and_node_tests():
```

- [ ] **Step 2: Run** — expected FAIL. **Step 3: Write the workflows and README.** **Step 4: Run** `python -m pytest` and `python tools/review.py` (full mode) — expected: all tests pass, review exit 0.

- [ ] **Step 5: Commit:** `feat: CI, scheduled pipeline, backfill and xG training workflows`

- [ ] **Step 6: Publish — needs the user's confirmation before each command, because it makes the code and data public.** `gh repo create nhl-analyysi --public --source . --push`; in the repo settings set Pages source to "GitHub Actions" and Actions workflow permissions to "Read and write"; `gh workflow run pipeline.yml`; `gh run watch`. Expected: run green, site reachable at the Pages URL, list shows tonight's games, top bar age under 10 minutes. Open it on the phone and add it to the home screen.

---

## Execution Notes for the Dispatcher
- Order is strict for Tasks 1–4. After Task 4: Tasks 5 → 6 → 7 are sequential; Task 8 (goalies) follows Task 7; Task 9 depends only on Task 1 and can run in parallel with 5–8; Task 11's schema can start after Task 1. Tasks 15 and 16 need Task 12's output shape only.
- Run `python tools/review.py` after every task from Task 13 on; read only `reports/review.md`.
- Task 14 steps 5–8 use the network and real data and are run by the controlling session, not delegated blind; they are the only place where modelling judgement is needed.
- Phase 2 entry points already in place: `sources` list and `optional_source`, `adjust.py` (lineup and H2H multipliers join the goalie one; `goalie_block` already takes a `status` for a confirmed-starter source), `odds: null` and `market`/`picks: null` in the contracts, `data/history/odds/` path reserved, `teams.py`, `review.CHECKS`, `SECTIONS`/`TABS` in the frontend, `simulate` returning `goal_dist` and all four spread keys.
