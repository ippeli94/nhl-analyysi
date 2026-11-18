"""Pipeline orchestration (spec 4.1, 4.3, 4.4): fetch, cache, model, predict, track, export.

CLI: python -m pipeline.run [--offline DIR] [--now ISO] [--extra-if-early]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import adjust, cache, config, export, features, predict, ratings, tracking, xg
from pipeline.sources import injuries, nhl
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


def select_games(
    week: list[ScheduledGame], today: str
) -> tuple[list[ScheduledGame], str | None]:
    """Unfinished games dated today or tomorrow; when none, the next date with a game."""
    day = date.fromisoformat(today)
    window = {today, (day + timedelta(days=1)).isoformat()}
    open_games = [g for g in week if not cache.is_finished(g)]
    chosen = [g for g in open_games if g["nhl_date"] in window]
    if chosen:
        return chosen, None
    later = sorted(g["nhl_date"] for g in open_games if g["nhl_date"] > today)
    return [], later[0] if later else None


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


def _fetch_week(today: date) -> list[ScheduledGame]:
    starts = _week_requests(today)
    if not starts:
        raise SourceError("no schedule available for the requested dates")
    games: dict[int, ScheduledGame] = {}
    for start in starts:
        week, _ = nhl.fetch_schedule_week(start)
        for g in week:
            games.setdefault(g["id"], g)
    return list(games.values())


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


def _predict_all(
    selected: list[ScheduledGame], records: list[dict], season: str, today: date, now: datetime
) -> list[dict]:
    p = config.load_params()
    coefs = _load_coefs()
    tg = features.team_games(records, coefs, p.xg_weight)
    r = ratings.fit(tg, today, p, season=season)
    ps = ratings.period_shares(tg, today, p, season=season)
    sp = features.sim_params(tg)
    played = features.last_played(tg) if len(tg) else {}
    gg = adjust.goalie_games(records, coefs)
    gr = adjust.goalie_ratings(gg, today, p, season=season)

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
        out.append(predict.predict_game(game, r, ps, sp, p, played, gr, starters))
    return out


def run(offline: Path | None, now: datetime, extra_if_early: bool) -> int:
    today_str = nhl_today(now)
    today = date.fromisoformat(today_str)
    sources = _read_json(SOURCES_FILE, {})

    try:
        week = _fetch_week(today)
    except SourceError as exc:
        logger.error("core source NHL failed: %s", exc)
        return 1
    selected, next_game_date = select_games(week, today_str)

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
        games = _predict_all(selected, records, season, today, now)

        state: dict = {
            "nhl": {
                "status": "ok", "fetched_at": _iso(now), "last_ok_at": _iso(now), "note": None,
            },
            "injuries": sources.get("injuries") or {},
        }

        def offline_injuries() -> list[dict]:
            raise SourceError("offline mode")

        optional_source(
            "injuries", offline_injuries if offline is not None else injuries.fetch, state, now,
            failed_status="unavailable", note_on_fail=INJURIES_NOTE,
        )
        latest = export.build_latest(games, state, next_game_date, now)
        errors = export.validate(latest)
        if errors:
            logger.error("latest.json is invalid: %s", "; ".join(errors))
            return 1

        # Everything above can fail without leaving anything durable behind.
        tracking.record_results([_result_record(rec) for rec in records])
        tracking.append_predictions(games, _iso(now))
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
    except (SourceError, FileNotFoundError) as exc:
        logger.error("run failed: %s", exc)
        return 1
    except Exception:
        logger.exception("run failed unexpectedly")
        return 1
    _github_output(True)
    logger.info("wrote latest.json with %d games", len(latest["games"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.run")
    parser.add_argument("--offline", metavar="DIR", help="read NHL responses from fixtures")
    parser.add_argument("--now", metavar="ISO", help="override the current time")
    parser.add_argument("--extra-if-early", action="store_true")
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
        return run(offline, now, args.extra_if_early)
    except Exception:
        logger.exception("run failed unexpectedly")
        return 1
    finally:
        config.OFFLINE_DIR = previous_dir


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
