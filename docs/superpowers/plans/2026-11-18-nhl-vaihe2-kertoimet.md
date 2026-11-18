# NHL-analyysi, Phase 2 (odds and value) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the regulation-draw calibration, then fetch bookmaker odds from The Odds API twice a day, turn them into no-vig market probabilities, best prices, edge and EV against the model, log them for CLV, and show a Finnish "Ylikertoimet" view and an odds section per game.

**Architecture:** `draw_kappa` is refitted analytically from one `draw_kappa = 1` backtest run per season (tied simulations are only reweighted, so every κ can be evaluated without re-simulating). A new optional source `pipeline/sources/odds.py` makes one featured-markets call per evening run, parses and matches events to NHL game ids, and stores a snapshot in `data/state/odds.json`. `pipeline/value.py` is pure computation over that snapshot and the game object; `run.py` attaches the result as `game.odds`, `tracking.py` logs `market`/`picks` and an odds history, and the site and `tools/review.py` read the new block.

**Tech Stack:** unchanged from phase 1 (Python 3.12, requests, numpy, pandas, scikit-learn, jsonschema, pytest, ruff; plain HTML/CSS/ES modules; GitHub Actions). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-15-nhl-analyysi-design.md` — sections 15–17 override earlier sections; section 16 holds the user's decisions. Phase-1 plan: `docs/superpowers/plans/2026-11-15-nhl-vaihe1-mvp.md` (its Global Constraints and Shared Contracts still apply; this plan only adds to them).

## Scope

In: draw calibration refit (re-run gate), `sources/odds.py`, Odds API name mapping in `teams.py`, `value.py`, `game.odds` + schema, `market`/`picks` in the prediction log, `data/history/odds/`, closing line and CLV helpers, frontend tab `Ylikertoimet` and detail section `Kertoimet`, `review.check_odds`, workflow secret wiring, README and spec section 17.

Out (phases 3–4, do not build): player probabilities, insights (incl. `model_vs_market`), H2H, form, performance page and `performance.json` (CLV/ROI are only logged and computable now), Kelly stakes, odds-movement tracking.

## Findings that shape this plan

1. **Draw rate by season (regular season, from the committed cache):** 2023-24 0.2073, 2024-25 0.2066, 2025-26 **0.2485**, 2026-27 to date 0.2229 (n = 332). `draw_kappa = 1.273` was fitted on 2024-25 and matches that season; 2025-26 is a draw-heavy outlier. No κ fitted out-of-sample on earlier seasons can bring the 2025-26 gap under 0.015 (pooling 2024-25 + 2025-26 leaves ≈ 0.022). The plan therefore fits κ on **2025-26 + 2026-27 to date** (the most recent completed season plus the current one, which is also what production should track). The 2025-26 draw check is then in-sample; the out-of-sample signal is the 2026-27 gap, reported alongside. Expected: κ ≈ 1.55–1.65, 2025-26 gap ≈ 0.006, 2026-27 gap ≈ 0.02. The win-probability gate (Brier/log loss on `home_win`) is barely affected by κ and stays out-of-sample in every respect except κ.

2. **1X2 odds (`h2h_3_way`) are not available in the base call.** The Odds API lists `h2h_3_way` under "Additional Markets", which "need to be accessed one event at a time" via `/v4/sports/{sport}/events/{eventId}/odds`, and says additional markets "are currently limited to US sports and selected bookmakers"; for ice hockey only the period variants `h2h_3_way_p1..p3` are listed. Per event cost = markets returned × regions, so 1X2 for ~7 games × 2 runs/day ≈ 420 credits/month on top of the 186 for featured markets — over the 500 limit, and Veikkaus/EU books are unlikely to be among the "selected bookmakers". Per spec section 16 the plan **skips 1X2 odds**. Two cheap substitutes are built instead: (a) if any bookmaker returns a three-outcome `h2h` (with `Draw`), it is treated as 1X2 automatically; (b) the game page always shows the model's 1X2 fair odds and the minimum Veikkaus price that would be a value bet, so the user can compare Veikkaus 1X2 by hand. `tools/record_odds_fixture.py --probe-3way` spends at most 1 credit once to show which bookmakers (if any) return `h2h_3_way` for NHL.

3. **Odds API event ids are 32 hex characters**, exactly what `review.check_secret` (`\b[0-9a-f]{32}\b`) flags as a leaked key. Event ids are therefore never persisted anywhere (state, history, `latest.json`); events are keyed by NHL game id after matching.

4. **The key goes in the `x-api-key` request header**, not the `apiKey` query parameter, so it can never appear in a URL, a `requests` exception message or a log line.

## Plan decisions (append to spec section 17 in Task 8, in Finnish)

10. `draw_kappa` is fitted by minimising regulation 1X2 log loss over the pooled seasons 2025-26 + current season to date (finding 1); `tune()` uses the same fitter. `MODEL_VERSION` becomes `1.1.0`.
11. 1X2 odds are not fetched (finding 2); a three-outcome `h2h` from any bookmaker is used as 1X2 when present.
12. Odds are fetched only when `pipeline.run` gets `--odds` (the 15:17 and 21:37 UTC crons and manual runs). Other runs reuse `data/state/odds.json` when it is under 36 h old. Prices are re-evaluated against the current model on every run.
13. Odds staleness threshold is 16 h (not the spec's 14 h): the weekend 12:17 UTC extra run is 14 h 40 min after the 21:37 fetch.
14. Betfair Exchange prices are reduced by a 5 % commission on winnings before EV; vig outside −1 %…15 % excludes that bookmaker's market (exchanges can sit just below 0 %).
15. A value pick is judged at the best price; each pick also carries Veikkaus's own price, EV and whether it qualifies on its own (`veikkaus.value`), which drives the "Vain Veikkaus" filter.
16. Only spreads of ±1.5 and totals on whole or half lines are evaluated; other lines are kept in the bookmaker table but get no model probability.
17. Closing line = Pinnacle (else median) no-vig reference of the last odds history row before puck drop, derived from the history like `official`, not stored separately.

## Global Constraints

Phase-1 Global Constraints apply unchanged. Additionally:

- The Odds API key is read only from env `ODDS_API_KEY`, sent only as header `x-api-key`, and never appears in a URL, log message, exception text, `data/`, `reports/`, `site/data/` or a fixture. Notes on failure use the HTTP status or exception class name only.
- No Odds API event id is written anywhere (finding 3).
- The odds source is optional: any failure (network, HTTP, validation, missing key) sets source `odds` to `failed` (or `stale` when skipped for credits) and the run continues; exit code and other outputs are unchanged.
- All new numbers live in `pipeline/config.py` (constants listed in Task 2 and Task 1); thresholds exactly: EV ≥ 0.03, edge ≥ 0.02 (2 pp), suspicious when |p_model − p_market| > 0.10, credits skip below 30, review warning below 60, reuse ≤ 36 h, stale > 16 h.
- `schema_version` stays `1` (the change is additive: `odds` goes from always-null to object-or-null). `MODEL_VERSION = "1.1.0"`.
- Budget: one call per `--odds` run, markets `h2h,totals,spreads`, `bookmakers=` the 10 keys (= 1 region) → 3 credits/call ≈ 186 credits/month. Fallback `regions=eu,fi` (6 credits/call) only after HTTP 422 on the bookmakers call. Empty responses cost nothing.
- UI copy Finnish, exactly as written in Task 7; decimal comma; odds with two decimals (`1,66`); percentages of probability with one decimal in the odds views (`64,2 %`); signed values use a true minus `−`.
- Tests never touch the network; the conftest network block stays. Run as `python -m pytest`, `node --test site/tests/format.test.js`.

## Review Focus

1. An Odds API event whose team name is not in the map (renamed or relocated franchise, e.g. "Utah Hockey Club"): run succeeds, that event goes to `unknown_teams`, the game's `odds` stays null, review reports `[VIRHE] ODDS`. Tests in Task 2 (`test_unknown_team_is_reported`) and Task 6 (`test_unknown_team_is_error`).
2. A bookmaker market that is malformed — a missing side, a price ≤ 1.0, a totals pair with different points, a duplicate outcome: that bookmaker's market is skipped, nothing else is. Test in Task 2 (`test_malformed_markets_are_skipped`).
3. Total lines the simulation does not export (6.0 with a push, 5.0, 7.5) and quarter lines (6.25): whole lines use push-aware EV, half lines use the total distribution, quarter lines get no model probability. Tests in Task 3.
4. Pinnacle absent for a market or line: reference is the median of ≥ 2 valid bookmakers; with one bookmaker there is no reference and no pick. Tests in Task 3.
5. The API answers 401, 429, 500, a timeout or `[]`: source `failed` with a note that contains no key; `[]` is `ok` with note `Ei kertoimia tarjolla`; the previous snapshot is reused if under 36 h old. Tests in Task 2 and Task 5.

## Shared Contracts

Give this section, Global Constraints, the phase-1 Shared Contracts, the task itself and the spec sections it cites to every implementer.

**`BookMarkets`** (one bookmaker's parsed markets for one event; prices decimal, raw, before commission):
`{"h2h": [home, away] | None, "h2h3": [home, draw, away] | None, "totals": [[line, over, under], ...], "spreads": [[home_point, home_price, away_price], ...]}`

**`ParsedEvent`**: `{"home_name": str, "away_name": str, "home": abbr | None, "away": abbr | None, "commence_utc": "YYYY-MM-DDTHH:MM:SSZ", "books": {book_key: BookMarkets}}` (no event id).

**`OddsResponse`** (dataclass in `sources/odds.py`): `events: list[dict]` (raw), `remaining: int | None`, `used: int | None`, `last: int | None`, `fallback: bool`.

**`data/state/odds.json`** (snapshot, written only after a successful fetch): `{"fetched_at": iso, "remaining": int | None, "games": {"<game_id>": {book_key: BookMarkets}}, "unknown_teams": [names], "unmatched": [{"home", "away", "commence_utc"}]}`.

**`data/state/sources.json` entry `odds`**: phase-1 fields plus `"remaining": int | None, "used": int | None`. On success `note` = `Krediittejä jäljellä {remaining}` (plus ` · varahaku alueilla eu,fi` when `fallback`).

**`game.odds`** (null when no snapshot under 36 h or no bookmaker for the game; started games keep their frozen block):

```jsonc
{"fetched_at": "2026-11-17T21:37:40Z",
 "markets": [{
   "key": "h2h",                  // h2h | 1x2 | totals | spreads
   "line": null,                  // totals: the total line; spreads: the HOME point (-1.5 or 1.5); else null
   "ref": "pinnacle",             // pinnacle | median | null
   "outcomes": [{
     "key": "home",               // h2h: home|away; 1x2: home|draw|away; totals: over|under; spreads: home|away
     "p_model": 0.642,            // null when the model cannot price the line; conditional on no push
     "p_market": 0.615,           // reference no-vig probability or null
     "best": {"book": "coolbet", "price": 1.66},
     "veikkaus": {"price": 1.55, "ev": -0.0049, "value": false},   // or null
     "edge": 0.027, "ev": 0.066,  // null when p_model or p_market is null
     "value": true, "flag": null  // flag: "suspicious" | null
   }],
   "books": [{"book": "pinnacle", "prices": [1.58, 2.52], "vig": 0.0298, "valid": true}]
 }],
 "picks": [{"market": "h2h", "line": null, "outcome": "home", "book": "coolbet", "price": 1.66,
            "p_model": 0.642, "p_market": 0.615, "edge": 0.027, "ev": 0.066, "flag": null,
            "veikkaus": {"price": 1.55, "ev": -0.0049, "value": false}}]}
```

Probabilities, edge, EV and vig rounded to 4 decimals. `books[].prices` follow the outcome order of the market. `picks` = outcomes with `value: true`, unflagged first by `ev` descending, then flagged by `ev` descending.

**Prediction log** (`data/history/predictions/{season}.jsonl`): `market` = `value.market_summary(game.odds)` = `{"fetched_at", "h2h": [p_home, p_away] | null, "1x2": [h, d, a] | null, "ref": "pinnacle" | "median" | null, "best_h2h": {"home": {"book", "price"}, "away": {...}} | null}` or null; `picks` = `game.odds.picks` or null.

**Odds history** (`data/history/odds/{season}.jsonl`, one row per matched game per successful fetch): `{"ts": fetched_at, "game_id", "start_utc", "home", "away", "books": {book_key: BookMarkets}, "ref": RefSummary}` where `RefSummary` = `{"source": "pinnacle" | "median" | null, "h2h": [p_home, p_away] | null, "1x2": [...] | null, "totals": {"<line>": p_over}, "spreads": {"<home_point>": p_home}}`.

## File Structure

```
pipeline/config.py            + odds/value constants, draw-fit constants, MODEL_VERSION 1.1.0
pipeline/backtest.py          + reg-outcome columns, reweight_draw, fit_draw_kappa, --refit-draw
pipeline/simulate.py          + total_dist
pipeline/predict.py           + _internal.total_dist
pipeline/teams.py             + Odds API name -> abbr
pipeline/sources/odds.py      NEW  fetch, parse, match (optional source)
pipeline/value.py             NEW  no-vig, reference, best price, edge/EV, picks, summaries, closing/CLV
pipeline/schema/latest.schema.json  odds block
pipeline/tracking.py          market/picks in log rows, odds history
pipeline/run.py               --odds, odds stage, snapshot reuse
tools/review.py               check_odds
tools/record_odds_fixture.py  NEW  manual, network: record fixture, probe h2h_3_way
site/app.js, site/format.js, site/styles.css, site/tests/format.test.js
.github/workflows/pipeline.yml, README.md, spec section 17
tests/fixtures/odds__icehockey_nhl.json (hand-built), tests/fixtures/FIELDS.md
tests/test_backtest.py, test_simulate.py, test_odds_source.py (NEW), test_value.py (NEW),
tests/test_export.py, test_tracking.py, test_run.py, test_review.py, test_workflows.py
```

---

### Task 1: Draw calibration refit  — model: Sonnet (steps 6–8 run by the controller on real data)

Spec: 6.9 item 4, 15.3, finding 1.

**Files:**
- Modify: `pipeline/backtest.py`, `pipeline/config.py`, `tests/test_backtest.py`
- Produced by running it: `model/params.json`, `model/backtest_20252026.json`, `reports/backtest.md`

**Interfaces:**
- Consumes: `run_backtest`, `tune`, `gate`, `kappa_from`, `load_team_games`, `load_goalie_games` (existing), `config.load_params`, `config.load_gate`.
- Produces:
  - `ROW_COLUMNS` gains `p_reg_home, p_reg_away, y_reg` (`y_reg` int: 0 home regulation win, 1 draw, 2 away). Existing columns unchanged.
  - `reweight_draw(p_h: np.ndarray, p_d: np.ndarray, p_a: np.ndarray, kappa: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]` — `z = 1 + (kappa − 1)·p_d`; returns `(p_h/z, kappa·p_d/z, p_a/z)`. Exact for probabilities simulated with `draw_kappa = 1`.
  - `logloss_1x2(rows: pd.DataFrame, kappa: float) -> float` — mean −log of the reweighted probability of `y_reg`, clipped at `1e-6`.
  - `fit_draw_kappa(rows: pd.DataFrame) -> float` — grid `np.arange(KAPPA_GRID[0], KAPPA_GRID[1] + 1e-9, KAPPA_GRID[2])`, lowest `logloss_1x2`. Rows must come from a `draw_kappa = 1` run.
  - `draw_report(rows: pd.DataFrame, kappa: float) -> dict` → `{"n", "p_mean", "y_mean", "gap", "logloss_1x2"}` with `p_mean` = mean reweighted draw probability, `gap = p_mean − y_mean`.
  - `tune()` replaces `kappa_from(rows)` with `fit_draw_kappa(rows)`; `kappa_from` stays (reported as a diagnostic).
  - CLI addition: `python -m pipeline.backtest --tune-season 20242025 --gate-season 20252026 --refit-draw 20252026 20262027`. With `--refit-draw`: no tuning; `p = config.load_params()`, judged goalie mode = existing `gate.goalie_mode`; seasons loaded with `load_team_games`/`load_goalie_games` = every requested season plus the season before each; one `run_backtest(..., replace(p, draw_kappa=1.0), gg=gg, goalie_mode=mode)` per season in the union of refit, tune and gate seasons; `kappa = fit_draw_kappa(pooled refit-season rows)`; then the three goalie modes on the gate season with `replace(p, draw_kappa=kappa)` exactly as the existing main path. Pass = `gate(m)` and `abs(gap_gate) < DRAW_GAP_MAX`. Only on pass it writes `model/params.json` (new `draw_kappa`, gate rebuilt as today plus `"draw": {"kappa", "fit_seasons", "kappa_rate_match": kappa_from(pooled rows), "seasons": {season: draw_report}}`) and `model/backtest_{gate}.json`. It always appends to `reports/backtest.md` a section `## Draw calibration` with one row per season (n, model, actual, gap, 1X2 log loss at κ = 1 and at the fitted κ). Exit 0 on pass, 1 otherwise. A current-season gap above `DRAW_GAP_CURRENT_MAX` is logged as a warning, not a failure.
  - `config`: `KAPPA_GRID = (0.80, 2.50, 0.005)`, `DRAW_GAP_MAX = 0.015`, `DRAW_GAP_CURRENT_MAX = 0.03`, `MODEL_VERSION = "1.1.0"`.

- [ ] **Step 1: Write failing tests** in `tests/test_backtest.py` (synthetic league helpers already in the file):

```python
def test_rows_have_regulation_outcome_columns():
    rows = run_backtest(tg, "20252026", P, n_sims=500)["rows"]
    assert {"p_reg_home", "p_reg_away", "y_reg"} <= set(rows.columns)
    assert np.allclose(rows.p_reg_home + rows.p_reg_draw + rows.p_reg_away, 1, atol=2e-3)
    assert set(rows.y_reg) <= {0, 1, 2} and (rows.y_reg == 1).mean() == rows.y_reg_draw.mean()
def test_reweight_draw_identity_and_sum():
    h, d, a = reweight_draw(ph, pd_, pa, 1.0); assert np.allclose((h, d, a), (ph, pd_, pa))
    h, d, a = reweight_draw(ph, pd_, pa, 1.6); assert np.allclose(h + d + a, 1) and (d > pd_).all()
def test_fit_draw_kappa_matches_closed_form_for_constant_probs():
    # 1000 rows with p = (.45, .17, .38); 247 draws, 400 home, 353 away
    k = fit_draw_kappa(rows)
    assert abs(k - kappa_from(rows)) <= 0.005 and abs(k - 1.6015) < 0.006
def test_fit_draw_kappa_stays_on_grid():  # all draws -> 2.50; no draws -> 0.80
def test_draw_report_fields():
    r = draw_report(rows, 1.0); assert set(r) == {"n", "p_mean", "y_mean", "gap", "logloss_1x2"}
def test_tune_uses_fit_draw_kappa(monkeypatch):   # stub fit_draw_kappa -> 1.42; tune(...).draw_kappa == 1.42
def test_refit_writes_kappa_and_draw_block(root, monkeypatch):
    # params.json with gate.goalie_mode "projected"; run_backtest stubbed: kappa-1 rows with mean draw
    # 0.17 and actual 0.24; gate metrics passing -> exit 0, params.json draw_kappa == fitted value,
    # gate.draw.fit_seasons == ["20252026", "20262027"], every stubbed run_backtest call for fitting had draw_kappa == 1.0
def test_refit_leaves_params_untouched_when_draw_gap_too_large(root, monkeypatch):
    # stub makes the gate season gap 0.03 -> exit 1, params.json byte-identical, report has "## Draw calibration"
def test_refit_leaves_params_untouched_when_gate_fails(root, monkeypatch):
```

- [ ] **Step 2: Run** `python -m pytest tests/test_backtest.py` — expected FAIL.

- [ ] **Step 3: Implement** the Interfaces above. `y_reg` from `g.score_reg` vs `g.opp_score_reg` (home row).

- [ ] **Step 4: Run** `python -m pytest` and `python -m ruff check .` — expected all pass. **Commit:** `feat: fit draw_kappa on 1X2 log loss and refit command`

- [ ] **Step 5 (controller, real data, ~1–2 h, run in background):** `python -m pipeline.backtest --tune-season 20242025 --gate-season 20252026 --refit-draw 20252026 20262027`.

- [ ] **Step 6: Check `reports/backtest.md`.** Expected: exit 0; fitted κ 1.45–1.75; 2025-26 |gap| < 0.015; gate passed with Brier and log loss still below baseline; 2026-27 gap printed (≈ +0.02 expected). If the gap criterion cannot be met or the gate fails, stop and report the table to the user — do not widen `DRAW_GAP_MAX` or change seasons on your own.

- [ ] **Step 7: Online smoke:** `python -m pipeline.run` then `python tools/review.py --data-only` — expected exit 0, mean `reg_draw` over tonight's games noticeably higher than before (≈ 0.22–0.25).

- [ ] **Step 8: Commit:** `git add model reports && git commit -m "model: refit draw_kappa on 2025-26 + 2026-27, model 1.1.0"`

---

### Task 2: Odds source, team mapping and fixtures  — model: Haiku

Spec: 6.11 (fetch, budget, team names), 4.4, 5, 13.4; findings 2–4.

**Files:**
- Create: `pipeline/sources/odds.py`, `tests/test_odds_source.py`, `tests/fixtures/odds__icehockey_nhl.json`, `tools/record_odds_fixture.py`
- Modify: `pipeline/teams.py`, `pipeline/config.py`, `tests/fixtures/FIELDS.md`

**Interfaces:**
- Consumes: `nhl.SourceError`, `ScheduledGame`, `config.OFFLINE_DIR`, `config.TIMEOUT_S`.
- Produces:
  - `config` constants: `ODDS_BASE = "https://api.the-odds-api.com/v4"`, `ODDS_SPORT = "icehockey_nhl"`, `ODDS_BOOKMAKERS = ("veikkaus_fi", "unibet_fi", "leovegas_fi", "pinnacle", "betsson", "coolbet", "nordicbet", "unibet_se", "betfair_ex_eu", "marathonbet")`, `ODDS_MARKETS = ("h2h", "totals", "spreads")`, `ODDS_FALLBACK_REGIONS = "eu,fi"`, `ODDS_ENV_KEY = "ODDS_API_KEY"`, `ODDS_LOOKAHEAD_H = 36`, `ODDS_NEXT_GAME_H = 24`, `ODDS_MIN_REMAINING = 30`, `ODDS_LOW_CREDITS = 60`, `ODDS_REUSE_HOURS = 36`, `ODDS_STALE_HOURS = 16`, `ODDS_MATCH_TOLERANCE_H = 6`, `ODDS_REF_BOOK = "pinnacle"`, `ODDS_REF_MIN_BOOKS = 2`, `ODDS_VIG_RANGE = (-0.01, 0.15)`, `ODDS_EV_MIN = 0.03`, `ODDS_EDGE_MIN = 0.02`, `ODDS_SUSPICIOUS_GAP = 0.10`, `ODDS_COMMISSION = {"betfair_ex_eu": 0.05}`, `VEIKKAUS_BOOK = "veikkaus_fi"`, `ODDS_FIXTURE = "odds__icehockey_nhl.json"`.
  - `teams.abbr_from_odds_name(name: str) -> str | None` — normalise (NFKD, drop combining marks, casefold, remove `.`, collapse spaces) and look up in a map built from `TEAM_NAMES` values plus `ODDS_ALIASES = {"utah hockey club": "UTA"}`.
  - `@dataclass OddsResponse` (Shared Contracts).
  - `fetch_odds(now: datetime, api_key: str | None) -> OddsResponse` — offline (`config.OFFLINE_DIR` set): reads `OFFLINE_DIR / ODDS_FIXTURE`, header fields `None`, key ignored; missing file → `SourceError("odds fixture missing")`. Online: `api_key` falsy → `SourceError("ODDS_API_KEY puuttuu")`. One GET `{ODDS_BASE}/sports/{ODDS_SPORT}/odds` with params `bookmakers` (comma-joined), `markets` (comma-joined), `oddsFormat=decimal`, `dateFormat=iso`, `commenceTimeFrom` = now, `commenceTimeTo` = now + `ODDS_LOOKAHEAD_H` (both `%Y-%m-%dT%H:%M:%SZ`), header `x-api-key`. HTTP 422 → one retry with `regions=ODDS_FALLBACK_REGIONS` instead of `bookmakers`, `fallback=True`. Other non-2xx → `SourceError(f"Odds API HTTP {status}")`; `requests.RequestException` → `SourceError(f"Odds API: {type(exc).__name__}")`; body not a JSON list → `SourceError`. Headers `x-requests-remaining/used/last` parsed to int (missing or non-numeric → None). No retries beyond the 422 fallback (each call costs credits). Own `requests.Session` with the generic User-Agent.
  - `parse_events(raw: list[dict]) -> list[ParsedEvent]` — events missing `home_team`, `away_team`, `commence_time` or `bookmakers` are dropped; only books in `ODDS_BOOKMAKERS`; per book and market the outcome rules below; a book with no valid market is omitted. Market `last_update` per market is ignored.
  - `match_events(events: list[ParsedEvent], games: list[ScheduledGame]) -> dict` → `{"games": {str(game_id): books}, "unknown_teams": sorted names, "unmatched": [{"home", "away", "commence_utc"}]}` — game ids as string keys, so the snapshot is identical in memory and after a JSON round trip. Match = same home and away abbreviation and `|commence − start_utc| ≤ ODDS_MATCH_TOLERANCE_H`. Unmapped team name → into `unknown_teams`; mapped but no game → into `unmatched`.

Outcome rules (the parser decides validity; everything invalid is skipped, never raised): every `price` must be a number > 1.0. `h2h` with exactly the two team names → `h2h: [home, away]`; with the two team names plus `Draw` → `h2h3: [home, draw, away]`; anything else skipped. `totals`: group by `point`; a point with exactly one `Over` and one `Under` → `[point, over, under]`; sorted by point. `spreads`: pair the home outcome with point `p` and the away outcome with point `−p` → `[p, home_price, away_price]`; sorted by `p`.

Hand-built fixture `tests/fixtures/odds__icehockey_nhl.json` (documented v4 shape; FIELDS.md marks it "käsin tehty — nauhoita uudelleen `tools/record_odds_fixture.py`:llä kun avain on saatavilla"): four events matching schedule fixture games `2026020333` LAK @ BOS (`2026-11-18T00:00:00Z`), `2026020335` EDM @ FLA, `2026020344` BOS @ DET (`2026-11-19T00:30:00Z`) and `2026020345` MTL @ NJD written as `"Montreal Canadiens"` (no accent). Books per event: `pinnacle`, `veikkaus_fi`, `coolbet`, `betfair_ex_eu`; markets h2h, totals 6.5 (Pinnacle also 6.0 on LAK @ BOS), spreads ±1.5; `marathonbet` on EDM @ FLA with a three-outcome h2h. Give BOS @ DET a coolbet h2h price at least 8 % above Pinnacle's fair price on one side so the offline run produces at least one pick. Event ids are placeholders `"fixture-1"`…; no key anywhere.

`tools/record_odds_fixture.py` (manual, the only odds code allowed to hit the network; key from env only): `python tools/record_odds_fixture.py` makes the same featured call as `fetch_odds` and saves it as `tests/fixtures/odds__icehockey_nhl__live.json` with every event `id` replaced by `"live-{n}"`; prints event count and the three usage headers. `--probe-3way` additionally calls `/sports/icehockey_nhl/events/{first event id}/odds` with `markets=h2h_3_way` and the same bookmakers, prints the bookmaker keys that returned the market (or `ei yhtään`) and `x-requests-last`. Never prints the key or a URL with it.

- [ ] **Step 1: Write failing tests** (`tests/test_odds_source.py`; online tests monkeypatch `requests.Session.request` with a recorder returning a fake response):

```python
def test_abbr_from_odds_name_variants():
    assert [teams.abbr_from_odds_name(n) for n in ("St Louis Blues", "St. Louis Blues",
            "Montreal Canadiens", "Montréal Canadiens", "Utah Hockey Club", "Utah Mammoth")] \
        == ["STL", "STL", "MTL", "MTL", "UTA", "UTA"]
    assert teams.abbr_from_odds_name("Arizona Coyotes") is None
def test_every_nhl_team_maps_from_its_display_name():   # all 32 TEAM_NAMES values round-trip
def test_offline_fetch_reads_fixture(monkeypatch):      # OFFLINE_DIR=fixtures -> 4 events, remaining None
def test_online_request_uses_header_not_query(monkeypatch):
    r = odds.fetch_odds(NOW, "k" * 32)
    call = recorder.calls[0]
    assert call.headers["x-api-key"] == "k" * 32 and "k" * 32 not in call.url + str(call.params)
    assert call.params["bookmakers"].count(",") == 9 and call.params["markets"] == "h2h,totals,spreads"
    assert r.remaining == 312 and r.used == 188 and r.last == 3
def test_422_falls_back_to_regions(monkeypatch):    # 2 calls; second has regions "eu,fi", no bookmakers; fallback True
@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_error_is_source_error_without_key(monkeypatch, status):
    with pytest.raises(SourceError) as e: odds.fetch_odds(NOW, KEY)
    assert str(status) in str(e.value) and KEY not in str(e.value) and len(recorder.calls) == 1
def test_connection_error_message_has_no_key(monkeypatch):   # stub raises ConnectionError(f"...{KEY}...")
def test_missing_key_is_source_error():
def test_parse_fixture_shapes():
    ev = odds.parse_events(fixture)
    lak = next(e for e in ev if e["home"] == "BOS" and e["away"] == "LAK")
    assert lak["books"]["pinnacle"]["h2h"] and [6.0, 6.5] == [t[0] for t in lak["books"]["pinnacle"]["totals"]]
    assert next(e for e in ev if e["home"] == "FLA")["books"]["marathonbet"]["h2h3"]
def test_malformed_markets_are_skipped():   # Review Focus 2: one side missing, price 1.0, Over 6.5 + Under 6.0,
                                            # duplicate home outcome -> those markets absent, others kept
def test_unknown_book_is_ignored():
def test_match_events_by_teams_and_time():  # fixture vs schedule__2026-11-17 games -> 4 game ids matched
def test_unknown_team_is_reported():        # Review Focus 1: "Utah Hockey Kings" -> unknown_teams, not matched
def test_mapped_event_without_game_is_unmatched():   # commence 10 h off -> unmatched
def test_parsed_events_hold_no_event_id():  assert not re.search(r"[0-9a-f]{32}", json.dumps(parse_events(raw_with_hex_ids)))
def test_live_fixture_parses_if_present():   # skipped unless odds__icehockey_nhl__live.json exists;
                                             # every event parses, every team name maps, >= 1 book per event
```

- [ ] **Step 2: Run** `python -m pytest tests/test_odds_source.py` — expected FAIL.

- [ ] **Step 3: Write the fixture and FIELDS.md section "The Odds API v4"** (response shape, the three headers, which fixture is hand-built, and the open questions of spec 13.4 to confirm from the first live recording: Veikkaus market set, whether totals/spreads include overtime per book, Pinnacle line set).

- [ ] **Step 4: Implement** `teams.abbr_from_odds_name`, the config constants, `pipeline/sources/odds.py`, `tools/record_odds_fixture.py`.

- [ ] **Step 5: Run** `python -m pytest` and `python -m ruff check .` — expected pass. **Commit:** `feat: The Odds API source with team mapping and hand-built fixture`

---

### Task 3: Value computation and odds schema  — model: Sonnet

Spec: 6.11 (calculation, thresholds, warning, market mapping), 4.5 (`odds`), 16; decisions 13–16.

**Files:**
- Create: `pipeline/value.py`, `tests/test_value.py`
- Modify: `pipeline/simulate.py`, `pipeline/predict.py`, `pipeline/schema/latest.schema.json`, `tests/test_simulate.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: `BookMarkets` (Task 2), config `ODDS_*` constants (Task 2), the game object with `prob`, `score.spread`, `_internal`.
- Produces:
  - `simulate(...)` result gains `"total_dist": [P(final total = k) for k in 0..TOTAL_DIST_MAX-1]`, last bucket = `≥ TOTAL_DIST_MAX-1`, weighted, rounded to 5 decimals; `config.TOTAL_DIST_MAX = 15`. `predict_game` stores it as `_internal["total_dist"]` (not exported).
  - `novig(prices: list[float]) -> tuple[list[float], float]` — `q = 1/price`, returns `(q/Σq, Σq − 1)`.
  - `effective_price(book: str, price: float) -> float` — `1 + (price − 1)·(1 − ODDS_COMMISSION.get(book, 0))`.
  - `model_prob(game: dict, market: str, line: float | None, outcome: str) -> tuple[float, float] | None` → `(p_win, p_push)`. `h2h`: `home_win`/`away_win`, push 0. `1x2`: `reg_home`/`reg_draw`/`reg_away`. `totals` line `L` from `_internal.total_dist` (`T` = final total incl. OT/SO goal): half line → over `P(T > L)`, under `P(T < L)`; whole line → push `P(T = L)`; any other fraction → None. `spreads` home point −1.5 → home `spread["home_-1.5"]`, away `spread["away_+1.5"]`; home point +1.5 → home `spread["home_+1.5"]`, away `spread["away_-1.5"]`; other points → None. Missing `_internal` → None.
  - `game_odds(game: dict, books: dict[str, BookMarkets], fetched_at: str) -> dict | None` — the `game.odds` block of Shared Contracts; None when `books` is empty. Per market and line: each book's market is `valid` when its vig is inside `ODDS_VIG_RANGE`; reference = Pinnacle's no-vig when Pinnacle is valid for that market and line, else the per-outcome median of valid books' no-vig when at least `ODDS_REF_MIN_BOOKS` are valid (renormalised to sum 1), else None. `p_model = p_win / (1 − p_push)`. `best` = valid book with the highest `effective_price` (ties: config order), shown with its raw price. `ev = p_win · effective_price + p_push − 1`. `edge = p_model − p_market`. `value = ev ≥ ODDS_EV_MIN and edge ≥ ODDS_EDGE_MIN`. `flag = "suspicious"` when `|edge| > ODDS_SUSPICIOUS_GAP`. `veikkaus` uses Veikkaus's own price with the same formulas, `value` by the same two thresholds. Market order: `h2h`, `1x2`, `totals` by line, `spreads` by line.
  - `market_summary(odds: dict | None) -> dict | None` and `ref_summary(books: dict[str, BookMarkets]) -> dict` (Shared Contracts; `ref_summary` needs no model).
  - `closing_lines(rows: list[dict]) -> dict[int, dict]` — per game the `ref` of the odds history row with the greatest `ts` strictly below `start_utc`.
  - `clv(price: float, p_close: float) -> float` = `price · p_close − 1`.
  - Schema: `$defs.odds` per Shared Contracts (`market.key` enum `h2h|1x2|totals|spreads`; `ref` enum `pinnacle|median|null`; outcome `key` enum `home|away|draw|over|under`; `p_model`, `p_market` prob-or-null; `best` null or `{book: string, price: number > 1}`; `flag` enum `suspicious|null`; `value` boolean; pick fields required); game property `odds` = `anyOf [null, $defs.odds]`.

- [ ] **Step 1: Write failing tests** (`tests/test_value.py` builds a minimal game: `prob` 0.642/0.358 and 1X2 .53/.22/.25, spreads from a known dict, `_internal.total_dist` hand-made; `tests/test_simulate.py`, `tests/test_export.py` additions):

```python
def test_total_dist_sums_to_one_and_matches_over():   # sum ≈ 1; 1 - sum(dist[:6]) ≈ total.over["5.5"] (1e-3)
def test_novig_known_numbers():
    p, vig = value.novig([1.58, 2.52]); assert vig == pytest.approx(1/1.58 + 1/2.52 - 1)
    assert p[0] == pytest.approx((1/1.58) / (1/1.58 + 1/2.52)) and sum(p) == pytest.approx(1)
def test_ev_and_edge_known_numbers():          # p_model .642, Pinnacle 1.58/2.52, coolbet home 1.66
    o = home_outcome(game_odds(...)); assert o["ev"] == pytest.approx(.642 * 1.66 - 1, abs=1e-4)
    assert o["edge"] == pytest.approx(.642 - .6146, abs=1e-4) and o["value"] is True
def test_value_needs_both_thresholds():        # ev .05 & edge .015 -> False; ev .025 & edge .03 -> False
def test_suspicious_flag_over_10pp():          # p_model .75 vs market .62 -> flag "suspicious", still a pick, sorted last
def test_picks_sorted_unflagged_by_ev_then_flagged():
def test_reference_falls_back_to_median():     # Review Focus 4: no Pinnacle, 3 books -> ref "median", p_market = renormalised medians
def test_single_book_gives_no_reference_and_no_pick():
def test_invalid_vig_excluded_from_best_and_reference():   # vig .20 book has best price -> not best, books[].valid False
def test_betfair_commission_lowers_ev():       # same raw price: betfair ev == p*(1+.95*(price-1)) - 1 < coolbet ev
def test_whole_line_total_uses_push():         # Review Focus 3: line 6.0 -> ev == p(T>6)*price + p(T=6) - 1;
                                               # p_model == p(T>6) / (1 - p(T=6))
def test_half_line_outside_exported_lines():   # 7.5 and 5.0 priced from total_dist
def test_quarter_line_has_no_model_prob():     # 6.25 -> p_model None, ev None, value False, books kept
def test_spreads_map_both_point_signs():       # home -1.5 and home +1.5 markets use the four spread keys
def test_three_way_h2h_becomes_1x2_market():
def test_veikkaus_block_and_value_flag():
def test_no_books_gives_none():
def test_game_without_internal_is_unpriced():  # started/frozen object -> every p_model None
def test_market_summary_and_ref_summary_shapes():
def test_closing_line_is_last_row_before_start():   # rows 15:17, 21:37, and 00:30 after start -> 21:37 ref
def test_clv(): assert value.clv(1.66, 0.62) == pytest.approx(0.0292)
# test_export.py
def test_odds_block_validates():               # game_odds output on a fixture game passes export.validate
def test_odds_bad_flag_rejected():             # flag "maybe" -> error mentioning "flag"
```

- [ ] **Step 2: Run** `python -m pytest tests/test_value.py tests/test_simulate.py tests/test_export.py` — expected FAIL.

- [ ] **Step 3: Implement** the Interfaces above.

- [ ] **Step 4: Run** `python -m pytest` and `python -m ruff check .` — expected pass. **Commit:** `feat: no-vig market reference, best price, edge, EV and value picks`

---

### Task 4: Prediction log market, odds history  — model: Haiku

Spec: 8 (storage: market level, best prices, picks), 4.2 (`history/odds`).

**Files:**
- Modify: `pipeline/tracking.py`, `tests/test_tracking.py`

**Interfaces:**
- Consumes: `value.market_summary`, `value.ref_summary` (Task 3); `BookMarkets`.
- Produces:
  - `log_row(game_obj, ts)`: `market = value.market_summary(game_obj.get("odds"))`, `picks = (game_obj.get("odds") or {}).get("picks")` (None when no odds). Other fields unchanged.
  - `odds_rows(snapshot: dict, games: list[dict]) -> list[dict]` — one odds history row (Shared Contracts) per game id in `snapshot["games"]` that is in `games` (exported objects give `start_utc`, `home.abbr`, `away.abbr`); `ts = snapshot["fetched_at"]`.
  - `append_odds(rows: list[dict]) -> int` — appends to `data/history/odds/{season_of(game_id)}.jsonl`.
  - `read_log(kind, season)` accepts `"odds"`.

- [ ] **Step 1: Write failing tests:**

```python
def test_log_row_market_and_picks_from_odds():     # game with odds -> market["ref"] == "pinnacle", picks == odds["picks"]
def test_log_row_without_odds_keeps_nulls():
def test_odds_rows_and_append_roundtrip(root):     # 2 snapshot games, 1 exported -> 1 row; read_log("odds", season) == rows
def test_odds_history_has_no_hex_ids_or_key(root): # file text has no 32-hex match
def test_read_log_rejects_unknown_kind():
```

- [ ] **Step 2: Run** `python -m pytest tests/test_tracking.py` — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Commit:** `feat: log market reference, picks and odds history`

---

### Task 5: Pipeline integration  — model: Sonnet

Spec: 4.1 step 7, 4.3 (odds runs), 4.4, 6.11 (budget guards); decisions 12–13.

**Files:**
- Modify: `pipeline/run.py`, `tests/test_run.py`

**Interfaces:**
- Consumes: `odds.fetch_odds`, `odds.parse_events`, `odds.match_events`, `OddsResponse` (Task 2); `value.game_odds` (Task 3); `tracking.odds_rows`, `tracking.append_odds` (Task 4); existing `optional_source`, `is_started`, `_read_json`, `_write_json`.
- Produces:
  - CLI flag `--odds`: fetch odds this run. `run(offline, now, extra_if_early, with_odds: bool = False)`.
  - `ODDS_STATE = ("data", "state", "odds.json")`.
  - `odds_stage(selected: list[ScheduledGame], state: dict, now: datetime, fetch: bool) -> tuple[dict | None, bool]` → `(snapshot, fetched_now)`. Rules in order: (a) `fetch` false → previous snapshot if its `fetched_at` is within `ODDS_REUSE_HOURS`, else None; `state["odds"]` left as it was. (b) No unstarted selected game starts within `ODDS_NEXT_GAME_H` → as (a), no call. (c) `state["odds"]["remaining"]` is an int below `ODDS_MIN_REMAINING` → no call; `state["odds"]` status `stale`, note `Haku ohitettu: krediittejä jäljellä {n}`; snapshot as (a). (d) Otherwise `optional_source("odds", lambda: odds.fetch_odds(now, os.environ.get(ODDS_ENV_KEY)), state, now)` (offline: same call, the fixture is read). Failure → previous snapshot as (a). Success → `match_events(parse_events(resp.events), selected)` becomes the new snapshot with `fetched_at = _iso(now)` and `remaining`; `state["odds"]` gets `remaining`, `used` and note `Krediittejä jäljellä {remaining}` (`Ei kertoimia tarjolla` when `events` is empty; ` · varahaku alueilla eu,fi` appended on fallback; when `remaining` is None the note is `Kertoimet haettu`).
  - In `run`: after `_predict_all`, every game with `state == "upcoming"` gets `game["odds"] = value.game_odds(game, snapshot["games"].get(str(game["id"]), {}), snapshot["fetched_at"])` when a snapshot exists (else stays None); the `odds` state entry is carried from `sources.json` like `injuries` and only exported when present. In the durable section (after validation): when `fetched_now`, write `ODDS_STATE` and `tracking.append_odds(tracking.odds_rows(snapshot, games))`. `append_predictions` now logs `market`/`picks` automatically (Task 4).

- [ ] **Step 1: Write failing tests** (reuse `seeded`, `go`, `latest_of`):

```python
def test_offline_odds_run_fills_odds(seeded):
    assert go(NOW, "--odds") == 0
    lat = latest_of(seeded); games = {g["id"]: g for g in lat["games"]}
    assert games[2026020333]["odds"]["markets"][0]["key"] == "h2h"
    assert any(g["odds"] and g["odds"]["picks"] for g in lat["games"])
    assert {s["id"]: s["status"] for s in lat["sources"]}["odds"] == "ok"
    assert export.validate(lat) == [] and tracking.read_log("odds", SEASON)
    row = tracking.read_log("predictions", SEASON)[-1]; assert "market" in row and "picks" in row
def test_run_without_odds_flag_reuses_fresh_snapshot(seeded):  # run --odds, then run 6 h later without -> same fetched_at, no new history rows
def test_snapshot_older_than_36h_is_not_used(seeded):          # second run 37 h later -> odds None
def test_odds_failure_does_not_fail_run(seeded, monkeypatch):  # Review Focus 5: fetch_odds raises SourceError("Odds API HTTP 401")
    # -> exit 0, odds source "failed", note contains "401", previous snapshot reused when fresh
def test_skip_when_credits_low(seeded, monkeypatch):           # state odds.remaining 25 -> fetch_odds never called, status "stale"
def test_skip_when_no_game_within_24h(seeded, monkeypatch):    # --now with first game 30 h away -> fetch_odds never called
def test_empty_odds_response_is_ok_with_note(seeded, monkeypatch):
def test_started_game_keeps_frozen_odds(seeded):               # second run after puck drop -> started game's odds == run 1's
def test_offline_without_odds_flag_has_no_odds_source(seeded): # phase-1 assertion {"nhl","injuries"} still holds
```

- [ ] **Step 2: Run** `python -m pytest tests/test_run.py` — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: Run by hand:** `python -m pipeline.run --offline tests/fixtures --odds` — expected exit 0, `site/data/latest.json` with `odds` on the four fixture games, `data/state/odds.json` present. Delete the generated `data/` files from the working tree before committing (offline output is not committed).

- [ ] **Step 6: Commit:** `feat: odds stage in the pipeline with snapshot reuse and budget guards`

---

### Task 6: Review check for odds  — model: Haiku

Spec: 10 (ODDS row), 6.11 (credits); decision 13.

**Files:**
- Modify: `tools/review.py`, `tests/test_review.py`

**Interfaces:**
- Consumes: `data/state/sources.json` (`odds` entry with `remaining`), `data/state/odds.json` snapshot, `latest.games[].odds`, config `ODDS_*`.
- Produces: `check_odds(ctx: Ctx) -> list[Problem]`, appended to `CHECKS` after `check_source`. Returns `[]` when neither `sources.json` nor `latest.sources` has an `odds` entry (odds never configured). Problems, group `ODDS`:
  - VAROITUS `odds` — `kertoimet haettu {h:.1f} h sitten` when `last_ok_at` is older than `ODDS_STALE_HOURS` and an upcoming game exists; hint `tarkista ODDS_API_KEY ja ajon --odds-lippu`.
  - VAROITUS `odds` — `krediittejä jäljellä {n}` when `remaining < ODDS_LOW_CREDITS`; hint `hakuja ohitetaan alle 30 krediitin; tarkista kuukauden kulutus`.
  - VAROITUS `{game}` — `{book} {market}: marginaali {vig:.1%}` for every `books[]` entry with `valid: false`; hint `tarkista välittäjän kertoimet tai ODDS_VIG_RANGE`.
  - VIRHE `data/state/odds.json` — `tuntematon joukkue: {name}` per `unknown_teams` entry; hint `lisää nimi pipeline/teams.py ODDS_ALIASES`.
  - VAROITUS `data/state/odds.json` — `kerroinottelu ilman NHL-ottelua: {away} @ {home} {commence}` per `unmatched`; hint `tarkista ajan toleranssi ja ohjelma`.
  - VAROITUS `{game}` — `ei kertoimia yhdeltäkään välittäjältä` for an upcoming game starting within 24 h of `generated_at` whose `odds` is null, only when the snapshot `fetched_at` is younger than `ODDS_STALE_HOURS`; hint `välittäjät eivät ehkä ole vielä avanneet kohdetta`.

- [ ] **Step 1: Write failing tests** (`root` seeded with a valid `latest.json` containing an odds block, `sources.json` with `odds`, and `data/state/odds.json`):

```python
def test_clean_odds_is_ok(root):                 assert review.main(ARGS) == 0
def test_no_odds_source_is_not_a_problem(root):
def test_stale_odds_is_warning(root):            # last_ok_at 17 h old -> exit 1, "[VAROITUS] ODDS"
def test_low_credits_is_warning(root):           # remaining 59 -> message contains "59"
def test_invalid_vig_is_warning(root):
def test_unknown_team_is_error(root):            # Review Focus 1 -> exit 2, "[VIRHE] ODDS", name in message
def test_unmatched_event_is_warning(root):
def test_game_without_odds_is_warning_only_when_fresh(root):
```

- [ ] **Step 2: Run** `python -m pytest tests/test_review.py` — expected FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest`, then `python tools/review.py --skip-tests` — expected pass / only real findings.

- [ ] **Step 5: Commit:** `feat: review check for odds freshness, credits, vig and team matching`

---

### Task 7: Frontend — Ylikertoimet tab and Kertoimet section  — model: Sonnet

Spec: 9 (view 1 badge, view 2 section 11, view 3, view 5), 16 (best price highlighted, Pinnacle reference); decisions 11, 15.

**Files:**
- Modify: `site/format.js`, `site/app.js`, `site/styles.css`, `site/tests/format.test.js`, `tests/test_value.py` (threshold sync test)

**Interfaces:**
- Consumes: `game.odds` (Shared Contracts), source `odds` in `latest.sources`; produce a local file with `python -m pipeline.run --offline tests/fixtures --odds`.
- Produces (`site/format.js`, pure):
  - `VALUE_EV_MIN = 0.03` (a pytest asserts it equals `config.ODDS_EV_MIN` by reading the file text)
  - `fmtOdds(x) -> "1,66"`; `fmtPct1(p) -> "64,2 %"`; `fmtPp(d) -> "+2,7" | "−1,3"` (percentage points, one decimal); `fmtEv(ev) -> "+6,6 %" | "−0,5 %"`
  - `bookName(key)`: `veikkaus_fi → Veikkaus`, `unibet_fi → Unibet`, `leovegas_fi → LeoVegas`, `pinnacle → Pinnacle`, `betsson → Betsson`, `coolbet → Coolbet`, `nordicbet → NordicBet`, `unibet_se → Unibet (SE)`, `betfair_ex_eu → Betfair Exchange`, `marathonbet → Marathonbet`, unknown → the key
  - `fairOdds(p) -> number` = `1 / p` (2 decimals); `minValuePrice(p) -> number` = `(1 + VALUE_EV_MIN) / p` rounded up to 2 decimals
  - `pickLabel(pick, game) -> string`: h2h `"{abbr} voittaa"`; 1x2 `"{abbr} voittaa varsinaisella peliajalla"` / `"Tasapeli varsinaisella peliajalla"`; totals `"Yli {line} maalia"` / `"Alle {line} maalia"` (line with comma); spreads `"{abbr} −1,5"` / `"{abbr} +1,5"` (away point = −home point)
  - `marketTitle(market, game)`: `h2h → "Voittaja (sis. jatkoaika)"`, `1x2 → "1X2 (varsinainen peliaika)"`, `totals → "Yli/alle {line}"`, `spreads → "Tasoitus {home abbr} {signed line}"`
  - `collectPicks(latest, {market = "all", veikkausOnly = false, showFlagged = false}) -> [{game, pick}]` — upcoming games only; `veikkausOnly` keeps picks with `veikkaus.value` and sorts by `veikkaus.ev`; otherwise unflagged by `ev` desc, then (when `showFlagged`) flagged by `ev` desc.

Structure and copy (`app.js`; reuse existing tokens and classes — `card`, `badge ok|warn|muted`, `stats`, `sec`, `note`; no new colour tokens):
- `TABS` gains `{id: "value", label: "Ylikertoimet", href: "#/ylikertoimet", icon: <inline SVG star>}`; route `#/ylikertoimet` → `valueView`; active tab derived from route (`list`/`game` → games, `value` → value).
- List card: badge `Ylikertoimia` (kind `ok`) when the game has an unflagged pick.
- `valueView`: heading `Ylikertoimet`; line `Kertoimet haettu {fmtDateTime(odds source fetched_at)}`; a segmented control `Kaikki · Voittaja · 1X2 · Yli/alle · Tasoitus` and two toggles `Vain Veikkaus` and `Näytä varoitusleimalliset` (filter state in a module variable; buttons with `aria-pressed`, tap targets ≥ `--tap`). One card per pick, linking to `#/ottelu/{id}`:
  - row 1: `{pickLabel} · {bookName} {fmtOdds(price)}`, right-aligned `EV {fmtEv(ev)}`
  - row 2: `Malli {fmtPct1(p_model)} · Markkina {fmtPct1(p_market)} · edge {fmtPp(edge)}`
  - row 3: `Veikkaus {fmtOdds} (EV {fmtEv})`, or `Veikkaus: ei kerrointa`
  - row 4: `{away} @ {home} · {fmtDate} {fmtTime}`
  - flagged: badge `Todennäköisemmin mallin virhe` (kind `warn`) and line `Suuri ero markkinaan — todennäköisemmin mallin virhe tai puuttuva tieto kuin löytö.`
  - empty: `Ei ylikertoimia juuri nyt.` plus the fetched line; no `odds` source at all: `Kertoimia ei ole vielä haettu.`
  - footnote: `Markkina = Pinnaclen marginaaliton todennäköisyys, tai välittäjien mediaani jos Pinnacle puuttuu. Ylikerroin: EV vähintään 3 % ja ero markkinaan vähintään 2 prosenttiyksikköä.`
- `SECTIONS` gains `{id: "odds", title: "Kertoimet", render: oddsSection}` after `goalies`; it always renders:
  - 1X2 block first, title `1X2 · varsinainen peliaika`: three columns `1 / X / 2` with `Malli {fmtPct1}`, `Reilu kerroin {fmtOdds(fairOdds)}`, `Ylikerroin, kun kerroin ≥ {fmtOdds(minValuePrice)}`. When the odds block has a `1x2` market, add `Paras {bookName} {fmtOdds}` and `EV` per column; otherwise the note `Kerroinlähde ei tarjoa NHL:n 1X2-markkinaa. Vertaa Veikkauksen 1X2-kerrointa reiluun kertoimeen.`
  - `odds === null`: `Kertoimia ei ole saatavilla tälle ottelulle.`
  - per market (`marketTitle`): a compact table, columns `Valinta · Malli · Markkina · Paras · Veikkaus · EV`; best cell `{fmtOdds} {bookName}`; a `value` row highlighted with class `is-value` (left border `--ok`) and a flagged row with the warning badge; `—` for nulls. Under it `<details><summary>Kaikki välittäjät</summary>` listing `{bookName}: {prices joined " / "} · marginaali {fmtPct1(vig)}`, invalid books with `(ei käytetä)`.
  - foot line `Kertoimet haettu {fmtDateTime(odds.fetched_at)}` and `Viite: Pinnacle` / `Viite: välittäjien mediaani` / `Ei viitettä`.
- Status view: `SOURCE_NAMES.odds = "Kertoimet (The Odds API)"`.
- At 375 px the tables must not scroll the page horizontally: wrap each table in a container with `overflow-x: auto`.

- [ ] **Step 1: Write failing tests** in `site/tests/format.test.js`:

```js
test("fmtOdds", () => assert.equal(fmtOdds(1.66), "1,66"));
test("fmtPct1", () => assert.equal(fmtPct1(0.642), "64,2 %"));
test("fmtPp", () => { assert.equal(fmtPp(0.027), "+2,7"); assert.equal(fmtPp(-0.013), "−1,3"); });
test("fmtEv", () => { assert.equal(fmtEv(0.066), "+6,6 %"); assert.equal(fmtEv(-0.0049), "−0,5 %"); });
test("bookName", () => { assert.equal(bookName("veikkaus_fi"), "Veikkaus"); assert.equal(bookName("x"), "x"); });
test("fair and min value price", () => { assert.equal(fairOdds(0.25), 4); assert.equal(minValuePrice(0.25), 4.12); });
test("pickLabel all markets", ...);   // h2h home, 1x2 draw, totals over 6.5 -> "Yli 6,5 maalia", spreads away at home line -1.5 -> "LAK +1,5"
test("collectPicks sorts and hides flagged by default", ...);
test("collectPicks veikkausOnly", ...);
test("collectPicks skips started games", ...);
```

and in `tests/test_value.py`: `test_frontend_ev_threshold_matches_config()` (regex `VALUE_EV_MIN = ([0-9.]+)` in `site/format.js` equals `config.ODDS_EV_MIN`).

- [ ] **Step 2: Run** `node --test site/tests/format.test.js` — expected FAIL.

- [ ] **Step 3: Implement** `format.js`, then `app.js` and the `styles.css` additions (reusing tokens). Escape every inserted string with `esc`.

- [ ] **Step 4: Run** `node --test site/tests/format.test.js`, `node --check site/app.js`, `python -m pytest` — expected pass.

- [ ] **Step 5: Check in a browser** (`python -m pipeline.run --offline tests/fixtures --odds`, `python -m http.server 8000 -d site`) at 375 px, dark and light: list badge, `#/ylikertoimet` with each filter and both toggles, a game's `Kertoimet` section with and without odds (set one game's `odds` to null in the file), no horizontal page scroll, `#/tila` shows `Kertoimet (The Odds API)`. Restore generated files; nothing under `site/data/` or offline `data/` output is committed.

- [ ] **Step 6: Commit:** `feat: Ylikertoimet view and odds section`

---

### Task 8: Workflow secret, README, spec, first live run  — model: Haiku (steps 5–8 by the controller, with the user)

Spec: 2 (secret), 4.3, 16; decisions 10–17.

**Files:**
- Modify: `.github/workflows/pipeline.yml`, `tests/test_workflows.py`, `README.md`, `docs/superpowers/specs/2026-11-15-nhl-analyysi-design.md`

**Interfaces:**
- Consumes: `python -m pipeline.run --odds` (Task 5), env `ODDS_API_KEY`.

Workflow requirements: only the `Run pipeline` step gets an `env:` block:

```yaml
        env:
          ODDS_API_KEY: ${{ (github.event.schedule == '17 15 * * *' || github.event.schedule == '37 21 * * *' || github.event_name == 'workflow_dispatch') && secrets.ODDS_API_KEY || '' }}
          ODDS_FLAG: ${{ (github.event.schedule == '17 15 * * *' || github.event.schedule == '37 21 * * *' || github.event_name == 'workflow_dispatch') && '--odds' || '' }}
        run: python -m pipeline.run $ODDS_FLAG${{ github.event.schedule == '17 12 * * 0,6' && ' --extra-if-early' || '' }}
```

No other step or workflow references `secrets.`; the morning (06:43) and weekend extra (12:17) runs get neither key nor flag.

- [ ] **Step 1: Replace `test_no_secret_reference_in_phase_1`** with:

```python
def test_odds_secret_only_in_run_step():
    text = _text("pipeline.yml")
    assert text.count("secrets.") == 1
    step = _step_containing(text, "id: run")
    assert "secrets.ODDS_API_KEY" in step and "--odds" in step
    for cond in ("'17 15 * * *'", "'37 21 * * *'", "github.event_name == 'workflow_dispatch'"):
        assert step.count(cond) == 2, cond     # key and flag share the same condition
    assert "'43 6 * * *'" not in step
def test_other_workflows_have_no_secrets():    # ci, backfill, train_xg
```

- [ ] **Step 2: Run** `python -m pytest tests/test_workflows.py` — expected FAIL. **Step 3: Edit `pipeline.yml`.** **Step 4: Run** `python -m pytest` — expected pass.

- [ ] **Step 5: README** (Finnish, keep under 80 lines): section `Kertoimet (The Odds API)` — key goes to GitHub: Settings → Secrets and variables → Actions → `ODDS_API_KEY`; locally only as an environment variable, never in a file; odds are fetched at 17.17 and 23.37 Finnish time and on manual runs; ≈ 186 credits/month of 500; 1X2 odds are not available from the source (finding 2) and the page shows fair 1X2 odds instead. Append decisions 10–17 to spec section 17 in Finnish. **Commit:** `feat: pass the odds key only to evening runs; docs`

- [ ] **Step 6 (user, manual):** create a free key at the-odds-api.com and add it as repository secret `ODDS_API_KEY`. Optionally set it in the local shell to record the live fixture.

- [ ] **Step 7 (controller, needs the key in the local env and the user's go-ahead; ≤ 4 credits):** `python tools/record_odds_fixture.py --probe-3way`. Report to the user: event count, which bookmakers returned `h2h_3_way` (expected none or US books only), remaining credits, whether `veikkaus_fi` appears in the featured response and with which markets. Run `python -m pytest` — `test_live_fixture_parses_if_present` now runs instead of skipping and must pass. Fill the FIELDS.md open questions from the recording. Commit the live fixture: `test: recorded Odds API response`.

- [ ] **Step 8 (controller, after the user confirms):** `gh workflow run pipeline.yml`, `gh run watch`. Expected: run green; `latest.json` source `odds` `ok` with `Krediittejä jäljellä …`; review has no `[VIRHE] ODDS`; the Ylikertoimet tab renders on the phone. If review reports an unknown team name, add it to `ODDS_ALIASES` with a test.

---

## Execution Notes for the Dispatcher

- Task 1 is independent and goes first (it changes predictions that every later check sees). Task 2 can run in parallel with Task 1. Task 3 needs Task 2's config constants and `BookMarkets`; Task 4 needs Task 3; Task 5 needs 2–4; Tasks 6 and 7 need Task 5's output shape (they can run in parallel); Task 8 last.
- Run `python tools/review.py` after every task; read only `reports/review.md`.
- Task 1 steps 5–8 and Task 8 steps 7–8 use real data or the network and are run by the controlling session. Task 1 is the only place with modelling judgement: if the draw criterion cannot be met as planned, return the per-season table to the user instead of changing seasons or thresholds.
- Phase 3 entry points this phase leaves: `market` in the prediction log (market baseline for Brier), `value.closing_lines` and `value.clv` (CLV for the performance page), `picks` with prices at pick time (ROI), `game.odds.markets[].outcomes[].flag` (feeds the `model_vs_market` insight).
