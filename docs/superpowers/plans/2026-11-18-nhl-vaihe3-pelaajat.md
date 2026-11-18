# NHL-analyysi, Phase 3 (player probabilities) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a draw-calibration monitor, then per-player goal and point probabilities (P ≥ 1/2/3) for every upcoming game, derived from each team's simulated goal distribution, gated by a leak-free player backtest against a season-average baseline, logged for live tracking, and shown in Finnish as a "Pelaajat" section per game and a "Pelaajat" tab for tonight's top 10.

**Architecture:** A new module `pipeline/players.py` turns the cache into a per-player-per-game table, fits recency-weighted, position-shrunk goals/60 and points/60 rates, projects tonight's lineup and TOI from the team's latest cached game, and thins the team's *credited* goal distribution (new `simulate` output: regulation goals incl. empty-net + the overtime winner's goal) binomially into player tails. `predict_game` attaches the block when the player gate in `model/player_params.json` has passed (always offline). `pipeline/player_backtest.py` reuses `backtest.run_backtest` (now able to keep goal distributions) to tune two player parameters on 2024-25 and gate on 2025-26. Review, tracking, schema and the site read the new `game.players` block.

**Tech Stack:** unchanged (Python 3.12, requests, numpy, pandas, scikit-learn, jsonschema, pytest, ruff; plain HTML/CSS/ES modules). No new dependencies — binomial terms use `math.comb`, not scipy.

**Spec:** `docs/superpowers/specs/2026-11-15-nhl-analyysi-design.md` — sections 15–17 override earlier sections; this phase implements 1.1 item 3, 4.1 step 6, 4.5 `players`, 6.10, 8 (player Brier), 9 view 2 section 6, 10 (PROB/ROSTER player rows), 11 (binomial tests). Previous plans: `docs/superpowers/plans/2026-11-15-nhl-vaihe1-mvp.md` and `docs/superpowers/plans/2026-11-18-nhl-vaihe2-kertoimet.md` — their Global Constraints and Shared Contracts still apply; this plan only adds to them. Code signatures in the repo win over both old plans.

## Scope

In: draw-calibration monitor (review warning + status page, no refit); `pipeline/players.py`; credited goal distribution in `simulate`; OT-goal share in `features.sim_params`; `game.players` + schema; player prediction log; player backtest, tuning and gate; review checks; frontend section and tab; spec section 17 decisions 19–28.

Out (do not build): spec 6.6 lineup/injury multiplier on team λ (deferred to phase 4, decision 25), EV/PP rate split (decision 27), roster or injury sources, player-prop odds (shape reserved: `players.*.top[].odds = null`), performance page, insights.

## Plan decisions (append to spec section 17 in Task 9, in Finnish)

19. **Player rates are per-60 rates, not shares.** Per player: goals/60 from `ĝ = xg_blend·ixG + (1 − xg_blend)·goals` (spec 6.10's 0.5/0.5 blend) and points/60 from `ĝ + assists`; time-weighted with `ratings.weights` (player half-life), shrunk toward the F/D position average with a prior worth `prior_toi_h` hours of ice time (5 h ≈ spec's 15 games). The previous season enters through `ratings.weights` (×0.7, 60-day gap), so no separate prior table exists. Rates follow the player id, so a traded player keeps his own rates on the new team.
20. **Tonight's lineup** = skaters who dressed in the team's most recent cached game, minus any who dressed for another team after it. `toi_proj` = mean TOI of the player's last 5 cached games (any team). Status is always `projected` ("Arvio"). Spec 6.6's roster/ESPN sources are not used (no roster fetch exists; ESPN is `unavailable`). A newly acquired player appears after his first game for the new team.
21. **Credited goals** = regulation goals including empty-net goals, plus the overtime winner's goal with probability `ot_goal_share` (league share of OT/SO games decided in OT, from the cache). Shootout goals are credited to nobody. Players are thinned from this distribution.
22. **Unassigned share** `u` = share of team goals scored by dressed players who were not in the projected lineup; measured on the tune season and stored in `model/player_params.json`. Σ over the lineup of expected goals = `(1 − u) ×` team credited goals mean.
23. **`game.players` is an object, not the spec 4.5 flat list** — `{lineup, home, away}` with per-team summaries so review can check sums; each team exports the union of its top 10 by P(≥1 point) and top 10 by P(≥1 goal). Each player has `odds: null`, reserved for player props.
24. **Player gate:** `model/player_params.json` (`gate.passed`) is written only when the 2025-26 player backtest beats the baseline on both Brier and log loss for both P(≥1 point) and P(≥1 goal). Online runs fill `players` only when it passed; offline runs always do.
25. **Spec 6.6 lineup/injury λ adjustment is deferred to phase 4.** Without a roster or injury source the only absence signal is "missed the last game", which the 40-day-half-life team rating already partly contains, and tuning ρ needs backtest variant 15.2(b) with lineups — a separate gated experiment. Phase 3's contribution rates make it cheap to add later. `factors` and `lineup_changes` are unchanged.
26. **Draw monitor** (spec 6.9 item 4): over the most recent 300 games of the current season that have an official prediction of the current `MODEL_VERSION` and a result, `gap = mean model reg_draw − actual regulation-draw rate`. Review warns when `n ≥ 100` and `|gap| > 0.02`. The status page shows it. No refit in this phase.
27. **No EV/PP split.** Cached skater rows carry total TOI only (no PP TOI), so per-60 PP rates cannot be computed; shot `strength` alone would give PP goals without PP time.
28. **Player log is compact and results are derived.** `data/history/players/{season}.jsonl` holds one row per upcoming game per run with all projected players as arrays; outcomes come from the immutable cache (boxscore lines), joined like `official`, not from a second log. `MODEL_VERSION` stays `1.1.0` (team model unchanged); player rows carry `PLAYER_MODEL_VERSION = "1.0.0"`.

## Global Constraints

Phase-1 and phase-2 Global Constraints apply unchanged. Additionally:

- All new numbers live in `pipeline/config.py` (`PlayerParams` defaults or module constants listed in Tasks 1, 2 and 4). Exact values: `DRAW_MONITOR_GAP = 0.02`, `DRAW_MONITOR_MIN_GAMES = 100`, `DRAW_MONITOR_WINDOW = 300`; `PLAYER_TOP_N = 10`, `PLAYER_TOI_GAMES = 5`, `PLAYER_PRIOR_FALLBACK = {"F": (0.85, 2.1), "D": (0.25, 1.1)}` (g60, p60), `PLAYER_PPG_FALLBACK = 2.7`, `PLAYER_PPG_MAX = 3.0`, `PLAYER_PI_MAX = 0.95`, `PLAYER_SUM_TOL = 0.10`, `PLAYER_DRESSED = 18`, `PLAYER_MIN_TOP = 5`, `OT_GOAL_SHARE_FALLBACK = 0.6`, `PLAYER_MODEL_VERSION = "1.0.0"`, grids `PLAYER_PRIOR_GRID = (2.5, 5.0, 10.0)`, `PLAYER_HALF_LIFE_GRID = (40.0, 80.0)`, `PLAYER_UNASSIGNED_RANGE = (0.0, 0.10)`.
- `schema_version` stays `1`; `MODEL_VERSION` stays `"1.1.0"` (additive changes only: `players` goes from always-null to object-or-null, top-level `calibration` is new and optional in the schema).
- A failure while computing one game's players never fails the run: that game's `players` is `null` and a warning is logged (same pattern as per-game odds in `run.py`).
- Simulation stays deterministic: the credited distribution is computed from existing arrays and weights, with no new random draws, so every existing `simulate` output is byte-identical.
- The player backtest is leak-free: rates, lineups and baselines for a date use only cache rows with `date < day`; xG for `ixG` comes from `model/xg_coefs_bt.json`.
- UI copy Finnish, exactly as written in Tasks 1 and 8; percentages in player tables as integers (`fmtPct`, "61 %"); TOI as `19,4 min`; every inserted string through `esc`; at 375 px no horizontal page scroll.
- Tests never touch the network; xG in unit tests is stubbed like `tests/test_adjust.py::stub_score`.

## Review Focus

1. A team with no cached game yet (season opener, or most offline fixture teams): its team block is `null`, the other team's block is still filled, `players` is `null` only when both are; nothing raises; the section says so. Tests: Task 3 `test_team_without_games_returns_empty`, Task 4 `test_game_players_one_team_missing`, Task 5 `test_offline_run_fills_players_where_history_exists`, Task 8 manual check.
2. A player traded mid-season: keeps his own rates; appears only in the new team's lineup once he has dressed there; disappears from the old team's lineup. Tests: Task 2 `test_traded_player_keeps_rates`, Task 3 `test_traded_away_player_dropped`.
3. A debutant (one cached game, no earlier history): rates equal the shrunk position prior plus his one game, probabilities finite and below the regular players'. Tests: Task 2 `test_unknown_player_gets_position_prior`, Task 4 `test_debutant_probabilities_finite`.
4. Extreme inputs to the binomial step — all mass in the `10+` bucket, a star whose π hits the cap, a pure sniper with few assists: tails stay monotone, `p_goals[k] ≤ p_points[k]`, all within 0–1. Tests: Task 4 `test_tails_monotone_and_goals_below_points`, `test_pi_capped_and_at_least_q`.
5. Player names that are `null` or contain `<`, `&`, `"`: shown as `#{id}` or escaped, never as HTML. Tests: Task 8 `playerName fallback` (node) and the browser step with a doctored name.

## Shared Contracts

Give this section, Global Constraints, the phase-1 and phase-2 Shared Contracts, the task itself and the spec sections it cites to every implementer.

**`PlayerParams`** (`pipeline/config.py` dataclass; `load_player_params()` overlays `model/player_params.json`, key `gate` excluded, unknown keys raise like `load_params`): `half_life_days=40.0, prior_toi_h=5.0, xg_blend=0.5, unassigned=0.03`. `load_player_gate() -> dict | None` returns the file's `gate`.

**`player_games`** DataFrame (one row per skater per cached game): `game_id, season, game_type, date (datetime64), team, opp, player (int), name, pos ("C"|"L"|"R"|"D"), grp ("F"|"D"), toi (seconds), g, a, pts (= g + a), ixg, team_goals` where `ixg` = Σ xG of the player's non-empty-net shots (all periods in the cache) + his empty-net goals, and `team_goals` = Σ `g` of that team's skaters in the game.

**`Slot`** (one projected skater): `{"id": int, "name": str | None, "pos": str, "toi_proj": float}` (minutes, 1 decimal).

**`PlayerRates`** (dataclass in `players.py`): `g60: dict[int, float]`, `p60: dict[int, float]`, `pos_g60: dict[str, float]`, `pos_p60: dict[str, float]` (keys `"F"`, `"D"`), `ppg: float`; method `rate(pid: int, pos: str) -> tuple[float, float]` → the player's `(g60, p60)` or the position prior.

**`PlayerInputs`** (dataclass in `players.py`): `rates: PlayerRates`, `lineups: dict[str, list[Slot]]`, `baseline: dict[int, tuple[float, float]]` (player → baseline `(P≥1 point, P≥1 goal)`), `pp: PlayerParams`.

**Credited distribution:** `simulate(...)["credited_dist"] = {"home": [11 floats], "away": [11 floats]}` — P(credited goals = 0..9, ≥ 10), weighted, rounded to 5 decimals. `predict_game` stores it as `_internal["credited_dist"]`.

**`game.players`** (null when neither team has a lineup, when players are disabled, or on failure; frozen with the rest of a started game):

```jsonc
{"lineup": "projected",                     // projected | confirmed (later source)
 "home": {                                  // TeamPlayers or null
   "team": "BOS", "dressed": 18,
   "goals_mean": 3.214,                     // mean of credited_dist
   "exp_goals": 3.118, "exp_points": 8.35,  // sums over ALL projected skaters
   "unassigned": 0.03,
   "top": [{"id": 8478403, "name": "…", "team": "BOS", "pos": "C", "toi_proj": 19.4,
            "p_points": [0.6123, 0.2411, 0.0702], "p_goals": [0.3301, 0.0612, 0.0081],
            "exp_goals": 0.402, "exp_points": 0.951, "odds": null}]
 },
 "away": {…}}
```

`top` = union of the team's `PLAYER_TOP_N` highest `p_points[0]` and `PLAYER_TOP_N` highest `p_goals[0]`, sorted by `p_points[0]` desc, then `p_goals[0]` desc, then id. Probabilities 4 decimals, `exp_*` 3 decimals, `toi_proj` 1 decimal.

**Player log** (`data/history/players/{season}.jsonl`, one row per upcoming game with players per run):
`{"ts", "player_model": "1.0.0", "game_id", "start_utc", "teams": {"BOS": {"goals_mean": 3.214, "unassigned": 0.03, "players": [[id, pos, toi_proj, pts1, pts2, pts3, g1, g2, g3, base_pts1, base_g1], ...]}, "LAK": {...}}}` — column order is `config.PLAYER_LOG_COLUMNS = ("id", "pos", "toi", "pts1", "pts2", "pts3", "g1", "g2", "g3", "base_pts1", "base_g1")`, probabilities 4 decimals, all projected skaters (not only `top`).

**`latest.calibration`** (top level, optional in the schema, always written by `build_latest`): `{"draw": DrawGap | null}` with `DrawGap = {"season": "20262027", "model_version": "1.1.0", "n": int, "model": float, "actual": float, "gap": float, "warn": bool}` (floats 4 decimals; `warn = n ≥ DRAW_MONITOR_MIN_GAMES and |gap| > DRAW_MONITOR_GAP`).

## File Structure

```
pipeline/config.py              + draw-monitor and player constants, PlayerParams, load_player_params/gate
pipeline/tracking.py            + draw_gap, player log (append/read), player_outcomes
pipeline/metrics.py             + binary_vs_base, calibration_bins (score() reuses calibration_bins)
pipeline/players.py        NEW  player_games, rates, baseline, lineup, binomial tails, team/game blocks
pipeline/features.py            + sim_params()["ot_goal_share"]
pipeline/simulate.py            + credited_dist
pipeline/predict.py             + players argument, _internal.credited_dist / players_log
pipeline/run.py                 + calibration, players stage, player log append
pipeline/export.py              + calibration argument
pipeline/backtest.py            + run_backtest(keep_goal_dist=...)
pipeline/player_backtest.py NEW backtest, tuning, gate, report (CLI)
pipeline/schema/latest.schema.json   players block, calibration
tools/review.py                 + check_calibration, check_players
site/format.js, site/app.js, site/styles.css, site/tests/format.test.js
README.md, spec section 17
tests/test_players.py (NEW), test_player_backtest.py (NEW), test_tracking.py, test_metrics.py (NEW if absent),
tests/test_simulate.py, test_features.py, test_predict.py, test_export.py, test_run.py, test_review.py, test_backtest.py
Produced by the controller: model/player_params.json, reports/player_backtest.md
```

---

### Task 1: Draw-calibration monitor  — model: Haiku

Spec: 6.9 item 4, 8 (tracking), 9 view 5, 10; decision 26.

**Files:**
- Modify: `pipeline/config.py`, `pipeline/tracking.py`, `pipeline/export.py`, `pipeline/run.py`, `pipeline/schema/latest.schema.json`, `tools/review.py`, `site/format.js`, `site/app.js`
- Test: `tests/test_tracking.py`, `tests/test_export.py`, `tests/test_run.py`, `tests/test_review.py`, `site/tests/format.test.js`

**Interfaces:**
- Consumes: `tracking.official(rows)`, `tracking.read_log`, prediction rows (`p_reg`, `model_version`, `start_utc`), result rows (`reg_home_goals`, `reg_away_goals`), `run._result_record`, review `_history(ctx, kind, season)`.
- Produces:
  - config: `DRAW_MONITOR_GAP = 0.02`, `DRAW_MONITOR_MIN_GAMES = 100`, `DRAW_MONITOR_WINDOW = 300`.
  - `tracking.draw_gap(pred_rows: list[dict], result_rows: list[dict], season: str) -> dict | None` — pure; result rows are de-duplicated by `game_id` (the run passes logged plus in-memory rows). Official rows (`official(pred_rows)`) with `model_version == config.MODEL_VERSION` and a result, sorted by `start_utc`, last `DRAW_MONITOR_WINDOW`; returns `DrawGap` (Shared Contracts) or None when none.
  - `export.build_latest(games, sources, next_game_date, now, calibration: dict | None = None)` — writes `"calibration": calibration or {"draw": None}`.
  - `run`: before `build_latest`, `calibration = {"draw": tracking.draw_gap(read_log("predictions", season), read_log("results", season) + [this run's _result_record rows], season)}`; wrapped so an exception gives `{"draw": None}`.
  - schema: top-level optional property `calibration` = object `{draw: null | DrawGap}` (all DrawGap fields required, `warn` boolean, `gap` number, probabilities in `$defs.prob`).
  - `review.check_calibration(ctx) -> list[Problem]` (appended to `CHECKS` after `check_track`): current season = newest `data/history/predictions/*.jsonl` stem; `DrawGap` from `tracking.draw_gap` over `_history` rows; one VAROITUS group `CALIB`, where `data/history/predictions/{season}.jsonl`, message `tasapelien ero malli − toteutunut {gap*100:+.1f} %-yks. ({n} ottelua, malli {model:.1%}, toteutunut {actual:.1%})`, hint `seuraa; ei uudelleensovitusta vaiheessa 3 — draw_kappa sovitetaan: python -m pipeline.backtest --refit-draw`.
  - `format.js`: `fmtSeason("20262027") -> "2026–27"` (en dash); `drawGapLine(d) -> "Malli 24,9 % · toteutunut 22,3 % · ero +2,6 %-yks."` (uses `fmtPct1`, `fmtPp`).
  - Status view: after the sources list a stat tile with key `Tasapelit, kausi {fmtSeason} ({n} ottelua)` and value `drawGapLine`, plus `badge("Poikkeama yli 2 %-yks.", "warn")` when `warn`; when `calibration?.draw` is null: key `Tasapelit` value `Ei vielä riittävästi ratkenneita ennusteita`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tracking.py
def test_draw_gap_counts_official_current_version_only():
    # 3 games: two 1.1.0 rows (official = last before start), one 1.0.0 row -> n == 2
    d = tracking.draw_gap(preds, results, "20262027")
    assert d["n"] == 2 and d["model"] == pytest.approx(0.25) and d["actual"] == pytest.approx(0.5)
    assert d["gap"] == pytest.approx(-0.25) and d["warn"] is False   # n < 100
def test_draw_gap_window_keeps_latest_300():          # 350 games -> n == 300, the earliest 50 dropped
def test_draw_gap_warn_threshold():                   # n 120, gap 0.021 -> warn; gap 0.019 -> not
def test_draw_gap_none_without_matches():             assert tracking.draw_gap([], [], "20262027") is None
# tests/test_export.py
def test_calibration_block_validates_and_defaults():  # build_latest(...) without arg -> {"draw": None}; with DrawGap -> valid
# tests/test_run.py
def test_latest_has_calibration(seeded):              assert "calibration" in latest_of(seeded)
# tests/test_review.py
def test_draw_gap_warning(root):    # 120 official 1.1.0 rows, model .25, actual .22 -> exit 1, "[VAROITUS] CALIB" and "+3.0 %-yks." in report
def test_no_draw_warning_under_100_games(root):
def test_no_draw_warning_small_gap(root):
```

```js
test("fmtSeason", () => assert.equal(fmtSeason("20262027"), "2026–27"));
test("drawGapLine", () => assert.equal(
  drawGapLine({ model: 0.249, actual: 0.223, gap: 0.026 }), "Malli 24,9 % · toteutunut 22,3 % · ero +2,6 %-yks."));
```

- [ ] **Step 2: Run** `python -m pytest tests/test_tracking.py tests/test_export.py tests/test_run.py tests/test_review.py` and `node --test site/tests/format.test.js` — expected FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `python -m pytest`, `python -m ruff check .`, `node --test site/tests/format.test.js`, `node --check site/app.js` — expected pass.
- [ ] **Step 5 (controller, real data):** `python tools/review.py --data-only` — report the CALIB line (or its absence) and the `n`/gap to the user. Do not change κ.
- [ ] **Step 6: Commit:** `feat: regulation draw calibration monitor in review and status page`

---

### Task 2: Player table, rates and baseline  — model: Sonnet

Spec: 6.10, 6.2 (weights), 15.1 (leak-free); decisions 19, 27.

**Files:**
- Create: `pipeline/players.py`, `tests/test_players.py`
- Modify: `pipeline/config.py`, `tests/synth.py` (add `make_records`)

**Interfaces:**
- Consumes: `xg.shots_frame`, `xg.score`, `ratings.weights(tg, asof, p, season)`, `ModelParams`, `config.TRAIN_GAME_TYPES`.
- Produces:
  - config: `PlayerParams`, `load_player_params()`, `load_player_gate()`, `PLAYER_PPG_FALLBACK = 2.7`, `PLAYER_PPG_MAX = 3.0`, `PLAYER_MODEL_VERSION = "1.0.0"`, `PLAYER_LOG_COLUMNS` (Shared Contracts).
  - `players.PG_COLUMNS` and `player_games(records: list[dict], coefs: dict) -> pd.DataFrame` (Shared Contracts).
  - `@dataclass PlayerRates` (Shared Contracts).
  - `player_rates(pg: pd.DataFrame, asof: date, p: ModelParams, pp: PlayerParams, season: str | None = None) -> PlayerRates` — `w = ratings.weights(pg, asof, replace(p, half_life_days=pp.half_life_days), season)`, rows of `TRAIN_GAME_TYPES` only. `ĝ = pp.xg_blend·ixg + (1 − pp.xg_blend)·g`; `p̂ = ĝ + a`; hours `h = toi/3600`. Position average `pos_g60[grp] = Σw·ĝ / Σw·h` (likewise points). Player rate `g60 = (Σw·ĝ + K·pos_g60) / (Σw·h + K)` with `K = pp.prior_toi_h`; same for `p60`. `ppg = min(PLAYER_PPG_MAX, Σw·pts / Σw·g)`, `PLAYER_PPG_FALLBACK` when `Σw·g == 0`. Empty input → position rates from `PLAYER_PRIOR_FALLBACK = {"F": (0.85, 2.1), "D": (0.25, 1.1)}` (g60, p60) and the ppg fallback.
  - `baseline_probs(pg: pd.DataFrame, asof: date, season: str, slots: list[Slot]) -> dict[int, tuple[float, float]]` — the spec 8 baseline: current-season rows before `asof`, **unweighted, unshrunk**: `rate = Σ / Σh` per player (position average of the same rows when the player has none, `PLAYER_PRIOR_FALLBACK` when there are no rows), times the player's season-average TOI (else `slot.toi_proj`) → `μ`; returns `(1 − e^{−μ_pts}, 1 − e^{−μ_g})`.

`tests/synth.py::make_records(games: list[tuple[str, str, str]], skaters: dict[str, list[tuple[int, str]]], seed: int = 0) -> list[dict]` — builds `CachedGame` dicts (date, home, away) with 18 skaters per team from `skaters[team] = [(id, pos), ...]`, deterministic g/a/toi from `seed`, and one non-empty-net shot per goal with `shooter`, `a1`, `a2` set; scores consistent with the skater goals; `decided = "REG"`.

- [ ] **Step 1: Write failing tests** (`tests/test_players.py`; `stub_score` fixture returns xG 0.1 per shot; `unit_weights` fixture monkeypatches `ratings.weights` to ones for rows before `asof`):

```python
def test_player_games_columns_points_and_team_goals(stub_score):
    pg = players.player_games(make_records(...), COEFS)
    assert list(pg.columns) == players.PG_COLUMNS
    assert (pg.pts == pg.g + pg.a).all() and set(pg.grp) <= {"F", "D"}
    one = pg[(pg.game_id == GID) & (pg.team == "AAA")]; assert (one.team_goals == one.g.sum()).all()
def test_ixg_counts_shots_and_empty_net_goals(stub_score):   # 3 shots (0.1 each) + 1 EN goal -> ixg 1.3
def test_rate_formula_with_unit_weights(stub_score, unit_weights):
    # one F player, 2 games, 40 min total, g=2, ixg=0.6, xg_blend .5 -> ĝ = 1.3; K = 5; pos_g60 known
    r = players.player_rates(pg, ASOF, P, PlayerParams(prior_toi_h=5.0))
    assert r.g60[PID] == pytest.approx((1.3 + 5 * r.pos_g60["F"]) / (40 / 60 + 5))
def test_unknown_player_gets_position_prior():               # Review Focus 3
    assert r.rate(999, "D") == (r.pos_g60["D"], r.pos_p60["D"]) and r.rate(999, "C")[0] == r.pos_g60["F"]
def test_traded_player_keeps_rates():                         # Review Focus 2: same games, team relabelled -> identical g60/p60
def test_previous_season_rows_count_with_lower_weight():      # real weights: prev-season-only player differs from prior
def test_rows_on_or_after_asof_are_ignored():
def test_ppg_from_data_capped_and_fallback():                 # pts/g = 3.4 -> 3.0; no goals -> 2.7
def test_baseline_is_unweighted_current_season():             # prev-season rows do not change baseline; values = 1 - exp(-mu)
def test_baseline_player_without_rows_uses_position_average():
def test_load_player_params_overlay_and_unknown_key(root):    # file {"prior_toi_h": 10, "gate": {...}} -> 10; {"x": 1} -> ValueError
```

- [ ] **Step 2: Run** `python -m pytest tests/test_players.py` — expected FAIL.
- [ ] **Step 3: Implement** the Interfaces above (`grp`: `C|L|R → F`, `D → D`).
- [ ] **Step 4: Run** `python -m pytest`, `python -m ruff check .` — expected pass.
- [ ] **Step 5: Commit:** `feat: player-game table, shrunk per-60 rates and season baseline`

---

### Task 3: Projected lineup and TOI  — model: Haiku

Spec: 6.6 last paragraph, 6.10 (TOI); decision 20.

**Files:**
- Modify: `pipeline/players.py`, `pipeline/config.py`, `tests/test_players.py`

**Interfaces:**
- Consumes: `player_games` frame (Task 2), `Slot`.
- Produces:
  - config: `PLAYER_TOI_GAMES = 5`.
  - `project_lineup(pg: pd.DataFrame, team: str, asof: date, season: str) -> list[Slot]` — rows with `date < asof` only. Team's last game = its latest `game_id` by `(date, game_id)` in `season`, else in any earlier season. Lineup = that game's skaters, minus players whose latest row (any team) is for another team and dated after that game. `toi_proj = round(mean(toi of the player's last PLAYER_TOI_GAMES rows) / 60, 1)`. `name`/`pos` from the player's latest row. Sorted by `toi_proj` desc, then id. Empty list when the team has no rows.

- [ ] **Step 1: Write failing tests:**

```python
def test_lineup_is_last_game_dressed_sorted_by_toi():
    slots = players.project_lineup(pg, "AAA", ASOF, SEASON)
    assert len(slots) == 18 and [s["toi_proj"] for s in slots] == sorted((s["toi_proj"] for s in slots), reverse=True)
    assert set(slots[0]) == {"id", "name", "pos", "toi_proj"}
def test_absent_from_last_game_excluded():           # played games 1-9, not game 10 -> not in lineup
def test_toi_proj_mean_of_last_five_any_team():      # 6 games, TOI 10..15 min, the last two for BBB -> 13.0
def test_traded_away_player_dropped():               # Review Focus 2: in AAA's last game, later dressed for BBB -> only in BBB lineup
def test_team_without_games_returns_empty():         # Review Focus 1
def test_previous_season_used_when_no_current_games():
def test_only_rows_before_asof():                    # a game dated asof does not count
```

- [ ] **Step 2: Run** `python -m pytest tests/test_players.py` — expected FAIL (the seven new tests). **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.
- [ ] **Step 5: Commit:** `feat: projected lineup and TOI from the latest cached games`

---

### Task 4: Credited goals and player probabilities  — model: Sonnet

Spec: 6.9 item 6 ("joukkueen maalimäärän jakauma pelaajamallia varten"), 6.10, 11 (binomial tests); decisions 21–23.

**Files:**
- Modify: `pipeline/simulate.py`, `pipeline/features.py`, `pipeline/config.py`, `pipeline/players.py`
- Test: `tests/test_simulate.py`, `tests/test_features.py`, `tests/test_players.py`

**Interfaces:**
- Consumes: `PlayerRates`, `Slot`, `PlayerParams`, `PlayerInputs` fields (Shared Contracts), `simulate`'s internal `reg_h`, `reg_a`, `tied`, `home_ot`, `w`.
- Produces:
  - config: `OT_GOAL_SHARE_FALLBACK = 0.6`, `PLAYER_TOP_N = 10`, `PLAYER_PI_MAX = 0.95`.
  - `features.sim_params(tg)` adds `"ot_goal_share"`: among home rows with `decided in ("OT", "SO")`, share with `decided == "OT"`; `OT_GOAL_SHARE_FALLBACK` below `EN_PROB_MIN_GAMES` such games.
  - `simulate(...)` adds `"credited_dist"` (Shared Contracts). With `s = sp.get("ot_goal_share", OT_GOAL_SHARE_FALLBACK)`, `ot_h = tied & home_ot`, `ot_a = tied & ~home_ot`: home = `bincount(min(reg_h, 10), w·(1 − s·ot_h)) + bincount(min(reg_h + 1, 10), w·s·ot_h)`, divided by `w.sum()`; away likewise with `ot_a`. No new random draws.
  - `players.binom_tails(dist: list[float], q: float) -> list[float]` — `[P(X≥1), P(X≥2), P(X≥3)]`, `X | G ~ Binomial(G, q)`, G = bucket index (the `10+` bucket counts as 10), via `math.comb`.
  - `players.player_probs(slots: list[Slot], rates: PlayerRates, dist: list[float], pp: PlayerParams) -> list[dict]` — `e_g = g60·toi_proj/60`, `e_p = p60·toi_proj/60`; `q_i = (1 − u)·e_g,i/Σe_g`; `π_i = min(PLAYER_PI_MAX, max(q_i, (1 − u)·ppg·e_p,i/Σe_p))`; `mean = Σ G·P(G)`; returns per slot `{"id", "name", "pos", "toi_proj", "q", "pi", "p_goals": binom_tails(dist, q), "p_points": binom_tails(dist, π), "exp_goals": q·mean, "exp_points": π·mean}` sorted by `p_points[0]` desc, then id. Empty slots → `[]`.
  - `players.team_block(team: str, probs: list[dict], dist: list[float], pp: PlayerParams) -> dict | None` — TeamPlayers (Shared Contracts; `top` rule; rounding; `odds: None`; `team` on each player); None for empty `probs`.
  - `players.game_players(home: str, away: str, pin: PlayerInputs, credited: dict[str, list[float]]) -> tuple[dict | None, dict | None]` → `(game.players block, log payload {"teams": {abbr: {"goals_mean", "unassigned", "players": [rows in PLAYER_LOG_COLUMNS order with base_pts1/base_g1 from pin.baseline]}}})`; `(None, None)` when both team blocks are None.

- [ ] **Step 1: Write failing tests:**

```python
# tests/test_simulate.py
def test_credited_dist_sums_to_one_and_adds_ot_goal():
    out = simulate(LH, LA, dict(SP, ot_goal_share=0.6), P, seed=1)
    for side in ("home", "away"): assert sum(out["credited_dist"][side]) == pytest.approx(1, abs=1e-4)
    mean = lambda d: sum(i * x for i, x in enumerate(d))
    p_ot_home = out["prob"]["home_win"] - out["prob"]["reg_home"]
    assert mean(out["credited_dist"]["home"]) == pytest.approx(mean(out["goal_dist"]["home"]) + 0.6 * p_ot_home, abs=2e-3)
def test_credited_dist_leaves_other_outputs_unchanged():   # prob/score/periods identical with ot_goal_share 0 vs 0.6
# tests/test_features.py
def test_ot_goal_share_estimated_and_fallback():          # 60 OT + 40 SO -> 0.6 exact from data; 10 games -> fallback
# tests/test_players.py
def test_binom_tails_known_numbers():
    assert players.binom_tails([0, 0, 1] + [0] * 8, 0.5) == pytest.approx([0.75, 0.25, 0.0])
def test_binom_tails_is_poisson_thinning():               # dist = Poisson(3) pmf with 10+ tail; q .1 -> P>=1 ≈ 1 - exp(-0.3) (1e-3)
def test_shares_sum_to_team_expectation():
    probs = players.player_probs(slots, rates, DIST, PlayerParams(unassigned=0.03))
    mean = sum(i * x for i, x in enumerate(DIST))
    assert sum(x["exp_goals"] for x in probs) == pytest.approx(0.97 * mean, rel=1e-6)
    assert sum(x["exp_points"] for x in probs) <= 3 * mean
def test_tails_monotone_and_goals_below_points():         # Review Focus 4: dist all in 10+, sniper with a=0
    for x in probs: assert x["p_points"][0] >= x["p_points"][1] >= x["p_points"][2] and all(g <= p for g, p in zip(x["p_goals"], x["p_points"]))
def test_pi_capped_and_at_least_q():                      # one player with huge p60 -> pi == 0.95; sniper pi == q
def test_debutant_probabilities_finite():                 # Review Focus 3: slot with no rates -> 0 < p_points[0] < top player's
def test_team_block_top_is_union_and_rounded():           # 18 slots, a D with high points and an F with high goals both kept
    b = players.team_block("AAA", probs, DIST, PP)
    assert 10 <= len(b["top"]) <= 20 and b["dressed"] == 18 and all(p["odds"] is None for p in b["top"])
def test_game_players_one_team_missing():                 # Review Focus 1: away lineup [] -> block["away"] is None, home filled
def test_game_players_none_when_both_missing():           assert players.game_players(...) == (None, None)
def test_log_payload_columns():                           # every row has len(PLAYER_LOG_COLUMNS) and base values from pin.baseline
```

- [ ] **Step 2: Run** `python -m pytest tests/test_simulate.py tests/test_features.py tests/test_players.py` — expected FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `python -m pytest`, `python -m ruff check .` — expected pass.
- [ ] **Step 5: Commit:** `feat: credited goal distribution and binomial player probabilities`

---

### Task 5: Pipeline integration, schema and player log  — model: Sonnet

Spec: 4.1 step 6, 4.5, 8 (storage); decisions 23, 24, 28.

**Files:**
- Modify: `pipeline/predict.py`, `pipeline/run.py`, `pipeline/tracking.py`, `pipeline/metrics.py`, `pipeline/schema/latest.schema.json`
- Test: `tests/test_predict.py`, `tests/test_run.py`, `tests/test_tracking.py`, `tests/test_export.py`, `tests/test_metrics.py`

**Interfaces:**
- Consumes: Tasks 2–4 (`player_games`, `player_rates`, `baseline_probs`, `project_lineup`, `game_players`, `PlayerInputs`), `config.load_player_params/load_player_gate`.
- Produces:
  - `predict_game(..., starters, pin: PlayerInputs | None = None)` — after `simulate`: `_internal["credited_dist"] = sim["credited_dist"]`; when `pin` is given, `players, payload = players.game_players(home, away, pin, sim["credited_dist"])` (an exception → log warning, both None); `game["players"] = players`, `_internal["players_log"] = payload`. `"players"` leaves `NULL_KEYS`' null-fill only when computed.
  - `run.players_enabled(offline: Path | None) -> bool` — offline True; online `(config.load_player_gate() or {}).get("passed") is True`.
  - `run._predict_all(..., with_players: bool)` — when enabled: `pp = load_player_params()`, `pg = players.player_games(records, coefs)`, `rates = player_rates(pg, today, p, pp, season)`; per upcoming game `lineups = {t: project_lineup(pg, t, day, season)}` for both teams, `baseline = baseline_probs(pg, day, season, home_slots + away_slots)`, `pin = PlayerInputs(...)`. A failure building `pg`/`rates` logs a warning and disables players for the run.
  - `tracking.player_log_row(game_obj: dict, ts: str) -> dict | None` (Shared Contracts; None without `_internal.players_log`), `append_players(game_objs, ts) -> int` (upcoming games only), `read_log` accepts `"players"`.
  - `tracking.player_outcomes(rows: list[dict], records: list[dict]) -> pd.DataFrame` — official player row per game (`official()` on `ts`/`start_utc`) joined with the cached record's skaters: one row per projected player who dressed, columns `game_id, player, team, p_pts1, p_g1, base_pts1, base_g1, y_pts (0/1: pts ≥ 1), y_g (0/1)`.
  - `metrics.calibration_bins(p, y) -> list[dict]` (extracted from `score`, same output) and `metrics.binary_vs_base(p, y, base: np.ndarray | None) -> {"n", "brier", "logloss", "brier_base", "logloss_base", "calibration"}` with per-row `base`; `*_base` are None when `base` is None.
  - Schema: `$defs.player` (fields of Shared Contracts; `pos` enum `C|L|R|D`; `p_points`, `p_goals` arrays of exactly 3 `$defs.prob`; `odds` const null; `name` string or null), `$defs.team_players`, `$defs.players` (`lineup` enum `projected|confirmed`; `home`/`away` team_players or null); game property `players` = `anyOf [null, $defs.players]`.
  - `run`: after validation, `tracking.append_players(games, _iso(now))` next to `append_predictions`.

- [ ] **Step 1: Write failing tests:**

```python
# tests/test_predict.py
def test_predict_game_with_players_fills_block():     # synthetic pin -> players["home"]["top"] non-empty, _internal has credited_dist
def test_predict_game_without_pin_keeps_null():
def test_player_failure_gives_null(monkeypatch):      # game_players raises -> players None, prediction still returned
# tests/test_export.py
def test_players_block_validates():
def test_players_bad_lengths_rejected():              # p_points of length 2 -> error path mentions "p_points"
# tests/test_tracking.py
def test_player_log_roundtrip(root):                  # append_players -> read_log("players", season) rows with PLAYER_LOG_COLUMNS arity
def test_started_game_writes_no_player_row(root):
def test_player_outcomes_official_and_dressed_only(): # 2 runs (last before start used); projected-but-scratched player absent
# tests/test_metrics.py
def test_binary_vs_base_known_numbers():              # p [.6,.2], y [1,0], base [.5,.5] -> brier .1 vs .25
def test_score_calibration_unchanged():               # score(...)["calibration"] == calibration_bins(...)
# tests/test_run.py
def test_offline_run_fills_players_where_history_exists(seeded):   # Review Focus 1
    lat = latest_of(seeded); g = by_id(lat)[2026020338]          # TOR @ CHI; TOR has a cached fixture game
    assert g["players"]["away"]["team"] == "TOR" and len(g["players"]["away"]["top"]) >= 5
    assert g["players"]["home"] is None and export.validate(lat) == []
    assert by_id(lat)[2026020333]["players"] is None             # LAK @ BOS: no history
    assert tracking.read_log("players", SEASON)
def test_online_without_player_gate_has_no_players(root, monkeypatch):   # use existing online_with_gate helper, no player_params.json
def test_started_game_keeps_frozen_players(seeded):
```

- [ ] **Step 2: Run** `python -m pytest tests/test_predict.py tests/test_export.py tests/test_tracking.py tests/test_metrics.py tests/test_run.py` — expected FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `python -m pytest`, `python -m ruff check .` — expected pass. Run by hand `python -m pipeline.run --offline tests/fixtures` — expected exit 0 and `players` on 2026020338 and 2026020343 (CGY @ SJS); then delete the generated `data/` and `site/data/` output.
- [ ] **Step 5: Commit:** `feat: player probabilities in latest.json and the player prediction log`

---

### Task 6: Player backtest, tuning and gate  — model: Sonnet (steps 5–7 by the controller on real data)

Spec: 8 (backtest, player Brier vs season baseline), 15.1, 15.3; decisions 22, 24.

**Files:**
- Create: `pipeline/player_backtest.py`, `tests/test_player_backtest.py`
- Modify: `pipeline/backtest.py`, `pipeline/config.py`, `tests/test_backtest.py`
- Produced by running it: `model/player_params.json` (only on pass), `reports/player_backtest.md`

**Interfaces:**
- Consumes: `run_backtest`, `load_team_games`, `load_goalie_games`, `BT_COEFS`, `config.load_params`, `config.load_gate()["goalie_mode"]`, Tasks 2–4 functions, `metrics.binary_vs_base`.
- Produces:
  - `backtest.run_backtest(..., keep_goal_dist: bool = False)` — when true, rows also carry `home`, `away`, `gd_home`, `gd_away` (`sim["credited_dist"]` lists); default output unchanged.
  - config: `PLAYER_PRIOR_GRID = (2.5, 5.0, 10.0)`, `PLAYER_HALF_LIFE_GRID = (40.0, 80.0)`, `PLAYER_UNASSIGNED_RANGE = (0.0, 0.10)`.
  - `PB_COLUMNS = ["game_id", "date", "team", "player", "pos", "p_pts1", "p_pts2", "p_pts3", "p_g1", "p_g2", "p_g3", "b_pts1", "b_g1", "y_pts", "y_g"]` (`y_*` = actual counts).
  - `player_backtest(pg: pd.DataFrame, team_rows: pd.DataFrame, season: str, p: ModelParams, pp: PlayerParams) -> dict` → `{"rows": DataFrame[PB_COLUMNS], "coverage": float, "unassigned": float, "metrics": {"points": m, "goals": m, "points2": m}}`. Per date `day` of the team rows: `hist = pg[pg.date < day]`, rates/lineup/baseline from `hist` exactly as production; per game and team `player_probs(slots, rates, gd_side, pp)`; rows only for projected players who dressed (`player_games` row in that game). `coverage` = projected∩dressed / dressed; `unassigned` = Σ goals of dressed-not-projected / Σ team goals. `m = metrics.binary_vs_base` on `p_pts1` vs `y_pts ≥ 1` with base `b_pts1` (`goals`: `p_g1`, `b_g1`; `points2`: `p_pts2` vs `y_pts ≥ 2`, base `None` → report model only).
  - `tune_players(pg, team_rows, season, p, base: PlayerParams) -> PlayerParams` — grid `PLAYER_HALF_LIFE_GRID × PLAYER_PRIOR_GRID`, objective mean of points and goals log loss; then `unassigned = clamp(round(measured / 0.005) * 0.005, *PLAYER_UNASSIGNED_RANGE)` from the best run.
  - `player_gate(metrics: dict) -> bool` — for both `points` and `goals`: `brier < brier_base and logloss < logloss_base`.
  - CLI `python -m pipeline.player_backtest --tune-season 20242025 --gate-season 20252026 [--sims 2000]`: load seasons {tune, gate, and the season before each} with `BT_COEFS`; `p = config.load_params()`, mode = `config.load_gate()["goalie_mode"]`; one `run_backtest(..., keep_goal_dist=True)` per tune and gate season (the goal distributions are reused across the grid); tune on tune season; evaluate on gate season. Always writes `reports/player_backtest.md`: params; table `Mittari | Malli | Perustaso` for P(≥1 piste), P(≥1 maali) (Brier, log loss, n) and P(≥2 pistettä) (model only); coverage and unassigned; calibration tables (5-pp bins: `lo–hi, n, malli, toteutunut`) for P(≥1 piste) and P(≥1 maali); verdict line. On pass writes `model/player_params.json` = `{**asdict(pp), "gate": {"passed": true, "season", "tune_season", "goalie_mode", "points": m, "goals": m, "coverage", "unassigned"}}` (calibration lists omitted from the file). Exit 0 pass / 1 fail; on fail `model/player_params.json` is untouched.

- [ ] **Step 1: Write failing tests** (synthetic league from `make_records`, `stub_score`; `run_backtest` stubbed in CLI tests to return rows with fixed `gd_*`):

```python
def test_run_backtest_keep_goal_dist_columns():        # tests/test_backtest.py: extra columns only when asked; default columns unchanged
def test_player_backtest_is_leak_free(monkeypatch):    # spy on player_rates/project_lineup: every hist.date.max() < day
def test_player_backtest_rows_only_dressed_projected():
def test_coverage_and_unassigned_known_numbers():      # one non-projected scorer of 1 of 4 goals -> unassigned .25
def test_metrics_compare_against_baseline():           # metrics["points"] has brier and brier_base, n == len(rows)
def test_tune_players_picks_lowest_logloss(monkeypatch):   # stub scoring -> picks (80.0, 2.5); unassigned rounded to .005 and clamped
def test_player_gate_requires_both_markets():
def test_cli_writes_params_and_report_on_pass(root, monkeypatch):
    assert player_backtest.main(ARGS) == 0
    pp = json.loads((root / "model/player_params.json").read_text())
    assert pp["gate"]["passed"] is True and "calibration" not in pp["gate"]["points"]
    assert "P(≥1 piste)" in (root / "reports/player_backtest.md").read_text(encoding="utf-8")
def test_cli_fail_leaves_params_untouched(root, monkeypatch):   # exit 1, file absent/byte-identical, report written
```

- [ ] **Step 2: Run** `python -m pytest tests/test_player_backtest.py tests/test_backtest.py` — expected FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** `python -m pytest`, `python -m ruff check .` — expected pass. **Commit:** `feat: leak-free player backtest, tuning and gate`
- [ ] **Step 5 (controller, real data, background, ~2–3 h):** `python -m pipeline.player_backtest --tune-season 20242025 --gate-season 20252026`.
- [ ] **Step 6: Check `reports/player_backtest.md`.** Expected: exit 0; model Brier and log loss below baseline for P(≥1 piste) and P(≥1 maali); calibration bins within about ±3 pp where n ≥ 200; coverage ≥ 0.85; unassigned 0.02–0.07; P(≥1 piste) mean ≈ actual rate (≈ 0.35–0.45 over dressed skaters). If the gate fails, stop and report the table to the user — do not widen grids or change seasons on your own (spec 15.4: one retry with the planner).
- [ ] **Step 7: Commit:** `git add model/player_params.json reports/player_backtest.md && git commit -m "model: player gate on 2025-26"`

---

### Task 7: Review checks for players  — model: Haiku

Spec: 10 (PROB player rows, ROSTER); decision 23.

**Files:**
- Modify: `tools/review.py`, `tests/test_review.py`

**Interfaces:**
- Consumes: `latest.games[].players` (Shared Contracts), `model/player_params.json` under `ctx.root`, config `PLAYER_*`.
- Produces: `check_players(ctx) -> list[Problem]`, appended to `CHECKS` after `check_roster`; returns `[]` when the schema check fails (like `check_prob`). Per upcoming game and team block:
  - VIRHE `PROB` `{game}` — `{name}: P(≥k) ei laskeva ({values})` when `p_points` or `p_goals` is not non-increasing; hint `tarkista pipeline/players.py binom_tails`.
  - VIRHE `PROB` — `{name}: maalit {g} > pisteet {p}` when any `p_goals[k] > p_points[k]`; hint `tarkista π ≥ q pipeline/players.py`.
  - VIRHE `PROB` — `{team}: pelaajien maaliodotus {exp_goals:.2f} vs joukkue {goals_mean:.2f}` when `|exp_goals/goals_mean − (1 − unassigned)| > PLAYER_SUM_TOL`; hint `tarkista normalisointi pipeline/players.py`.
  - VIRHE `PROB` — `{team}: pisteodotus {exp_points:.2f} yli 3 × maalit` when `exp_points > PLAYER_PPG_MAX · goals_mean`.
  - VIRHE `ROSTER` — `{team}: pelaaja {id} ei kuulu joukkueeseen` when a `top[].team` differs from the block `team` or the block `team` is not the game's side; also a player id present in both teams.
  - VIRHE `ROSTER` — `{team}: listalla {n} pelaajaa (< 5)` when `len(top) < PLAYER_MIN_TOP`.
  - VAROITUS `ROSTER` — `{team}: kokoonpanossa {dressed} kenttäpelaajaa` when `dressed != PLAYER_DRESSED`; hint `arvio viimeisimmästä ottelusta`.
  - VAROITUS `ROSTER` `{game}` — `ei pelaajaennustetta` when `players` is null for an upcoming game while the player gate passed; hint `joukkueella ei välimuistissa otteluita tai pelaajavaihe epäonnistui (katso ajoloki)`.

- [ ] **Step 1: Write failing tests** (`root` seeded with a valid latest containing a players block):

```python
def test_clean_players_ok(root):                assert review.main(ARGS) == 0
def test_non_monotone_tail_is_error(root):      # p_points [.3,.4,.1] -> exit 2, "[VIRHE] PROB"
def test_goals_above_points_is_error(root):
def test_sum_mismatch_is_error(root):           # exp_goals 2.5 vs goals_mean 3.2, unassigned .03 -> error
def test_foreign_player_is_error(root):         # top[0].team "XXX" -> "[VIRHE] ROSTER"
def test_short_top_list_is_error(root):
def test_dressed_17_is_warning(root):
def test_missing_players_warns_only_with_gate(root):   # no player_params.json -> no problem; gate passed -> warning
```

- [ ] **Step 2: Run** `python -m pytest tests/test_review.py` — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest`, then `python tools/review.py --skip-tests` — expected pass / only real findings.
- [ ] **Step 5: Commit:** `feat: review checks for player probabilities and lineups`

---

### Task 8: Frontend — Pelaajat section and tab  — model: Sonnet

Spec: 9 (view 2 section 6, list view's "Illan kuumimmat pelaajat"), 1.1 item 3; decisions 20, 23.

**Files:**
- Modify: `site/format.js`, `site/app.js`, `site/styles.css`, `site/tests/format.test.js`

**Interfaces:**
- Consumes: `game.players` (Shared Contracts); produce a local file with `python -m pipeline.run --offline tests/fixtures`.
- Produces (`site/format.js`, pure):
  - `playerName(p) -> p.name || "#" + p.id`
  - `fmtToi(min) -> "19,4 min"`
  - `pKey(kind) -> "p_points" | "p_goals"` (`kind` ∈ `"points" | "goals"`)
  - `topOf(list, kind, n = 5)` — sorted by `p[kind][0]` desc, then `playerName`; first `n`
  - `gamePlayers(game, scope)` — `scope` ∈ `"game" | "away" | "home"`: the team's `top`, or both teams' `top` concatenated for `"game"`; `[]` when missing
  - `tonightPlayers(latest, kind, n = 10) -> [{game, player}]` — upcoming games of the earliest `nhl_date` among upcoming games (as `groupByDate`), all `top` entries, `topOf` across them

Structure and copy (`app.js`; reuse `card`, `badge`, `sec`, `note`, `seg`, `seg-btn`, `toggle` classes and existing tokens; no new colour tokens):
- `ICON_PERSON` inline SVG (head circle + shoulders path, same stroke style as `ICON_BALL`); `TABS` gains `{id: "players", label: "Pelaajat", href: "#/pelaajat", icon: ICON_PERSON}` between Ottelut and Ylikertoimet; route `#/pelaajat` → `playersView`; active tab mapping extended.
- `SECTIONS` gains `{id: "players", title: "Pelaajat", render: playersSection}` after `periods`. `playersSection(g)` returns `null` when `g.players` is null. Module state `playerFilter = {kind: "points", scope: "game"}`. Controls: segmented `Pisteet · Maalit` (`data-pkind`) and segmented `Ottelu · {away abbr} · {home abbr}` (`data-pscope`), `aria-pressed`, tap targets ≥ `--tap`. Under them `badge("Kokoonpano: arvio", "muted")`. Table (in an `overflow-x: auto` wrapper) with header `Pelaaja · 1+ · 2+ · 3+`; each row: name (bold) and below it small `{team} · {pos} · {fmtToi(toi_proj)}`; three cells `fmtPct(p[0..2])`; top 5 via `topOf`; the rest of the scope list inside `<details><summary>Näytä kaikki ({n})</summary>`. A scope whose team block is null: `Ei pelaajadataa: joukkueella ei ole vielä pelattuja otteluita tällä kaudella.` Foot note: `Kokoonpano ja peliajat on arvioitu joukkueen viimeisimmästä ottelusta. Luvut ovat todennäköisyyksiä saada ottelussa vähintään 1, 2 tai 3 pistettä (tai maalia), jatkoaika mukaan lukien.`
- `playersView(latest)`: heading `Pelaajat`, line `Ensi yön ottelut · {fmtDate(nhl_date)}`; two cards: `Todennäköisimmät pistemiehet` (sub `vähintään 1 piste`) and `Todennäköisimmät maalintekijät` (sub `vähintään 1 maali`), each an ordered list of `tonightPlayers(latest, kind, 10)`: `{rank}. {playerName}` with small `{team} · {away} @ {home} · {fmtTime(start_utc)}`, right-aligned `fmtPct(p[0])`, each row a link to `#/ottelu/{id}`. Empty: `Pelaajaennusteita ei ole vielä saatavilla.` Foot note: `Kokoonpanot ovat arvioita. Pelaajavetojen kertoimia ei vertailla — ilmainen kerroinlähde ei tarjoa niitä.`
- Click handling for `data-pkind`/`data-pscope` re-renders like the existing value-view controls.

- [ ] **Step 1: Write failing tests** in `site/tests/format.test.js`:

```js
test("playerName fallback", () => { assert.equal(playerName({ id: 7, name: null }), "#7"); assert.equal(playerName({ id: 7, name: "A <b>" }), "A <b>"); });
test("fmtToi", () => assert.equal(fmtToi(19.4), "19,4 min"));
test("topOf sorts by kind and cuts", ...);          // goals order differs from points order; n respected
test("gamePlayers scopes", ...);                    // "game" concatenates; null away block -> []
test("tonightPlayers uses earliest date and upcoming only", ...);   // started game and next-day game excluded
```

- [ ] **Step 2: Run** `node --test site/tests/format.test.js` — expected FAIL.
- [ ] **Step 3: Implement** `format.js`, then `app.js` and `styles.css` additions. Every inserted string through `esc`.
- [ ] **Step 4: Run** `node --test site/tests/format.test.js`, `node --check site/app.js`, `python -m pytest` — expected pass.
- [ ] **Step 5: Check in a browser** (`python -m pipeline.run --offline tests/fixtures`, `python -m http.server 8000 -d site`) at 375 px, dark and light: game 2026020338 Pelaajat section with both toggles and all three scopes (home scope shows the no-data note); a game with `players: null` has no section; `#/pelaajat` lists; edit one name in `site/data/latest.json` to `<img src=x>&"` and confirm it renders as text (Review Focus 5); no horizontal scroll; three tabs fit. Restore generated files; commit nothing under `site/data/` or offline `data/`.
- [ ] **Step 6: Commit:** `feat: Pelaajat section and tab`

---

### Task 9: Docs and first live run  — model: Haiku (steps 3–4 by the controller)

Spec: 17; decisions 19–28.

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-11-15-nhl-analyysi-design.md`

- [ ] **Step 1:** Append decisions 19–28 to spec section 17 in Finnish (numbered 19–28, same style as 10–18). README (Finnish, stay under 80 lines in total): section `Pelaajaennusteet` — how probabilities are derived in three sentences, `python -m pipeline.player_backtest --tune-season 20242025 --gate-season 20252026`, that the site shows players only after `model/player_params.json` has `gate.passed`, and that lineups are estimates. **Commit:** `docs: phase 3 decisions and player backtest`
- [ ] **Step 2: Run** `python -m pytest`, `python -m ruff check .`, `node --test site/tests/format.test.js` — expected pass.
- [ ] **Step 3 (controller, after the user confirms):** `gh workflow run pipeline.yml`, `gh run watch`. Expected: run green; `latest.json` games have `players` where both teams have cache history; review has no `[VIRHE] PROB/ROSTER` from players; `data/history/players/20262027.jsonl` gained rows; `calibration.draw` present.
- [ ] **Step 4 (controller):** on the phone, check the Pelaajat tab, one game's section and the status page draw tile. Report the draw gap line to the user.

---

## Execution Notes for the Dispatcher

- Task 1 is independent and goes first. Tasks 2 → 3 → 4 are sequential (same module). Task 5 needs 2–4. Task 6 needs 2–4 (its code can run in parallel with Task 5; its real-data steps need nothing from Task 5). Tasks 7 and 8 need Task 5's output shape and can run in parallel. Task 9 last, after Task 6's gate has passed.
- Run `python tools/review.py` after every task; read only `reports/review.md`.
- Real-data and network steps (Task 1 step 5, Task 6 steps 5–7, Task 9 steps 3–4) are run by the controlling session. Task 6 is the only modelling judgement: if the gate fails, return the report to the user instead of changing grids, seasons or the baseline.
- Phase 4 entry points this phase leaves: `PlayerRates` contribution rates (input for spec 6.6), `tracking.player_outcomes` + `metrics.binary_vs_base` (live player Brier for the performance page), `players.*.top[].odds` (player props), `calibration.draw` (performance page).
