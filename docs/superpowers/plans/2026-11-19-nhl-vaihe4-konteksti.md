# Vaihe 4 — konteksti, nostot, kokoonpano ja suorituskyky — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the remaining `later_phase` fields of `latest.json` (form, season_compare, h2h, insights, lineup_changes), add the lineup/injury and head-to-head λ factors behind an out-of-sample gate, publish `performance.json` with a "Malli" tab, and make the Pages deploy survive transient GitHub errors.

**Architecture:** Three new pure modules (`context.py`, `teamstats.py` + `insights.py`, `lineup.py`) compute from the cached records and the team-games frame that `run._predict_all` already builds; `predict.game_lambdas` gains one optional multiplier argument so production and backtest compose λ identically; `performance.py` derives every metric from the history logs (no cumulative state). All new outputs are optional: a failure sets that field to `null` and never fails the run (spec 4.4).

**Tech Stack:** Python 3.12, pandas/numpy, jsonschema, vanilla JS + inline SVG, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-15-nhl-analyysi-design.md` (sections 4.5, 6.6, 6.7, 7, 8, 9; decisions in section 17). Earlier plans: `docs/superpowers/plans/2026-11-18-nhl-vaihe3-pelaajat.md`.

## Global Constraints

- Probabilities are decimals 0–1; missing data is `null`, never an invented value (spec 4.5).
- UI copy is Finnish; JSON keys English. Decimal comma in the UI (existing `format.js` helpers).
- `schema_version` stays `1`: new fields replace `later_phase` (`null`) definitions with `anyOf: [null, <def>]`, so old readers keep working.
- Optional sources/fields never fail the run; nothing durable is written before `export.validate` passes (existing invariant in `run.run`).
- Schema errors confined to one new field of one game → that field becomes `null` for that game and the object is rebuilt and revalidated (same pattern as the existing players drop in `run.py`).
- All thresholds live in `pipeline/config.py` with the spec's values as defaults.
- Tests: `pytest -q` and `node --test site/tests` must pass; `ruff check .` clean; `python tools/review.py` exit 0 before merge.
- CRLF note for subagents: after every edit verify with `git diff --stat`; after every commit `git show --stat HEAD`.

## Review Focus

1. **Season opener / new team with < 10 games** → form, season_compare, insights degrade gracefully (fewer rows, `null` blocks, rules skipped by minimum sample) instead of raising. Test in Task 2 and Task 4.
2. **Traded player** (dressed for team A, then team B) → appears as `traded_away` for A and `acquired` for B, never double-counted in both lineups. Test in Task 5.
3. **ESPN name that matches no cached player, or two players with the same short name on one team** → entry kept for display with `player_id: null` and zero impact; never crashes, never attaches to the wrong player. Test in Task 5.
4. **Performance with zero resolved predictions or no odds rows** → `performance.json` still validates with `n: 0` and `null` metrics; UI shows "Otos pieni". Test in Task 7.
5. **Pick settlement edge cases**: totals push (exact line), shootout game for totals/spreads, Betfair commission → push returns stake, SO goal counted as books settle (final score). Test in Task 7.

---

### Task 1: Retry the Pages deploy

**Files:**
- Modify: `.github/workflows/pipeline.yml` (Deploy Pages step)
- Test: `tests/test_workflows.py`

Context: run on 2026-11-19 02:46 UTC failed in `actions/deploy-pages@v4` with "No artifacts named github-pages were found" right after a successful upload (transient GitHub API issue).

- [ ] **Step 1: Write the failing test** `test_pipeline_deploy_retries` in `tests/test_workflows.py`: load the YAML; assert the step with `id: deploy` has `continue-on-error: true`; assert a following step named `Retry deploy` uses `actions/deploy-pages@v4` and its `if` contains `steps.deploy.outcome == 'failure'`; assert the retry step's `if` also keeps `steps.run.outputs.ran == 'true' && steps.review.outputs.code != '2'`.
- [ ] **Step 2: Run** `pytest tests/test_workflows.py -q` → FAIL.
- [ ] **Step 3: Implement**: add `continue-on-error: true` to `Deploy Pages`; add `Retry deploy` step that first runs `sleep 60` (a separate `run` step `Wait before retry` with the same `if`) then `actions/deploy-pages@v4` with `id: deploy_retry`. A failure of the retry fails the job (no continue-on-error).
- [ ] **Step 4: Run** `pytest tests/test_workflows.py -q` → PASS.
- [ ] **Step 5: Commit** `ci: retry Pages deploy once after a transient failure`.

---

### Task 2: Form, season comparison and head-to-head (`context.py`)

**Files:**
- Create: `pipeline/context.py`, `tests/test_context.py`
- Modify: `pipeline/schema/latest.schema.json` (`form`, `season_compare`, `h2h` defs), `pipeline/predict.py` (`NULL_KEYS`), `pipeline/run.py` (`_predict_all` fills the three fields), `pipeline/config.py`

**Interfaces:**
- Consumes: `tg` = `features.team_games(...)` frame (columns `game_id, season, game_type, date, team, opp, is_home, xg_reg, score_reg, opp_score_reg, decided, won`); `records` = cached game dicts (`score`, `score_reg`, `decided`, `shots[{p,t,team,ev,strength}]`, `penalties[{p,t,team,min}]`).
- Produces:
  - `form(tg: pd.DataFrame, team: str, asof: date, n: int = config.FORM_GAMES) -> dict | None`
  - `special_teams(records: list[dict], team: str, season: str, asof: date) -> tuple[float | None, float | None]` (pp, pk)
  - `season_line(tg, records, team: str, season: str, asof: date) -> dict | None`
  - `season_compare(tg, records, team: str, season: str, asof: date) -> dict | None`
  - `h2h(tg, records, home: str, away: str, asof: date) -> dict | None`
  - `game_context(tg, records, home, away, season, asof) -> dict` returning `{"form": {...}|None, "season_compare": {...}|None, "h2h": {...}|None}` (each sub-dict keyed `home`/`away` where applicable; any exception inside one block → that block `None`, logged).

Definitions (decide nothing else):
- Only `game_type == 2` and rows with `date < asof`. `form` spans seasons (last n regular-season games).
- Outcome letters: `V` win, `H` regulation loss, `J` OT/SO loss. `seq` newest first; `last10` = `"W-L-OTL"` e.g. `"7-2-1"`; `streak` = letter + run length of the newest letter, e.g. `"V3"`.
- `gf`/`ga` = regulation goals + 1 for the OT winner (`decided == "OT"`); shootout adds nothing. `xgf_pct` = Σxg_reg / (Σxg_reg + Σopp xg_reg) rounded to 3.
- `season_line` keys: `gp, pts_pct, gf_pg, ga_pg, xgf_pct, pp, pk`; points W=2, OT/SO loss=1; `pts_pct = pts / (2·gp)`; `None` when `gp == 0`.
- PP opportunities = opponent penalties with `min in (2, 4, 5)` in that team's games (coincidentals not netted — record as decision 29); PP goals = shots with `ev == "goal"` and `strength == "pp"` for the team; `pp = pp_goals / opps`, `pk = 1 − pp_goals_against / times_short`; `None` when the denominator is 0.
- `season_compare` = `{"home": {"now": season_line(season), "last": season_line(previous season, asof=far future)}, "away": ...}`; `last` is `None` when the previous season is not cached.
- `h2h`: meetings of the two teams in either venue, game types 2 and 3, newest first, at most `config.H2H_MAX_GAMES = 8`, from the cached seasons (current + previous; spec says 3 seasons — only 2 are loaded, decision 30). Each `{"date", "home", "away", "score": "4-2" (home-away final), "xg": "3.1-2.4"}`; `summary` = `"{ABBR} {w}–{l} viimeisissä {n} kohtaamisessa"` with the leader first, or `"Tasan {w}–{l} viimeisissä {n} kohtaamisessa"`. `None` when there are no meetings.

- [ ] **Step 1: Write failing tests** in `tests/test_context.py` using `tests/synth.py` helpers (or small hand-built `tg`/records):
  - `test_form_letters_and_streak`: 10 games W,W,OTL,L,W… → `seq` newest first, `last10 == "6-3-1"`, `streak == "V2"`.
  - `test_form_none_without_games`: empty frame → `None`.
  - `test_form_short_history`: 3 games → `last10` counts sum to 3 (Review Focus 1).
  - `test_special_teams_counts`: 4 opponent minors, 1 PP goal → `pp == 0.25`; no penalties → `(None, None)`.
  - `test_season_line_points_pct`: 2 W, 1 OTL, 1 L → `pts_pct == 5/8`.
  - `test_h2h_order_limit_summary`: 10 meetings → 8 rows newest first; summary names leader; no meetings → `None`.
  - `test_game_context_isolates_failures`: monkeypatch `context.h2h` to raise → `h2h is None`, `form` still filled.
- [ ] **Step 2: Run** `pytest tests/test_context.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `pipeline/context.py`; add `FORM_GAMES = 10`, `H2H_MAX_GAMES = 8` to config.
- [ ] **Step 4: Schema + wiring**: add `$defs` `form_team`, `form`, `season_line`, `season_compare`, `h2h` matching the shapes above (`additionalProperties: false` not required); set the three game properties to `anyOf: [{"type":"null"}, {"$ref": ...}]`. Remove the three keys from `predict.NULL_KEYS`; in `run._predict_all` after `predict_game`, `obj.update(context.game_context(tg, records, home, away, season, day))` for upcoming games. Add to the existing players-style drop in `run.run`: errors matching `\$\.games\[\d+\]\.(form|season_compare|h2h|insights|lineup_changes)` null only that field for that game (generalise the existing regex block into a helper `_drop_invalid(games, errors) -> bool`).
- [ ] **Step 5: Run** `pytest -q` → PASS (update `tests/test_export.py`/`test_run.py` fixtures if they assert `form is None`).
- [ ] **Step 6: Commit** `feat: form, season comparison and head-to-head per game`.

---

### Task 3: Team statistics for insights (`teamstats.py`)

**Files:**
- Create: `pipeline/teamstats.py`, `tests/test_teamstats.py`
- Modify: `pipeline/config.py`

**Interfaces:**
- Consumes: `tg`, `records`, `context.form`, `context.season_line`, `context.special_teams` (Task 2).
- Produces:
  ```python
  @dataclass
  class TeamStats:
      gp: int
      period_xg_share: list[float | None]      # periods 1..3, team xG / (team+opp xG)
      led_after2: int; led_after2_won: int
      trailed_after2: int; trailed_after2_won: int
      scored_first: int; scored_first_won: int
      pp: float | None; pk: float | None
      pp_rank: int | None; pk_rank: int | None  # 1 = best, among teams with gp >= INSIGHT_MIN_GAMES
      pim_rank: int | None                       # penalties taken per game, 1 = most
      home_pts_pct: float | None; road_pts_pct: float | None
      pts_pct: float | None; pts_pct_last: float | None; gp_last: int
      xgf_pct_season: float | None; xgf_pct_last10: float | None
      luck_last10: float | None                  # (goals_reg − xg_reg) per game, last 10
      streak: str | None                         # "V5" / "H4" (from context.form)
      point_streak: int                          # consecutive games with ≥ 1 point
      played_yesterday: bool                     # filled per game, see below
      games_last_4d: int                         # incl. tonight
      road_run: int                              # consecutive away games incl. tonight (0 if home)
  def league_stats(tg, records, season: str, asof: date) -> dict[str, TeamStats]
  def schedule_load(tg, team: str, day: date, is_home: bool) -> tuple[bool, int, int]  # (b2b, games_last_4d, road_run)
  ```
- Period xG share uses shots in periods 1–3 with xG from `xg.shots_frame` + coefficients (same as `features._shot_sums`); score-state counts use goal shots (`ev == "goal"`) ordered by `(p, t)`, regular season of `season` only.

- [ ] **Step 1: Write failing tests**: `test_period_share`, `test_led_after_two_counts` (goal timeline 2–1 after P2, final 2–3 → `led_after2=1, won=0`), `test_first_goal`, `test_ranks_require_min_games` (team with 5 gp → `pp_rank is None`), `test_schedule_load_road_run` (three away games in a row incl. tonight → `road_run == 3`; tonight home → 0), `test_league_stats_empty` (no games → `{}`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; config `INSIGHT_MIN_GAMES = 10`.
- [ ] **Step 4: Run** `pytest tests/test_teamstats.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: league team statistics for insight rules`.

---

### Task 4: Insight rule engine (`insights.py`)

**Files:**
- Create: `pipeline/insights.py`, `tests/test_insights.py`
- Modify: `pipeline/config.py`, `pipeline/schema/latest.schema.json` (`insight` def), `pipeline/predict.py` (`NULL_KEYS`), `pipeline/run.py`

**Interfaces:**
- Consumes: `TeamStats`, `league_stats`, `schedule_load` (Task 3); the game object (`factors`, `goalies`, `odds.markets[].outcomes[].flag`, `h2h`, `lineup_changes`, `home.abbr/name`, `away...`).
- Produces: `game_insights(game: dict, stats: dict[str, TeamStats]) -> list[dict]`; each `{"id": str, "team": str | None, "severity": 1|2|3, "text": str}`; at most `INSIGHT_MAX = 6`, at most `INSIGHT_MAX_PER_TEAM = 3`, sorted by severity desc then rule order below; `run` fills `game["insights"]` for upcoming games after odds and lineup are attached (so `model_vs_market` and `key_absence` see them). Exception → `insights = None`.

Rules (spec section 7 thresholds; severity in brackets; team name = `teams.display_name` short form as in UI, e.g. "Carolina"; numbers with decimal comma and `%`; sample size appended as "(n ottelua)" when n < 20):

| id | sev | notes |
|---|---|---|
| `key_absence` | 3 | from `lineup_changes` kind out/doubtful/traded_away with `impact_attack ≤ −0.02` |
| `model_vs_market` | 3 | any h2h/1x2 outcome `flag == "suspicious"`; team None |
| `b2b` | 2 | team played yesterday and opponent did not |
| `goalie_edge` | 2 | goalie factor ratio home/away ≥ 1.04 or ≤ 1/1.04 |
| `return_or_trade` | 2 | kind returning/acquired with `impact_attack ≥ 0.02` |
| `streak` | 2 | win or loss streak ≥ 4, or point streak ≥ 6 |
| `pp_vs_pk` | 2 | pp_rank ≤ 8 and opp pk_rank ≥ 25 (32 teams: bottom 8) |
| `third_period_collapse` | 2 | led_after2 ≥ 6 and won share < 0.65 |
| `period_matchup` | 1 | team's best period (share ≥ 0.55) equals opponent's worst (share ≤ 0.45) |
| `period_swing` | 1 | max − min period share ≥ 0.10 (suppresses `period_strength`/`period_weakness` for that team) |
| `period_strength` | 1 | best period share ≥ 0.55 |
| `period_weakness` | 1 | worst period share ≤ 0.45 |
| `comeback` | 1 | trailed_after2 ≥ 6 and won share ≥ 0.30 |
| `first_goal` | 1 | scored_first ≥ 10 and won share ≥ 0.80 or ≤ 0.55 |
| `penalty_prone` | 1 | opponent pim_rank ≤ 5 and own pp_rank ≤ 10 |
| `schedule_load` | 1 | games_last_4d ≥ 3 or road_run ≥ 4 |
| `form_vs_season` | 1 | |xgf_pct_last10 − xgf_pct_season| ≥ 0.05 |
| `luck` | 1 | |luck_last10| ≥ 0.6 |
| `vs_last_season` | 1 | |pts_pct − pts_pct_last| ≥ 0.10 and gp ≥ 15 |
| `h2h_dominance` | 1 | ≥ 5 wins in last 6 meetings (from `game["h2h"]`), text adds "(pieni otos)" |
| `home_road_split` | 1 | |home − road pts_pct| ≥ 0.20, each side ≥ 5 games |

`goalie_unconfirmed` is **not** implemented: every goalie is `projected` (decision 9), so it would fire on every game — record as decision 31. All minimum samples 10 games unless the table says otherwise. Example copy (exact form for the two most visible rules): `"Seattle pelaa toisena iltana peräkkäin, Carolina levänneenä."`, `"Malli ja markkina ovat eri mieltä yli 10 prosenttiyksikköä — tarkista poissaolot."`; other rules follow the spec section 7 example sentences with live numbers.

- [ ] **Step 1: Write failing tests**: one positive and one negative test per rule for `b2b`, `streak`, `period_swing` (and that it suppresses `period_strength`), `third_period_collapse`, `pp_vs_pk`, `model_vs_market`, `key_absence`, `h2h_dominance`; `test_caps_and_order` (10 firing rules → 6 returned, ≤ 3 per team, severities non-increasing); `test_min_sample` (team with gp 5 → no stats-based rule fires, Review Focus 1); `test_texts_use_decimal_comma`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** with config constants `INSIGHT_MAX = 6`, `INSIGHT_MAX_PER_TEAM = 3` and one constant per threshold (names `INSIGHT_<RULE>_...`).
- [ ] **Step 4: Schema + wiring**: `insight` def `{id: string, team: string|null, severity: 1..3, text: string minLength 1}`; `insights` property `anyOf [null, array maxItems 6]`; remove from `NULL_KEYS`; in `run.run` compute `stats = teamstats.league_stats(tg, records, season, today)` once (inside try → `{}` on failure) and call `game_insights` per upcoming game after odds and lineup attach. `_predict_all` must return `tg` for reuse (change its return to `(games, tg)`; update callers/tests).
- [ ] **Step 5: Run** `pytest -q` → PASS.
- [ ] **Step 6: Commit** `feat: rule-based insights per game`.

---

### Task 5: Lineup changes and lineup multipliers (`lineup.py`)

**Files:**
- Create: `pipeline/lineup.py`, `tests/test_lineup.py`
- Modify: `pipeline/sources/injuries.py` (return value kept, docstring), `pipeline/run.py` (store parsed injuries, build changes), `pipeline/schema/latest.schema.json` (`lineup_change` def), `pipeline/predict.py` (`NULL_KEYS`), `pipeline/config.py`, `pipeline/teams.py` (ESPN display-name → abbr via existing `TEAM_NAMES`)

**Interfaces:**
- Consumes: `pg` = `players.player_games(records, coefs)` (columns `game_id, season, game_type, date, team, player, name, pos, grp, toi, g, a, pts, ixg, team_goals`), `tg` (`xg_reg` per `(game_id, team)`), `ratings.weights(frame, asof, p, season)`, `players.project_lineup`.
- Produces:
  ```python
  def parse_espn(raw: list[dict]) -> dict[str, list[dict]]
      # abbr -> [{"name": displayName, "short": shortName, "status": "out"|"doubtful", "detail": shortComment, "since": "YYYY-MM-DD"|None}]
      # ESPN status "Day-To-Day" -> doubtful; "Out", "Injured Reserve", "Suspension", anything else -> out; unknown team -> skipped
  @dataclass
  class Contrib:            # one player's standing in one team
      player: int; name: str | None; grp: str   # "F" | "D"
      share: float          # attack share (spec 6.6 formula)
      presence: float       # time-weighted share of team games dressed
      toi_share: float      # mean toi / 3600
  def contributions(pg, tg, team: str, asof: date, p: ModelParams, season: str) -> dict[int, Contrib]
  def expectations(contrib: dict[int, Contrib], lineup: list[Slot], injuries: list[dict], pg, team: str, asof: date) -> tuple[dict[int, float], list[dict]]
      # (player -> expectation 0 | 0.5 | 1, change entries without impacts)
  def multipliers(contrib, expect: dict[int, float], rho: float) -> tuple[float, float, dict[int, tuple[float, float]]]
      # (m_att, m_def, per-player (impact_attack, impact_defense)); both clamped to config.LINEUP_CLAMP = (0.85, 1.15)
  def team_lineup(pg, tg, team, asof, p, season, lineup, injuries, rho) -> dict
      # {"m_att", "m_def", "changes": [lineup_change...]} ; changes sorted by |impact_attack| desc, at most LINEUP_MAX_CHANGES = 8 per team
  ```

Definitions:
- `share_i = 0.5·Σw·ixg_i / Σw·team_xg + 0.5·Σw·pts_i / (Σw·team_goals·PPG)` over the team games player i dressed in, w = `ratings.weights` of those team-game rows; `PPG` = league Σpts/Σgoals from `pg` before `asof` (fallback `config.LINEUP_PPG_FALLBACK = 2.7`).
- `presence_i` = Σw over this team's games i dressed in / Σw over all this team's games. Universe = players with presence > 0 for this team in the current season plus `lineup` players.
- `expectation`: 0 for ESPN `out` (name match), 0.5 for ESPN `doubtful`, otherwise 1 if in `lineup` else 0. ESPN match: normalised (`unicodedata` NFKD, ascii, lower, strip dots) `shortName` vs cached `name` within the team; zero or ≥ 2 matches → entry kept with `player_id: None` and zero impact (Review Focus 3). A matched ESPN-out player is removed from the lineup the players block uses (Task 5 wiring), so player probabilities skip known absentees.
- Kinds: `out` (ESPN out, or inferred: dressed ≥ 8 of team's last 10 and none of last 2 → `source "päätelty"`), `doubtful` (ESPN DTD), `returning` (in lineup, dressed ≤ 5 of team's last 10 before the last game), `acquired` (in lineup, latest game for this team but earlier games in season for another team), `traded_away` (dressed for this team this season, latest game for another team). `source`: `"espn"`, `"päätelty"`, or `"nhl"` (trades/returns from cached box scores).
- Acquired player: presence on new team computed only from new-team games (≈ 0) and share from his own games regardless of team (spec 6.6: old team's numbers) — `contributions` therefore computes share over all the player's games but presence over this team's games.
- `m_att = 1 + Σ share_i·(1 − ρ)·(expect_i − presence_i)`; `m_def = 1 + Σ κ_i·toi_share_i·(presence_i − expect_i)`, `κ = 0.5` D, `0.25` F (`config.LINEUP_KAPPA = {"D": 0.5, "F": 0.25}`). `impact_attack_i = share_i·(1 − ρ)·(expect_i − presence_i)`, analogously defense. Default `config.LINEUP_RHO = 0.45`.
- Change entry: `{"team", "player", "player_id", "kind", "source", "detail", "since", "impact_attack", "impact_defense"}` (impacts rounded to 3). Include entries with |impact| ≥ 0.005 or any ESPN entry.

- [ ] **Step 1: Write failing tests** (small synthetic `pg`): `test_star_absent_full_reduction` (presence 1, expect 0 → impact ≈ −share·0.55), `test_long_absence_near_zero` (presence ≈ 0.05 → |impact| < 0.01), `test_returning_adds`, `test_traded_player_once` (A gets `traded_away`, B gets `acquired`, each once; Review Focus 2), `test_espn_ambiguous_name` (two "J. Smith" on team → `player_id None`, impact 0; Review Focus 3), `test_espn_unknown_team_skipped`, `test_clamp` (m_att never < 0.85), `test_inferred_out` (8 of last 10, missed last 2 → kind out, source päätelty).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `pipeline/lineup.py`.
- [ ] **Step 4: Wiring (display only in this task)**: `optional_source("injuries", ...)` keeps its status logic; additionally keep the returned list (parsed via `parse_espn`) in a local `injuries_by_team` (`{}` on failure/offline). Because `_predict_all` runs before the injuries fetch today, move the injuries fetch before `_predict_all` and pass `injuries_by_team` in. In `_predict_all` compute `team_lineup` for both teams (inside try; failure → `lineup_changes = None`, multipliers 1.0) and set `game["lineup_changes"] = home_changes + away_changes`; remove matched ESPN-out ids from `pin.lineups` before `predict_game`. Multipliers are **not** applied yet (Task 6). Schema `lineup_change` def + property `anyOf [null, array]`; remove from `NULL_KEYS`.
- [ ] **Step 5: Run** `pytest -q` → PASS.
- [ ] **Step 6: Commit** `feat: lineup changes from box scores and ESPN injuries`.

---

### Task 6: Lineup and head-to-head factors behind a gate

**Files:**
- Create: `pipeline/lineup_backtest.py`, `tests/test_lineup_backtest.py`, `model/lineup_params.json` (generated), `reports/lineup_backtest.md` (generated)
- Modify: `pipeline/predict.py` (`game_lambdas`, `predict_game` factors), `pipeline/backtest.py` (`run_backtest` accepts a multiplier hook), `pipeline/config.py` (`LineupParams`, `load_lineup_params`, `MODEL_VERSION` only if enabled), `pipeline/run.py`, `pipeline/context.py` (`h2h_mult`)

**Interfaces:**
- Consumes: `lineup.contributions/expectations/multipliers` (Task 5), `context` meeting selection (Task 2).
- Produces:
  ```python
  @dataclass(frozen=True)
  class Mults:              # pipeline/predict.py
      att_h: float = 1.0; def_h: float = 1.0; att_a: float = 1.0; def_a: float = 1.0
      h2h_h: float = 1.0; h2h_a: float = 1.0
  def game_lambdas(..., p: ModelParams, mults: Mults | None = None) -> dict
      # lineup_h = att_h * def_a ; lineup_a = att_a * def_h ; both enter the clamped product
      # with h2h_h / h2h_a; returned dict gains "lineup_h", "lineup_a", "h2h_h", "h2h_a"
  def h2h_mult(tg, r: Ratings, team: str, opp: str, asof: date) -> float   # pipeline/context.py
  @dataclass(frozen=True)
  class LineupParams:       # pipeline/config.py
      rho: float = 0.45; use_lineup: bool = False; use_h2h: bool = False
  def load_lineup_params() -> LineupParams   # missing file -> defaults (both off)
  def run_backtest(..., mults_fn: Callable[[pd.Timestamp, int, str, str], Mults] | None = None)  # backtest.py
  ```
- `h2h_mult`: meetings as in Task 2 (≤ 8, before asof); `ratio = Σ actual goals_reg of team / Σ ratings.base_lambda(r, team, opp)` over those meetings; `m = 1 + (ratio − 1)·n/(n + 20)`, clamped `config.H2H_CLAMP = (0.97, 1.03)`; no meetings → 1.0.
- Factors: when `use_lineup` add `_factor("lineup", "Poissaolot ja kaupat", ...)`, when `use_h2h` add `_factor("h2h", "Keskinäiset", ...)`; both go through `_scaled` so displayed factors multiply to the applied value. Disabled factors are omitted (not shown as 1.00).

Gate procedure (`python -m pipeline.lineup_backtest`):
1. Tune `rho` ∈ {0.30, 0.45, 0.60} on season `20242025` in **actual** mode (expectation 1 for players who dressed in that game, 0 otherwise), goalie mode `projected`, minimising winner log loss.
2. On season `20252026` compare, goalie mode `projected`: baseline (no mults) vs `lineup` in **projected** mode (expectation from `players.project_lineup` of the last cached game; no ESPN history exists) vs `lineup+h2h`.
3. `use_lineup = True` only if projected-mode lineup beats baseline on both winner log loss and regulation 1X2 log loss (`backtest.logloss_1x2` with fitted `draw_kappa` from params). `use_h2h = True` only if adding h2h on top of the chosen variant improves winner log loss again.
4. Write `model/lineup_params.json` `{"rho", "use_lineup", "use_h2h", "gate": {"season", "metrics": {...}, "passed_lineup", "passed_h2h"}}` and `reports/lineup_backtest.md` (≤ 40 lines, Finnish).
5. If either flag is enabled, bump `config.MODEL_VERSION` to `"1.2.0"` (draw monitor restarts by design, decision 26) and refresh `reports/backtest.md` numbers only if the existing backtest command supports it; otherwise note it.

- [ ] **Step 1: Write failing tests**: `test_game_lambdas_default_unchanged` (mults None equals `Mults()` and equals pre-change values), `test_mults_enter_clamp` (att_h 1.3 → total clamp at 1.25), `test_h2h_mult_bounds` (ratio 3 with n 8 → 1.03; no meetings → 1.0), `test_factors_multiply_to_applied` with lineup on, `test_load_lineup_params_missing_file`, `test_gate_decision` (pure function `decide(metrics) -> (bool, bool)` with crafted metrics).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** code paths; production `_predict_all` builds `Mults` from Task 5 multipliers and `h2h_mult` gated by `load_lineup_params()`.
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Run the gate** in the background (long): `python -m pipeline.lineup_backtest` → writes the params and report. Record the outcome in the ledger.
- [ ] **Step 6: Commit** `model: lineup and h2h factors with out-of-sample gate` (include generated files).

---

### Task 7: Performance metrics (`performance.py` → `performance.json`)

**Files:**
- Create: `pipeline/performance.py`, `pipeline/schema/performance.schema.json`, `tests/test_performance.py`
- Modify: `pipeline/run.py` (write after `latest.json`, inside its own try), `pipeline/export.py` (`write_performance`, `validate_performance`), `tools/review.py` (new `check_perf`: warn if file missing/invalid)

**Interfaces:**
- Consumes: `tracking.read_log("predictions"|"results"|"odds"|"players", season)`, `tracking.official(rows)`, `tracking.player_outcomes(rows, records)`, `metrics.score`, `metrics.calibration_bins`, `model/backtest_20252026.json`.
- Produces: `build_performance(pred: list[dict], results: list[dict], odds: list[dict], player_rows: pd.DataFrame | None, now: datetime, backtest: dict | None) -> dict`; `settle(pick: dict, result: dict) -> float | None` (profit per 1 unit stake; `None` = unsettleable).

Shape:
```jsonc
{"generated_at": "…Z", "model_version": "1.x.y",
 "windows": {"30d": W, "season": W},
 "recent": [{"date","home","away","p_home","score","won_home","hit"}],   // ≤ 50, newest first
 "backtest": {"label": "Backtest 2025–26", "n", "brier", "brier_base", "logloss", "logloss_base"} | null}
W = {"n", "small_sample", "brier", "logloss", "brier_base", "logloss_base",
     "n_market", "brier_market", "logloss_market", "accuracy", "accuracy_market",
     "calibration": [...], "total_mae",
     "picks": {"all": P, "no_flag": P, "veikkaus": P}, "clv_mean", "clv_n",
     "players": {"n", "brier", "brier_base"} | null}
P = {"n", "hits", "hit_rate", "roi"}   // roi = Σprofit / n, null when n = 0
```
- Official prediction = `tracking.official` (last row before start); joined to results by `game_id`. 30d window = result date ≥ now − 30 d. `small_sample = n < 100`. Base = league home-win rate of results resolved before each game is overkill — use the season's resolved home-win rate (document as decision 32). Market = `row["market"]` h2h novig home when present.
- `settle`: h2h by `home_won`; 1x2 by regulation goals; totals vs `home_goals + away_goals` (final, includes SO winner goal as books settle), exact line → 0.0; spreads ±1.5 vs final margin; profit = price − 1 on win (Betfair: `(price − 1)·0.95`), −1 on loss. `veikkaus` group uses pick `veikkaus.price` and only picks where `veikkaus.value` is true.
- CLV per pick: closing ref = last odds-log row for the game with `ts < start_utc`; p_close from its `ref` for the pick's market/line/outcome; `clv = price·p_close − 1`; skipped when missing.

- [ ] **Step 1: Write failing tests**: `test_empty_logs_validate` (Review Focus 4: all `n == 0`, metrics `null`, schema valid), `test_settle_totals_push`, `test_settle_shootout_totals_counts_final`, `test_settle_betfair_commission`, `test_settle_spread_minus_one_and_half`, `test_official_row_only` (two rows, one after start → the earlier is used), `test_clv_uses_last_row_before_start`, `test_small_sample_flag`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** module + schema; `run` writes `site/data/performance.json` atomically after `latest.json` (failure logged, never fails the run); review `check_perf` adds a warning (severity like other optional checks) when invalid.
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat: performance metrics from history logs`.

---

### Task 8: UI — match sections, card badge and Malli tab

**Files:**
- Modify: `site/index.html` (4th nav tab "Malli"), `site/app.js`, `site/format.js`, `site/styles.css`, `site/tests/format.test.js`

Use the `ui-ux-pro-max` skill for consistency with the existing dark theme; reuse existing card/section components and CSS tokens. No new libraries; charts inline SVG.

**Interfaces:**
- Consumes: `game.insights`, `game.form`, `game.season_compare`, `game.h2h`, `game.lineup_changes`, factors `lineup`/`h2h`; `data/performance.json`.
- Produces (format.js, tested): `formSeq(seq: string) -> [{letter, cls}]`, `pct1(x) -> "56,0 %"`, `signedPct(x) -> "+2,7 %"`, `kindLabel(kind) -> "Poissa" | "Epävarma" | "Palaa" | "Hankittu" | "Kaupattu pois"`, `sourceLabel(src) -> "ESPN" | "Päätelty" | "NHL"`.

Match view order (spec 9.2): header · xG ja tulos · Mistä ennuste koostuu (now incl. lineup/h2h when present) · **Nostot** (severity dot 1–3, text) · Erät · Pelaajat · **Maalivahdit ja poissaolot** (lineup_changes rows: player, kind chip, source chip, impact %) · **Vire** (two columns: seq chips V/H/J, last10, GF–GA, xG %, streak) · **Kausivertailu** (table now vs last: P %, GF/GP, GA/GP, xG %, YV %, AV %) · **Keskinäiset** (rows date, score, xG + summary) · Kertoimet. Each new section hidden when its field is `null`; empty array → "Ei nostoja" / "Ei tiedossa olevia poissaoloja".

Card: first insight with severity ≥ 2 shown as one badge line (truncate with ellipsis), next to existing ★ ylikerroin badge (max two badges, spec 9.1).

Malli tab: fetch `data/performance.json` lazily on first open; failure → "Suorituskykydataa ei saatu ladattua". Window toggle 30 pv / Kausi. Tiles: Brier (malli vs perustaso vs markkina), log loss, osumatarkkuus vs markkina, ylikerroinpoiminnat ROI (Kaikki / Ilman varoitusta / Vain Veikkaus) with n, CLV ka., pelaajat Brier. Calibration chart inline SVG (bins vs diagonal). Recent list (ennuste → tulos, ✓/✗). Backtest block labelled "Backtest 2025–26", visually separate. `small_sample` → banner "Otos pieni — luvut eivät vielä kerro paljoa".

- [ ] **Step 1: Write failing tests** for the five format helpers in `site/tests/format.test.js`.
- [ ] **Step 2: Run** `node --test site/tests` → FAIL.
- [ ] **Step 3: Implement** helpers and views.
- [ ] **Step 4: Run** `node --test site/tests` → PASS; generate offline data (`python -m pipeline.run --offline tests/fixtures/... ` as in earlier phases) and check the page in the browser pane at mobile width (375 px): every new section renders, no horizontal scroll, Malli tab with empty performance renders the small-sample banner.
- [ ] **Step 5: Commit** `feat(site): insights, form, h2h, lineup and Malli tab`.

---

### Task 9: Decisions, README and final review

**Files:**
- Modify: spec section 17 (decisions 29–33), `README.md`

- [ ] **Step 1:** Add decisions: 29 PP opportunities approximation; 30 h2h uses the two cached seasons; 31 `goalie_unconfirmed` omitted; 32 performance base rate = season home-win rate; 33 lineup/h2h gate outcome and `rho` (from `model/lineup_params.json`), MODEL_VERSION if bumped.
- [ ] **Step 2:** README: new sections and the Malli tab, how to re-run `pipeline.lineup_backtest`.
- [ ] **Step 3:** Run `ruff check .`, `pytest -q`, `node --test site/tests`, `python tools/review.py` → all clean (review exit 0).
- [ ] **Step 4: Commit** `docs: phase 4 decisions and README`.
