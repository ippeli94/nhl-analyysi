"""Pipeline orchestration (spec 4.1, 4.3, 4.4): fetch, cache, model, predict, track, export.

CLI: python -m pipeline.run [--offline DIR] [--now ISO] [--extra-if-early] [--odds]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from pipeline import (
    adjust,
    cache,
    config,
    context,
    export,
    features,
    insights,
    lineup,
    performance,
    players,
    predict,
    ratings,
    teamstats,
    tracking,
    value,
    xg,
)
from pipeline.sources import injuries, nhl, odds
from pipeline.sources.nhl import ScheduledGame, SourceError

logger = logging.getLogger(__name__)

NHL_TZ = ZoneInfo("America/New_York")
EXTRA_RUN_CUTOFF_UTC = (15, 17)  # the next scheduled run after the 12:17 UTC extra run
SCHEDULE_LOOKBACK_DAYS = 7
SCHEDULE_WEEK_DAYS = 7
OFFLINE_NOW_UTC = (15, 17)
STARTED_STATES = ("LIVE", "CRIT")
GATE_MESSAGE = "Backtest-ehto ei ole täyttynyt — aja python -m pipeline.backtest"
INJURIES_NOTE = "ESPN ei vastannut — ei vaikuta vaiheen 1 ennusteeseen"
XG_COEFS = ("model", "xg_coefs.json")
XG_COEFS_STUB = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "xg_coefs_stub.json"
LAST_GAMES = ("data", "state", "last_games.json")
SOURCES_FILE = ("data", "state", "sources.json")
SCHEDULE_IDS = ("data", "state", "schedule_ids.json")
ODDS_STATE = ("data", "state", "odds.json")


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def nhl_today(now: datetime) -> str:
    """The NHL date (America/New_York) of `now`."""
    return now.astimezone(NHL_TZ).date().isoformat()


def is_started(game: ScheduledGame, now: datetime) -> bool:
    return game["state"] in STARTED_STATES or _parse_utc(game["start_utc"]) <= now


def _future_date(text: str | None, today: str) -> str | None:
    """`text` when it is a YYYY-MM-DD date after `today`, else None."""
    try:
        parsed = date.fromisoformat(text) if isinstance(text, str) and len(text) == 10 else None
    except ValueError:
        return None
    return text if parsed is not None and text > today else None


def select_games(
    week: list[ScheduledGame], today: str, season_start: str | None = None
) -> tuple[list[ScheduledGame], str | None]:
    """Unfinished games dated today or tomorrow; when none, the next date with a game.

    With no game later in the fetched weeks (offseason, preseason), the next date is the
    schedule's regular-season start when that is still ahead (decision 37)."""
    day = date.fromisoformat(today)
    window = {today, (day + timedelta(days=1)).isoformat()}
    open_games = [g for g in week if not cache.is_finished(g)]
    chosen = [g for g in open_games if g["nhl_date"] in window]
    if chosen:
        return chosen, None
    later = sorted(g["nhl_date"] for g in open_games if g["nhl_date"] > today)
    return [], later[0] if later else _future_date(season_start, today)


def needs_extra_run(games: list[ScheduledGame], now: datetime) -> bool:
    """True when an upcoming game starts before today's 15:17 UTC scheduled run."""
    hour, minute = EXTRA_RUN_CUTOFF_UTC
    utc = now.astimezone(UTC)
    cutoff = utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return any(
        not cache.is_finished(g) and utc < _parse_utc(g["start_utc"]) < cutoff for g in games
    )


def optional_source(
    source_id: str,
    fn: Callable[[], object],
    state: dict,
    now: datetime,
    failed_status: str = "failed",
    note_on_fail: str | None = None,
) -> object | None:
    """Run an optional source. Never raises; records the outcome in state[source_id]."""
    previous = state.get(source_id) or {}
    stamp = _iso(now)
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - an optional source must never abort the run
        logger.warning("optional source %s failed: %s", source_id, exc)
        state[source_id] = {
            "status": failed_status,
            "fetched_at": stamp,
            "last_ok_at": previous.get("last_ok_at"),
            "note": note_on_fail or str(exc),
        }
        return None
    state[source_id] = {"status": "ok", "fetched_at": stamp, "last_ok_at": stamp, "note": None}
    return result


def _fixture_week_starts() -> list[date]:
    found = []
    for f in config.OFFLINE_DIR.glob("schedule__*.json"):
        try:
            found.append(date.fromisoformat(f.stem.removeprefix("schedule__")))
        except ValueError:
            continue
    return sorted(found)


def _week_requests(today: date) -> list[str]:
    """Schedule week start dates to request: weeks covering today - 7 days and today.

    Offline, a wanted date maps to the recorded fixture week that contains it, and a date
    with no recorded week is skipped.
    """
    wanted = [today - timedelta(days=SCHEDULE_LOOKBACK_DAYS), today]
    if config.OFFLINE_DIR is None:
        return [d.isoformat() for d in wanted]
    starts = _fixture_week_starts()
    span = timedelta(days=SCHEDULE_WEEK_DAYS)
    out: list[str] = []
    for d in wanted:
        match = [s for s in starts if s <= d < s + span]
        if match and match[-1].isoformat() not in out:
            out.append(match[-1].isoformat())
    return out


def _fetch_week(today: date) -> tuple[list[ScheduledGame], str | None]:
    """Games of the requested weeks and the schedule's regular-season start date (latest)."""
    starts = _week_requests(today)
    if not starts:
        raise SourceError("no schedule available for the requested dates")
    games: dict[int, ScheduledGame] = {}
    season_starts: list[str] = []
    for start in starts:
        meta: dict = {}
        week, _ = nhl.fetch_schedule_week(start, meta)
        for g in week:
            games.setdefault(g["id"], g)
        if meta.get("regular_season_start"):
            season_starts.append(meta["regular_season_start"])
    return list(games.values()), max(season_starts, default=None)


def _result_record(rec: dict) -> dict:
    return {
        "game_id": rec["id"],
        "date": rec["date"],
        "home": rec["home"]["abbr"],
        "away": rec["away"]["abbr"],
        "home_goals": rec["score"]["home"],
        "away_goals": rec["score"]["away"],
        "reg_home_goals": rec["score_reg"]["home"],
        "reg_away_goals": rec["score_reg"]["away"],
        "decided": rec["decided"],
        "home_won": rec["score"]["home"] > rec["score"]["away"],
    }


def _season_before(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}{start + 1}"


def _current_season(selected: list[ScheduledGame], week: list[ScheduledGame]) -> str:
    if selected:
        return selected[0]["season"]
    folder = config.path(*cache.GAMES_DIR)
    seasons = sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    seasons = [name for name in seasons if re.fullmatch(r"\d{8}", name)]
    if seasons:
        return seasons[-1]
    if week:
        return week[0]["season"]
    raise SourceError("cannot determine the current season")


def _read_json(parts: tuple[str, ...], default):
    f = config.path(*parts)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default


def _write_json(parts: tuple[str, ...], obj) -> None:
    f = config.path(*parts)
    f.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    tmp = f.with_name(f.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, f)


def _public(game_obj: dict) -> dict:
    return {k: v for k, v in game_obj.items() if not k.startswith("_")}


def _github_output(ran: bool) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"ran={'true' if ran else 'false'}\n")


def _default_now(offline: Path | None) -> datetime:
    if offline is not None:
        starts = sorted(offline.glob("schedule__*.json"))
        if starts:
            try:
                day = date.fromisoformat(starts[-1].stem.removeprefix("schedule__"))
            except ValueError:
                day = None
            if day is not None:
                hour, minute = OFFLINE_NOW_UTC
                return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    return datetime.now(UTC)


def _load_coefs() -> dict:
    coefs_file = config.path(*XG_COEFS)
    if not coefs_file.exists() and config.OFFLINE_DIR is not None:
        coefs_file = XG_COEFS_STUB
    return xg.load_coefs(coefs_file)


def players_enabled(offline: Path | None) -> bool:
    """Offline runs always fill players; online runs only when the player gate has passed."""
    if offline is not None:
        return True
    return (config.load_player_gate() or {}).get("passed") is True


def _player_context(records: list[dict], coefs: dict, p, today: date, season: str):
    """(player_games, rates, params), or None (with a warning) when they cannot be built."""
    try:
        pp = config.load_player_params()
        pg = players.player_games(records, coefs)
        rates = players.player_rates(pg, today, p, pp, season)
        return pg, rates, pp
    except Exception as exc:  # noqa: BLE001 - players must never fail the run
        logger.warning("players disabled for this run: %s", type(exc).__name__)
        return None


def _player_inputs(ctx, game: ScheduledGame, season: str) -> players.PlayerInputs | None:
    pg, rates, pp = ctx
    day = date.fromisoformat(game["nhl_date"])
    try:
        lineups = {
            t: players.project_lineup(pg, t, day, season)
            for t in (game["home"]["abbr"], game["away"]["abbr"])
        }
        slots = [s for lineup in lineups.values() for s in lineup]
        baseline = players.baseline_probs(pg, day, season, slots)
        return players.PlayerInputs(rates, lineups, baseline, pp)
    except Exception as exc:  # noqa: BLE001 - one game's players never fail the run
        logger.warning("player inputs for game %s failed: %s", game["id"], type(exc).__name__)
        return None


def _lineup_games(records: list[dict], coefs: dict):
    """player_games for lineup changes when players are disabled; None when unbuildable."""
    try:
        return players.player_games(records, coefs)
    except Exception as exc:  # noqa: BLE001 - lineup changes must never fail the run
        logger.warning("lineup changes disabled for this run: %s", type(exc).__name__)
        return None


def _lineup_block(pg, tg, game: ScheduledGame, day: date, p, season: str, by_team: dict,
                  rho: float = config.LINEUP_RHO):
    """(lineup_changes | None, {home|away: team_lineup result} | None) for one game."""
    if pg is None:
        return None, None
    try:
        res = {}
        for side in ("home", "away"):
            abbr = game[side]["abbr"]
            lu = players.project_lineup(pg, abbr, day, season)
            res[side] = lineup.team_lineup(
                pg, tg, abbr, day, p, season, lu, by_team.get(abbr, []), rho)
        return res["home"]["changes"] + res["away"]["changes"], res
    except Exception as exc:  # noqa: BLE001 - one game's lineup changes never fail the run
        logger.warning("lineup changes for game %s failed: %s", game["id"], type(exc).__name__)
        return None, None


def _game_mults(lp: config.LineupParams, lineup_mults: dict | None, tg, r, home: str,
                away: str, day: date) -> predict.Mults:
    """Multipliers applied to lambda: only the factors the lineup gate enabled (Task 6)."""
    kw: dict[str, float] = {}
    if lp.use_lineup and lineup_mults:
        h, a = lineup_mults["home"], lineup_mults["away"]
        kw.update(att_h=h["m_att"], def_h=h["m_def"], att_a=a["m_att"], def_a=a["m_def"])
    if lp.use_h2h:
        kw.update(h2h_h=context.h2h_mult(tg, r, home, away, day),
                  h2h_a=context.h2h_mult(tg, r, away, home, day))
    return predict.Mults(**kw)


def _without_absentees(pin: players.PlayerInputs, mults: dict) -> players.PlayerInputs:
    """Drop ESPN-confirmed absentees from the lineups the players block uses."""
    gone = {pid for side in ("home", "away") for pid in mults[side]["espn_out_ids"]}
    if not gone:
        return pin
    return replace(pin, lineups={
        t: [s for s in lu if s["id"] not in gone] for t, lu in pin.lineups.items()})


def _predict_all(
    selected: list[ScheduledGame], records: list[dict], season: str, today: date, now: datetime,
    with_players: bool = False, injuries_by_team: dict | None = None,
) -> tuple[list[dict], pd.DataFrame]:
    p = config.load_params()
    lp = config.load_lineup_params()
    coefs = _load_coefs()
    tg = features.team_games(records, coefs, p.xg_weight)
    r = ratings.fit(tg, today, p, season=season)
    ps = ratings.period_shares(tg, today, p, season=season)
    sp = features.sim_params(tg)
    played = features.last_played(tg) if len(tg) else {}
    gg = adjust.goalie_games(records, coefs)
    gr = adjust.goalie_ratings(gg, today, p, season=season)
    ctx = _player_context(records, coefs, p, today, season) if with_players else None
    pg = ctx[0] if ctx is not None else _lineup_games(records, coefs)

    frozen = {g["id"]: g for g in _read_json(LAST_GAMES, [])}
    out: list[dict] = []
    for game in selected:
        if is_started(game, now):
            previous = frozen.get(game["id"])
            if previous is None:
                logger.warning("game %s has started and has no earlier export; dropped", game["id"])
                continue
            out.append(dict(previous, state="started"))
            continue
        day = date.fromisoformat(game["nhl_date"])
        starters = {
            t: adjust.project_starters(gg, t, day, season=season)
            for t in (game["home"]["abbr"], game["away"]["abbr"])
        }
        pin = _player_inputs(ctx, game, season) if ctx is not None else None
        changes, mults = _lineup_block(pg, tg, game, day, p, season, injuries_by_team or {},
                                       lp.rho)
        if pin is not None and changes is not None:
            pin = _without_absentees(pin, mults)
        lineup_mults = {
            side: {"m_att": mults[side]["m_att"], "m_def": mults[side]["m_def"]}
            if mults else {"m_att": 1.0, "m_def": 1.0}
            for side in ("home", "away")
        }
        try:
            gm = _game_mults(lp, lineup_mults, tg, r, game["home"]["abbr"],
                             game["away"]["abbr"], day)
        except Exception as exc:  # noqa: BLE001 - a failing factor never fails the game
            logger.warning("lineup/h2h factors for game %s failed: %s", game["id"],
                           type(exc).__name__)
            gm = predict.Mults()
        obj = predict.predict_game(game, r, ps, sp, p, played, gr, starters, pin, gm, lp)
        obj["lineup_changes"] = changes
        obj["_internal"]["lineup"] = lineup_mults
        obj.update(context.game_context(
            tg, records, game["home"]["abbr"], game["away"]["abbr"], season, day))
        out.append(obj)
    return out, tg


def _attach_insights(
    games: list[dict], tg: pd.DataFrame, records: list[dict], season: str, today: date
) -> None:
    """Fill insights of upcoming games; runs after odds and lineup changes are attached."""
    try:
        league = teamstats.league_stats(tg, records, _load_coefs(), season, today)
    except Exception as exc:  # noqa: BLE001 - insights must never fail the run
        logger.warning("league stats failed: %s", type(exc).__name__)
        league = {}
    for g in games:
        if g["state"] != "upcoming":
            continue
        try:
            stats = insights.schedule_stats(league, tg, g, season)
            g["insights"] = insights.game_insights(g, stats)
        except Exception as exc:  # noqa: BLE001 - one game's insights never fail the run
            logger.warning("insights for game %s failed: %s", g["id"], type(exc).__name__)
            g["insights"] = None


_DROPPABLE = ("players", "form", "season_compare", "h2h", "insights", "lineup_changes")
_ERROR_RE = re.compile(r"\$\.games\[(\d+)\]\.(" + "|".join(_DROPPABLE) + r")")


def _drop_invalid(games: list[dict], errors: list[str]) -> bool:
    """Null only the invalid optional field of the game it belongs to; True when all errors
    were such fields (so a rebuild can be validated again)."""
    hits = [_ERROR_RE.match(e) for e in errors]
    if not errors or not all(hits):
        return False
    ordered = sorted(games, key=lambda g: (g["start_utc"], g["id"]))  # as build_latest
    for m in hits:
        g = ordered[int(m.group(1))]
        field = m.group(2)
        logger.warning("%s dropped for game %s", field, g.get("id"))
        g[field] = None
        if field == "players" and g.get("_internal"):
            g["_internal"]["players_log"] = None
    return True


def _fresh_snapshot(now: datetime) -> dict | None:
    """The stored odds snapshot when it is at most ODDS_REUSE_HOURS old."""
    try:
        snapshot = _read_json(ODDS_STATE, None)
        if snapshot is None:
            return None
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("games"), dict):
            raise ValueError("bad snapshot shape")
        age = now - _parse_utc(snapshot["fetched_at"])
    except Exception as exc:  # noqa: BLE001 - a damaged snapshot must never fail the run
        logger.warning("odds snapshot ignored: %s", type(exc).__name__)
        return None
    return snapshot if age <= timedelta(hours=config.ODDS_REUSE_HOURS) else None


def _odds_note(resp: odds.OddsResponse) -> str:
    if not resp.events:
        note = "Ei kertoimia tarjolla"
    elif resp.remaining is None:
        note = "Kertoimet haettu"
    else:
        note = f"Krediittejä jäljellä {resp.remaining}"
    if resp.fallback:
        note += f" · varahaku alueilla {config.ODDS_FALLBACK_REGIONS}"
    return note


def _recent_fetch(entry: dict, now: datetime) -> bool:
    """True when the odds source was fetched within 24 h (else a quota reset may be missed)."""
    try:
        stamp = entry.get("last_ok_at") or entry.get("fetched_at")
        return now - _parse_utc(stamp) <= timedelta(hours=24)
    except Exception:  # noqa: BLE001 - unreadable state means: re-probe
        return False


def odds_stage(
    selected: list[ScheduledGame], state: dict, now: datetime, fetch: bool
) -> tuple[dict | None, bool]:
    """Odds snapshot for this run and whether it was fetched now (spec 4.1 step 7, 6.11)."""
    previous = _fresh_snapshot(now)
    if not fetch:
        return previous, False
    horizon = timedelta(hours=config.ODDS_NEXT_GAME_H)
    if not any(
        not is_started(g, now) and _parse_utc(g["start_utc"]) - now <= horizon for g in selected
    ):
        return previous, False
    entry = state.get("odds") or {}
    remaining = entry.get("remaining")
    if (
        isinstance(remaining, int)
        and remaining < config.ODDS_MIN_REMAINING
        and _recent_fetch(entry, now)
    ):
        state["odds"] = dict(
            entry, status="stale", note=f"Haku ohitettu: krediittejä jäljellä {remaining}"
        )
        return previous, False

    def fetch_and_match() -> tuple[odds.OddsResponse, dict]:
        try:
            resp = odds.fetch_odds(now, os.environ.get(config.ODDS_ENV_KEY))
            matched = odds.match_events(odds.parse_events(resp.events), selected)
        except SourceError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep arbitrary messages (and keys) out of state
            raise SourceError(type(exc).__name__) from None
        return resp, matched

    result = optional_source("odds", fetch_and_match, state, now)
    if result is None:
        state["odds"].update(remaining=entry.get("remaining"), used=entry.get("used"))
        return previous, False
    resp, matched = result
    state["odds"].update(remaining=resp.remaining, used=resp.used, note=_odds_note(resp))
    snapshot = {"fetched_at": _iso(now), "remaining": resp.remaining, **matched}
    return snapshot, True


def run(
    offline: Path | None, now: datetime, extra_if_early: bool, with_odds: bool = False
) -> int:
    today_str = nhl_today(now)
    today = date.fromisoformat(today_str)
    sources = _read_json(SOURCES_FILE, {})

    try:
        week, season_start = _fetch_week(today)
    except SourceError as exc:
        logger.error("core source NHL failed: %s", exc)
        return 1
    selected, next_game_date = select_games(week, today_str, season_start)

    if extra_if_early and not needs_extra_run(selected, now):
        logger.info("no game starts before the scheduled run; extra run skipped")
        _github_output(False)
        return 0

    try:
        # Cache files are immutable and idempotent, so writing them early is safe.
        written, skipped = cache.cache_finished(week)
        logger.info("cache: %d written, %d skipped", len(written), len(skipped))
        season = _current_season(selected, week)
        records = cache.load_seasons([_season_before(season), season])
        state: dict = {
            "nhl": {
                "status": "ok", "fetched_at": _iso(now), "last_ok_at": _iso(now), "note": None,
            },
            "injuries": sources.get("injuries") or {},
        }
        if sources.get("odds"):
            state["odds"] = sources["odds"]

        def offline_injuries() -> list[dict]:
            raise SourceError("offline mode")

        raw_injuries = optional_source(
            "injuries", offline_injuries if offline is not None else injuries.fetch, state, now,
            failed_status="unavailable", note_on_fail=INJURIES_NOTE,
        )
        try:
            injuries_by_team = lineup.parse_espn(raw_injuries) if raw_injuries else {}
        except Exception as exc:  # noqa: BLE001 - a odd payload must never fail the run
            logger.warning("injuries not parsed: %s", type(exc).__name__)
            injuries_by_team = {}
        games, tg = _predict_all(
            selected, records, season, today, now, players_enabled(offline), injuries_by_team
        )
        snapshot, fetched_now = odds_stage(selected, state, now, with_odds)
        if snapshot:
            for g in games:
                if g["state"] != "upcoming":
                    continue
                try:
                    books = snapshot["games"].get(str(g["id"]), {})
                    g["odds"] = value.game_odds(g, books, snapshot["fetched_at"])
                except Exception as exc:  # noqa: BLE001 - odds must never fail the run
                    logger.warning("odds for game %s failed: %s", g["id"], type(exc).__name__)
                    g["odds"] = None
                    previous = state.get("odds") or {}
                    state["odds"] = {
                        "status": "failed", "fetched_at": _iso(now),
                        "last_ok_at": previous.get("last_ok_at"), "note": type(exc).__name__,
                        "remaining": previous.get("remaining"), "used": previous.get("used"),
                    }
        _attach_insights(games, tg, records, season, today)
        odds_history = []
        if fetched_now:
            odds_history = tracking.odds_rows(
                snapshot, [g for g in games if g["state"] == "upcoming"]
            )
        try:
            draw = tracking.draw_gap(
                tracking.read_log("predictions", season),
                tracking.read_log("results", season)
                + [_result_record(rec) for rec in records],
                season,
            )
            calibration = {"draw": draw}
        except Exception as exc:  # noqa: BLE001 - calibration must never fail the run
            logger.warning("calibration failed: %s", type(exc).__name__)
            calibration = {"draw": None}
        latest = export.build_latest(games, state, next_game_date, now, calibration)
        errors = export.validate(latest)
        if _drop_invalid(games, errors):
            latest = export.build_latest(games, state, next_game_date, now, calibration)
            errors = export.validate(latest)
        if errors:
            logger.error("latest.json is invalid: %s", "; ".join(errors))
            return 1

        # Everything above can fail without leaving anything durable behind.
        tracking.record_results([_result_record(rec) for rec in records])
        tracking.append_predictions(games, _iso(now))
        try:
            tracking.append_players(games, _iso(now))
        except Exception as exc:  # noqa: BLE001 - players must never block publishing
            logger.warning("players log failed: %s", type(exc).__name__)
        if fetched_now:
            _write_json(ODDS_STATE, snapshot)
            tracking.append_odds(odds_history)
        _write_json(LAST_GAMES, [_public(g) for g in games])
        _write_json(SOURCES_FILE, state)
        # started games without an earlier export are dropped by design (plan decision 3),
        # so they are not "missing" for the SCHED check
        exported = {g["id"] for g in games}
        _write_json(
            SCHEDULE_IDS,
            [g["id"] for g in selected if g["id"] in exported or not is_started(g, now)],
        )
        export.write_latest(latest)
        _write_performance(records, season, now)
    except (SourceError, FileNotFoundError) as exc:
        logger.error("run failed: %s", exc)
        return 1
    except Exception:
        logger.exception("run failed unexpectedly")
        return 1
    _github_output(True)
    logger.info("wrote latest.json with %d games", len(latest["games"]))
    return 0


def _write_performance(records: list[dict], season: str, now: datetime) -> None:
    """performance.json from the history logs; a failure is logged and never fails the run."""
    try:
        results = tracking.read_log("results", season)
        player_rows = tracking.player_outcomes(tracking.read_log("players", season), records)
        backtest_file = config.path("model", "backtest_20252026.json")
        backtest = (json.loads(backtest_file.read_text(encoding="utf-8"))
                    if backtest_file.exists() else None)
        perf = performance.build_performance(
            tracking.read_log("predictions", season), results,
            tracking.read_log("odds", season), player_rows, now, backtest,
        )
        export.write_performance(perf)
    except Exception as exc:  # noqa: BLE001 - performance must never fail the run
        logger.warning("performance failed: %s", type(exc).__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.run")
    parser.add_argument("--offline", metavar="DIR", help="read NHL responses from fixtures")
    parser.add_argument("--now", metavar="ISO", help="override the current time")
    parser.add_argument("--extra-if-early", action="store_true")
    parser.add_argument("--odds", action="store_true", help="fetch bookmaker odds this run")
    args = parser.parse_args(argv)

    offline = Path(args.offline) if args.offline else None
    if offline is None:
        gate = config.load_gate()
        if not gate or gate.get("passed") is not True:
            print(GATE_MESSAGE)
            return 2
    now = _parse_utc(args.now) if args.now else _default_now(offline)

    previous_dir = config.OFFLINE_DIR
    config.OFFLINE_DIR = offline
    try:
        return run(offline, now, args.extra_if_early, args.odds)
    except Exception:
        logger.exception("run failed unexpectedly")
        return 1
    finally:
        config.OFFLINE_DIR = previous_dir


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
