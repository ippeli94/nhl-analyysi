# NHL API fixtures: recorded field paths

Recorded with `tools/record_fixtures.py --date 2026-11-10` on 2026-11-15. Dotted paths
below are relative to the payload root. `[]` marks a list. All paths were checked against
the recorded files; deviations from the plan are marked **DIFFERS**.

## Files

| File | Content |
|---|---|
| `schedule__2026-11-10.json` | Schedule week starting 2026-11-10 (finished games) |
| `schedule__2026-11-17.json` | Schedule week starting 2026-11-17 (upcoming games, all `FUT`) |
| `gamecenter__2026020286__play-by-play.json` | 133 KB. REG game |
| `gamecenter__2026020286__boxscore.json` | REG game |
| `gamecenter__2026020298__play-by-play.json` | 146 KB. SO game (goes through OT) |
| `gamecenter__2026020298__boxscore.json` | SO game |
| `gamecenter__2026020333__landing.json` | Upcoming game, `gameState` `FUT` |

## Recorded games

| Game id | Kind | Date (UTC start) | Teams | Final | `gameOutcome.lastPeriodType` |
|---|---|---|---|---|---|
| 2026020286 | REG | 2026-11-11T00:00Z (game date 2026-11-10) | COL (away, id 21) at TOR (home, id 10) | COL 6 - TOR 3 | `REG` |
| 2026020298 | SO | finished 2026-11-10 week | PIT (away, id 5) at SJS (home, id 28) | PIT 3 - SJS 2 | `SO` (periods 1-3 REG, 4 OT, 5 SO) |
| 2026020333 | upcoming | 2026-11-18T00:00Z (game date 2026-11-17) | LAK (away, id 26) at BOS (home, id 6) | not played | `FUT` |

Only the 2026-11-10 week was searched; both games were found in it (no step back needed).

## Schedule (`/schedule/{date}`)

| Field | Path | Notes |
|---|---|---|
| next week start | `nextStartDate` | string `"2026-11-17"`; also `previousStartDate` |
| day list | `gameWeek[]` | each has `date` (`"2026-11-10"`), `dayAbbrev`, `games[]` |
| game date | `gameWeek[].date` | the schedule game object has **no** game date field; use the parent day |
| game id | `gameWeek[].games[].id` | int |
| season | `gameWeek[].games[].season` | **int** `20262027` in the payload; ScheduledGame stores it as str |
| game type | `gameWeek[].games[].gameType` | int, `2` in these files |
| start | `gameWeek[].games[].startTimeUTC` | `"2026-11-11T00:00:00Z"`, ends with `Z` |
| state | `gameWeek[].games[].gameState` | `FUT` (upcoming), `OFF` (finished), also `PRE`, `LIVE`, `CRIT`, `FINAL` |
| outcome | `gameWeek[].games[].gameOutcome.lastPeriodType` | present only on finished games: `REG`, `OT`, `SO`. Used by the recorder |
| away team | `gameWeek[].games[].awayTeam` | see team fields below |
| home team | `gameWeek[].games[].homeTeam` | same shape |
| score | `...Team.score` | int, finished games only |
| team abbr | `...Team.abbrev` | `"COL"`, 3 letters |
| team name | `...Team.commonName.default` and `...Team.placeName.default` | `"Avalanche"` and `"Colorado"`. There is **no** `name` field; ScheduledGame's `name` is built as `placeName.default + " " + commonName.default` |
| logo | `...Team.logo` | `https://assets.nhle.com/logos/nhl/svg/COL_light.svg` |
| dark logo | `...Team.darkLogo` | `https://assets.nhle.com/logos/nhl/svg/COL_dark.svg`. **DIFFERS:** the plan name `logo_dark` maps to `darkLogo` |
| team id | `...Team.id` | int |

## Play-by-play (`/gamecenter/{id}/play-by-play`)

| Field | Path | Notes |
|---|---|---|
| final outcome | `gameOutcome.lastPeriodType` | `REG`, `OT` or `SO` |
| teams | `homeTeam`, `awayTeam` | `id`, `abbrev`, `score`, `sog`. Team id 10 is TOR, 28 SJS, 5 PIT, 21 COL |
| plays | `plays[]` | 320 and 359 events |
| event type | `plays[].typeDescKey` | `shot-on-goal`, `goal`, `missed-shot`, `blocked-shot`, `faceoff`, `hit`, `giveaway`, `takeaway`, `stoppage`, `penalty`, `delayed-penalty`, `period-start`, `period-end`, `game-end` |
| period | `plays[].periodDescriptor.number`, `plays[].periodDescriptor.periodType` | `periodType` is `REG`, `OT` (number 4) or `SO` (number 5). Shootout plays have `number` 5 |
| time | `plays[].timeInPeriod` | string `"MM:SS"`, e.g. `"03:33"`. Shootout time is `"00:00"` |
| time left | `plays[].timeRemaining` | string `"MM:SS"` |
| situation | `plays[].situationCode` | 4 chars `[away goalie][away skaters][home skaters][home goalie]`. See the hand-read example |
| defending side | `plays[].homeTeamDefendingSide` | **top-level on each play**, not in `details`. `"right"` or `"left"`. Present on all plays; changes per period |
| event owner | `plays[].details.eventOwnerTeamId` | team id of the shooter's team for `shot-on-goal`, `goal`, `missed-shot` |
| x | `plays[].details.xCoord` | int, raw NHL rink coordinate, present on all shot events in the fixtures (0 missing) |
| y | `plays[].details.yCoord` | int |
| zone | `plays[].details.zoneCode` | `O`, `D`, `N` from the event owner's view |
| shot type | `plays[].details.shotType` | `wrist`, `slap`, `snap`, `backhand`, `tip-in`, `deflected`, `wrap-around`, `bat`. **Absent on 21-28 % of shots** (34 of 121 in the REG game, 29 of 138 in the SO game) |
| shooter | `plays[].details.shootingPlayerId` | on `shot-on-goal`, `missed-shot`, `blocked-shot` |
| scorer | `plays[].details.scoringPlayerId` | `goal` only (it is absent on `shot-on-goal`) |
| assist 1 | `plays[].details.assist1PlayerId` | `goal` only, absent if unassisted |
| assist 2 | `plays[].details.assist2PlayerId` | `goal` only, absent if fewer than two assists |
| goalie | `plays[].details.goalieInNetId` | on `goal`, `shot-on-goal`, `missed-shot`. **Absent for an empty-net goal** (`situationCode` `1560` in the fixture) |
| blocker | `plays[].details.blockingPlayerId` | `blocked-shot` only, not used (blocked shots are excluded) |
| penalty | `plays[].details.typeCode`, `.descKey`, `.duration`, `.committedByPlayerId`, `.drawnByPlayerId` | `penalty` events, `duration` in minutes |

Event counts in the REG fixture: `shot-on-goal` 50, `goal` 9, `missed-shot` 28, `blocked-shot` 34. Shot events with coordinates: 121 (all shot events of types `shot-on-goal`, `goal`, `missed-shot`, `blocked-shot`).

### Shot direction (verified)

`homeTeamDefendingSide` gives the side the **home** team defends. The home team therefore attacks the opposite side, and the away team attacks `homeTeamDefendingSide`'s side.

Checked against all offensive-zone shots (`zoneCode == "O"`) of both games, using the event owner's team: `x` has the sign of the attacking side in 100 % of cases (85 of 85 in the REG game, 105 of 105 in the SO game, excluding the SO shootout).

Normalized coordinates (spec section 5.1, attacked net at +89): `x_norm = x * (+1 if the shooter attacks the +x side, else -1)`.

Some shots in the defensive zone (`zoneCode == "D"`) have `x` on the shooter's own defended side. That is expected, and a shot's `zoneCode` alone does not set direction.

### Hand-read example: power play `situationCode`

SO game `2026020298`, period 3, home SJS, away PIT:

| Clock | situationCode | Event |
|---|---|---|
| 02:56 | `1551` | `penalty`, team 28 (SJS), tripping |
| 02:56 | `1541` | `faceoff`, PIT (5) now has 5 skaters, SJS has 4 |
| 03:28 | `1541` | `goal`, team 5 (PIT), scorer 8482137, `xCoord` -82 |
| 04:01 | `1551` | `penalty` events (roughing, PIT and SJS). Still 5 v 5 at the penalty events |
| 04:01 | `1451` | `faceoff`, PIT (away, 5) down a skater: away 4, home 5. Not back to 5 v 5 |

The 1541 goal is a power-play goal for PIT: away 5 skaters, home 4. Index 1 is the away skater count and index 2 the home skater count. Goalies are 1 (in net) in both positions. A 1560 at 17:30 in the REG game (away goal, `goalieInNetId` absent) has home goalie `0`, which is the empty-net case.

## Boxscore (`/gamecenter/{id}/boxscore`)

| Field | Path | Notes |
|---|---|---|
| player rows | `playerByGameStats.{homeTeam,awayTeam}.{forwards,defense,goalies}[]` | forwards and defense are skater rows; goalies are goalie rows. 12 F, 6 D, 2 G per team |
| player id | `...[].playerId` | int |
| name | `...[].name.default` | e.g. `"Z. MacEwen"` |
| position | `...forwards[].position` (`C`, `L`, `R`), `...defense[].position` (`D`), `...goalies[].position` (`G`) | |
| goals | `...skaters[].goals` | int |
| assists | `...skaters[].assists` | int |
| points | `...skaters[].points` | int |
| shots | `...skaters[].sog` | int |
| time on ice | `...skaters[].toi`, `...goalies[].toi` | string `"MM:SS"`, e.g. `"18:45"`, `"59:50"`. `"00:00"` for a goalie who did not play |
| power-play goals | `...skaters[].powerPlayGoals` | **No power-play points field.** PP points must come from the play-by-play goal rows (`assist1PlayerId` / `assist2PlayerId`) |
| other skater fields | `plusMinus`, `pim`, `hits`, `blockedShots`, `shifts`, `giveaways`, `takeaways`, `faceoffWinningPctg` | |
| starter | `...goalies[].starter` | bool. `true` for the goalie who started. Present on finished games, both teams (one `true` per team in the fixtures) |
| shots against | `...goalies[].shotsAgainst` | int |
| saves | `...goalies[].saves` | int |
| goals against | `...goalies[].goalsAgainst` | int |
| save string | `...goalies[].saveShotsAgainst` | string `"22/27"` (saves/shots). Also `evenStrengthShotsAgainst`, `powerPlayShotsAgainst`, `shorthandedShotsAgainst` in the same format |
| save percentage | `...goalies[].savePctg` | float, may be absent when the goalie did not play |
| team totals | `homeTeam.score`, `homeTeam.sog`, `awayTeam.score`, `awayTeam.sog` | |
| outcome | `gameOutcome.lastPeriodType` | same as play-by-play |

Goalies who did not play have `toi` `"00:00"`, `starter` `false` and zero shots. The starter is the goalie with `starter: true` and non-zero `toi`.

## Landing (`/gamecenter/{id}/landing`), upcoming game

| Field | Path | Notes |
|---|---|---|
| state | `gameState` | `FUT` |
| start | `startTimeUTC` | `"2026-11-18T00:00:00Z"` |
| teams | `awayTeam`, `homeTeam` | same team fields as schedule, plus `record` (`"5-11-2"`) |
| season goalie leaders | `matchup.goalieComparison.{homeTeam,awayTeam}.leaders[]` | Season leaders of each team (`positionCode` `G`, `gamesPlayed`, `gaa`, `savePctg`). **These are not the probable starters.** |
| team goalie totals | `matchup.goalieComparison.{homeTeam,awayTeam}.teamTotals` | `record`, `gaa`, `savePctg`, `shutouts`, `gamesPlayed` |
| skater comparison | `matchup.skaterComparison.leaders[]` | not used in phase 1 |
| goalie season stats | `matchup.goalieSeasonStats.goalies[]` | not used in phase 1 |

### Starting goalies in the landing payload

**No.** The landing payload has no starter, probable or confirmed-goalie field. A search of the file for `starter`, `probable`, `confirm`, `lineup` and `projected` finds nothing. The only goalie data is the season leaders in `matchup.goalieComparison`, which does not name the starter. So the landing payload cannot serve as a `confirmed` source. This matches the plan (spec section 17.9, `sources/goalies.py` is out of scope).

## Differences from the plan

1. **DIFFERS:** `homeTeamDefendingSide` lives at `plays[].homeTeamDefendingSide`, not under `details`.
2. **DIFFERS:** The team name is not a single field. Use `commonName.default` and `placeName.default`.
3. **DIFFERS:** Dark logo is `darkLogo`, not `logo_dark`. The `logo_dark` name is the internal ScheduledGame key.
4. **DIFFERS:** Schedule `season` is an int. The code converts it to str.
5. **DIFFERS:** The boxscore has no power-play points. PP points are derived from play-by-play goal rows.
6. Shot type is missing on 21-28 % of shots. The mapper must treat a missing type as `other` (the `type` enum in the contract).
7. Empty-net goals have no `goalieInNetId` (`goalie` = null in CachedGame).
8. The schedule game object has no game date. Use `gameWeek[].date`.

# The Odds API v4

`odds__icehockey_nhl.json` is **käsin tehty — nauhoita uudelleen `tools/record_odds_fixture.py`:llä kun avain on saatavilla**.
It follows the documented v4 shape (`GET /v4/sports/icehockey_nhl/odds`); nothing in it was recorded
live. Event ids are placeholders (`fixture-1`...). Real ids are 32 hex characters and are never stored.
The recorder writes `odds__icehockey_nhl__live.json` (ids `live-{n}`), which
`test_live_fixture_parses_if_present` checks when it exists.

## Response shape

| Field | Path | Notes |
|---|---|---|
| events | root | JSON list; `[]` when nothing is on offer (costs nothing) |
| event id | `[].id` | 32 hex chars live; never persisted |
| start | `[].commence_time` | ISO UTC, `2026-11-18T00:00:00Z` |
| teams | `[].home_team`, `[].away_team` | full display names, e.g. `Montreal Canadiens` (no accent); mapped by `teams.abbr_from_odds_name` |
| bookmakers | `[].bookmakers[]` | `key`, `title`, `last_update`, `markets[]` |
| market | `markets[].key` | `h2h`, `totals`, `spreads` |
| h2h outcomes | `outcomes[]` | `name` = team name (or `Draw`), `price` decimal |
| totals outcomes | `outcomes[]` | `name` = `Over` / `Under`, `price`, `point` = line |
| spreads outcomes | `outcomes[]` | `name` = team name, `price`, `point` = that team's handicap (home -1.5 pairs with away +1.5) |

Request: the key goes in the `x-api-key` header; `bookmakers` (10 keys), `markets=h2h,totals,spreads`,
`oddsFormat=decimal`, `dateFormat=iso`, `commenceTimeFrom/To`.

## Usage headers (every response)

| Header | Meaning |
|---|---|
| `x-requests-remaining` | credits left in the period |
| `x-requests-used` | credits used in the period |
| `x-requests-last` | cost of this call (3 for 3 markets x 1 region) |

## Hand-built fixture content

Four events matching the schedule fixture: `2026020333` LAK @ BOS, `2026020335` EDM @ FLA,
`2026020344` BOS @ DET (coolbet BOS price about 10 % above Pinnacle's fair price, a value pick),
`2026020345` MTL @ NJD. Books `pinnacle`, `veikkaus_fi`, `coolbet`, `betfair_ex_eu`; totals 6.5
(Pinnacle also 6.0 on LAK @ BOS); spreads +-1.5; `marathonbet` on EDM @ FLA has a three-outcome h2h.

## Open questions (spec 13.4) to confirm from the first live recording

1. Which markets does Veikkaus (`veikkaus_fi`) return: h2h only, or totals and spreads too?
2. Do totals and spreads include overtime per book (regulation-only lines would differ)?
3. Which total and spread lines does Pinnacle offer (only 6.5 and +-1.5, or several)?
4. Does any bookmaker return a three-outcome `h2h` with `Draw`? (`--probe-3way` checks `h2h_3_way`.)
