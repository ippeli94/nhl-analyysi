# Vaihe 6 — pudotuspelit, kauden vaihde ja omat vedot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** (A) The pipeline handles playoffs, preseason and the offseason/season change correctly; (B) the user can place imaginary bets on any priced outcome, and the site settles them automatically and tracks the user's own ROI % and profit in euros.

**Architecture:** (A) is an audit-and-fix of `run`/`cache`/`simulate`/`players` with time-travel tests (`--now`, synthetic schedules). (B) is browser-side: bets live in `localStorage` (single user, phone) with JSON export/import as backup; the pipeline publishes `site/data/results.json` (recent finished games) so the browser can settle bets; settlement rules mirror `pipeline/performance.settle` and are unit-tested in JS.

**Spec:** `docs/superpowers/specs/2026-11-15-nhl-analyysi-design.md` (13.8 season change and playoffs; 6.9 simulation; 6.10 players; 6.11 odds; 8 tracking; 9 UI). Earlier plan: `2026-11-19-nhl-vaihe4-konteksti.md`.

## Global Constraints

- Optional outputs never fail the run; nothing durable is written before `export.validate` passes; review exit 2 blocks deploy only for real errors.
- UI copy Finnish, decimal comma, everything interpolated through `esc`; mobile 375 px, touch targets ≥ 44 px; no new libraries.
- Bets never leave the user's browser (no network writes); `localStorage` access wrapped in try/catch; the page works without it.
- Settlement matches `pipeline/performance.settle`: h2h by final winner; 1x2 by regulation goals; totals vs final goals incl. SO winner goal, exact line = push (stake back); spreads: `line` is the HOME handicap, cover = margin + line vs 0; Betfair (`betfair_ex_eu`) 5 % commission on winnings; profit per bet = stake·(price−1) on win, −stake on loss, 0 on push.
- Tests: `.venv/Scripts/python -m pytest -q -p no:cacheprovider` exit 0, `ruff check .`, `node --check site/app.js`, `node --test site/tests/format.test.js` (plus new JS test files passed explicitly).

## Review Focus

1. Playoff game (game_type 3): no shootout — the draw mass after regulation must go fully to sudden-death OT; players' OT goal credited with probability 1 (not `ot_goal_share`). Test in Task 1.
2. Offseason (no games for weeks) and preseason (game_type 1): run returns 0, publishes an empty-but-valid latest.json with `next_game_date`, review does not raise SCHED/FRESH errors that block deploy. Test in Task 1.
3. Season change (first regular-season game of a new season): `_current_season`, ratings prior from the previous season, context/teamstats with empty current season — no crash. Test in Task 1.
4. A bet on a game that is postponed or absent from results.json stays "Odottaa" and is never settled wrongly; a bet whose game id is in results but whose market/line cannot be settled → "Ei ratkaistavissa", stake returned. Test in Task 3.
5. Corrupt or foreign data in localStorage / imported JSON → ignored with a message, existing bets untouched. Test in Task 3.

---

### Task 1: Playoffs, preseason and season change

**Files:** `pipeline/run.py`, `pipeline/simulate.py`, `pipeline/predict.py`, `pipeline/players.py`, `pipeline/features.py`, `tools/review.py` (only where the audit finds a defect), tests in `tests/test_run.py`, `tests/test_simulate.py`, `tests/test_players.py`.

- [ ] Audit how `game_type` 1/2/3 flows through schedule fetch (`sources/nhl.py` filters to `CACHED_GAME_TYPES` (2, 3)), `select_games`, `_current_season`, simulation (OT/SO split: `ot_slope`, SO), credited goals (`ot_goal_share`), `features.sim_params` (`ot_goal_share` currently pools playoffs — restrict to regular season), ratings (`TRAIN_GAME_TYPES` = (2,)), context/teamstats/insights, odds matching, review checks (SCHED/FRESH/ROSTER/ODDS) during offseason.
- [ ] Playoffs: pass `game_type` into `predict_game`/`simulate`; for game_type 3 the post-regulation tie is decided entirely by sudden-death OT (no SO) — home share of OT wins uses the same OT model as regular season; `prob.home_win + away_win == 1`; reg 1X2 unchanged. Credited goals in playoffs: OT winner goal credited with probability 1. Tests with a synthetic playoff game.
- [ ] Offseason/preseason: run with `--now` in July and in late September (only preseason games) on synthetic schedules → exit 0, valid latest.json with `games: []` and correct `next_game_date`; `tools/review.py --data-only` has no error-level problem caused only by having no games (FRESH must still apply to `generated_at`). Tests.
- [ ] Season change: first regular-season day of a new season with the previous season cached → ratings fit, players, context, teamstats, insights, lineup and performance do not crash; `season_compare.now` null/`last` filled; insights only from rules with enough sample. Test.
- [ ] Record decisions 36+ in spec section 17 (Finnish). Commit `fix: playoffs, preseason and season change`.

### Task 2: Publish recent results for settlement

**Files:** `pipeline/export.py`, `pipeline/run.py`, `pipeline/schema/results.schema.json` (new), `tools/review.py` (warning check), tests.

**Produces:** `site/data/results.json` = `{"generated_at", "games": [{"id", "date", "home", "away", "home_goals", "away_goals", "reg_home_goals", "reg_away_goals", "decided"}]}` for finished games of the last `config.RESULTS_EXPORT_DAYS = 120` days (current and previous season results logs + this run's records), newest first, unique by id. Written after latest.json in its own try (warning only). Review: missing/invalid → WARNING.

- [ ] Tests: content/order/window, dedupe, schema-valid when empty, failure does not fail the run. Commit `feat: publish recent results for bet settlement`.

### Task 3: Bet store and settlement (pure JS)

**Files:** `site/bets.js` (new ES module, no DOM), `site/tests/bets.test.js` (new).

**Produces:**
```js
// Bet = {id, placed_at (ISO), game_id, start_utc, home, away, market: "h2h"|"1x2"|"totals"|"spreads",
//        line: number|null, outcome: "home"|"away"|"draw"|"over"|"under", book, price, stake,
//        p_model: number|null, label: string}
export function loadBets(storage) -> Bet[]            // invalid entries dropped
export function saveBets(storage, bets) -> boolean
export function addBet(bets, bet) -> Bet[]             // validates; stake > 0, price > 1; new id
export function removeBet(bets, id) -> Bet[]
export function settle(bet, result) -> {status: "won"|"lost"|"push"|"void", profit}   // result from results.json
export function evaluate(bets, results, now) -> {rows: [{bet, status: "open"|"waiting"|"won"|"lost"|"push"|"void", profit}], totals: {n, n_settled, staked, profit, roi, hits, hit_rate}, byMarket: {...}}
export function exportJson(bets) -> string
export function importJson(text, bets) -> {bets, added, skipped, error}
```
- "open" = game not started; "waiting" = started, no result yet; roi = profit / staked over settled bets (push counts in staked with 0 profit; void excluded).
- [ ] Tests for every settlement rule in Global Constraints (incl. Betfair commission, totals push, SO game totals, spread both signs, 1x2 draw on OT game), Review Focus 4–5, ROI math. Commit `feat(site): bet store and settlement`.

### Task 4: Bets UI and "Vedot" tab

**Files:** `site/app.js`, `site/format.js`, `site/styles.css`, `site/index.html` if needed.

- [ ] "Lyö veto" button on each pick card (Ylikertoimet) and on each priced outcome in the match view odds table: opens an inline form (stake € prefilled with the Kelly € amount when bankroll is set, else empty; price prefilled with the shown price and editable, since the user may get a different price), save → `addBet`.
- [ ] New 5th bottom-nav tab "Vedot" (`#/vedot`): summary tiles (Panostettu €, Tulos €, ROI %, Osumat, avoimet/odottavat), list of open/waiting bets (delete allowed only while open), settled history (newest first, ✓/✗/push), per-market breakdown, buttons "Vie varmuuskopio" (download JSON) and "Tuo varmuuskopio" (file input) with result message, and a note "Vedot tallentuvat vain tähän selaimeen — ota varmuuskopio". Fetch `data/results.json` lazily (retry on error like Malli).
- [ ] 5 nav tabs fit at 375 px; check in the browser pane at 375 px and desktop; screenshots. Commit `feat(site): imaginary bets and Vedot tab`.

### Task 5: Docs

- [ ] Spec section 17 decisions for bets (storage, settlement, results.json); README section "Omat vedot". Commit `docs: phase 6`.
