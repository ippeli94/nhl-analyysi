"""Problems-only review report (spec chapter 10).

    python tools/review.py [--skip-tests] [--data-only]

Writes reports/review.md and the `review` field of site/data/latest.json.
Exit code: 0 no problems, 1 warnings only, 2 any error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import cache, config, export, tracking, xg  # noqa: E402

ERROR, WARNING = "VIRHE", "VAROITUS"
MAX_LINES = 40
OK_TEXT = "OK — 0 ongelmaa\n"
REPORT = ("reports", "review.md")
LATEST = ("site", "data", "latest.json")
SOURCES_FILE = ("data", "state", "sources.json")
SCHEDULE_IDS = ("data", "state", "schedule_ids.json")
SUBPROCESS_TIMEOUT_S = 600
RECURSION_ENV = "REVIEW_RUNNING"

SUM_TOL = 0.001
MATRIX_TOL = 0.01
WIN_PROB_RANGE = (0.20, 0.80)
XG_RANGE = (1.5, 5.0)
GOALIE_MULT_EDGES = config.GOALIE_MULT_CLAMP
EDGE_EPS = 1e-6
MAX_AGE_DATA = timedelta(hours=1)
LATE_AFTER = timedelta(hours=24)
GAME_LENGTH = timedelta(hours=3)  # start -> assumed end
XG_WINDOW = timedelta(days=7)
XG_MIN_GAMES = 20
XG_RATIO_RANGE = (0.85, 1.15)
BAD_SOURCE_STATUSES = ("failed", "stale")
SECRET_RE = re.compile(r"\b[0-9a-f]{32}\b")
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".superpowers", ".ruff_cache",
             ".pytest_cache"}
SKIP_PREFIXES = ("data/cache/", "tests/fixtures/", ".superpowers/")
SKIP_SUFFIXES = (".gz", ".png", ".jpg", ".ico", ".woff", ".woff2", ".pyc", ".zip")


@dataclass
class Problem:
    level: str
    group: str
    where: str
    message: str
    hint: str


@dataclass
class Ctx:
    root: Path
    latest: dict | None
    now: datetime
    mode: str  # "data" | "full"
    skip_tests: bool = False
    memo: dict = field(default_factory=dict)  # per-run caches, so files are read once


def _parse(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _games(ctx: Ctx) -> list[dict]:
    return (ctx.latest or {}).get("games") or []


def _gname(g: dict) -> str:
    return f"{g['home']['abbr']}-{g['away']['abbr']} {g['id']}"


def _run(cmd: list[str], root: Path) -> str:
    done = subprocess.run(
        cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=SUBPROCESS_TIMEOUT_S, env={**os.environ, RECURSION_ENV: "1"}, check=False,
    )
    return done.stdout + done.stderr


# --- checks -----------------------------------------------------------------------------


def check_lint(ctx: Ctx) -> list[Problem]:
    if ctx.mode == "data":
        return []
    out = _run([sys.executable, "-m", "ruff", "check", ".", "--output-format", "concise",
                "--no-cache"], ctx.root)
    found = []
    for line in out.splitlines():
        m = re.match(r"^(\S.*?:\d+):\d+: (\S+) (.*)$", line)
        if m:
            found.append(Problem(WARNING, "LINT", m.group(1), f"{m.group(2)} {m.group(3)}",
                                 "aja python -m ruff check --fix ."))
    return found


def check_test(ctx: Ctx) -> list[Problem]:
    if ctx.mode == "data" or ctx.skip_tests or os.environ.get(RECURSION_ENV):
        return []
    out = _run([sys.executable, "-m", "pytest", "-q", "--tb=no", "-rfE", "-p",
                "no:cacheprovider"], ctx.root)
    found = []
    for line in out.splitlines():
        m = re.match(r"^(FAILED|ERROR) (\S+?)(?: - (.*))?$", line)
        if m:
            found.append(Problem(ERROR, "TEST", m.group(2), (m.group(3) or m.group(1)).strip(),
                                 "aja testi erikseen: python -m pytest <testi>"))
    return found


def check_site(ctx: Ctx) -> list[Problem]:
    hint = "korjaa pipeline/export.py tai pipeline/schema/latest.schema.json"
    if ctx.latest is None:
        return [Problem(ERROR, "SITE", "site/data/latest.json",
                        "tiedosto puuttuu tai ei ole luettavaa JSONia",
                        "aja pipeline tai tarkista viennin virheet")]
    found = [Problem(ERROR, "SITE", "site/data/latest.json", e, hint)
             for e in export.validate(ctx.latest)]
    app = ctx.root / "site" / "app.js"
    node = shutil.which("node")
    if node and app.exists():
        done = subprocess.run([node, "--check", str(app)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False)
        if done.returncode != 0:
            first = next((x for x in done.stderr.splitlines() if x.strip()), "syntaksivirhe")
            found.append(Problem(ERROR, "SITE", "site/app.js", first.strip(),
                                 "korjaa JavaScript-syntaksi"))
    return found


def _numbers(game: dict):
    prob, score = game["prob"], game["score"]
    for k in ("home_win", "away_win", "reg_home", "reg_draw", "reg_away"):
        yield f"prob.{k}", prob[k]
    for k, v in (score.get("total", {}).get("over") or {}).items():
        yield f"score.total.over[{k}]", v
    for k, v in (score.get("spread") or {}).items():
        yield f"score.spread[{k}]", v
    for i, row in enumerate(score.get("matrix") or []):
        for j, v in enumerate(row):
            yield f"score.matrix[{i}][{j}]", v
    for p in game.get("periods") or []:
        for k, v in p.items():
            if k.startswith("p_"):
                yield f"periods[{p.get('n')}].{k}", v


def check_prob(ctx: Ctx) -> list[Problem]:
    if ctx.latest is None or export.validate(ctx.latest):
        return []  # SITE reports the schema errors; values cannot be trusted
    found: list[Problem] = []
    for g in _games(ctx):
        where, prob = _gname(g), g["prob"]
        s2 = prob["home_win"] + prob["away_win"]
        if abs(s2 - 1) > SUM_TOL:
            found.append(Problem(ERROR, "PROB", where, f"home_win+away_win = {s2:.4f}",
                                 "tarkista pipeline/simulate.py normalisointi"))
        s3 = prob["reg_home"] + prob["reg_draw"] + prob["reg_away"]
        if abs(s3 - 1) > SUM_TOL:
            found.append(Problem(ERROR, "PROB", where, f"1X2-summa = {s3:.4f}",
                                 "tarkista pipeline/simulate.py normalisointi"))
        matrix = g["score"].get("matrix") or []
        total = sum(sum(row) for row in matrix)
        if matrix and abs(total - 1) > MATRIX_TOL:
            found.append(Problem(ERROR, "PROB", where, f"tulosmatriisin summa = {total:.4f}",
                                 "tarkista matriisin rajaus pipeline/simulate.py"))
        bad = [name for name, v in _numbers(g) if not 0 <= v <= 1]
        if bad:
            found.append(Problem(ERROR, "PROB", where,
                                 f"todennäköisyys väliltä 0-1 ulkona: {bad[0]} (yht. {len(bad)})",
                                 "tarkista pipeline/simulate.py"))
        lo, hi = WIN_PROB_RANGE
        if prob["home_win"] > hi or prob["home_win"] < lo:
            found.append(Problem(WARNING, "PROB", where,
                                 f"kotivoiton todennäköisyys {prob['home_win']:.2f} ääripäässä",
                                 "tarkista joukkuevahvuudet pipeline/ratings.py"))
        for side in ("home", "away"):
            value = g["xg"][side]
            if not XG_RANGE[0] <= value <= XG_RANGE[1]:
                found.append(Problem(WARNING, "PROB", where,
                                     f"xg.{side} {value} alueen {XG_RANGE[0]}-{XG_RANGE[1]} "
                                     "ulkopuolella", "tarkista pipeline/ratings.py"))
            goalie = (g.get("goalies") or {}).get(side)
            if goalie and any(abs(goalie["mult"] - e) < EDGE_EPS for e in GOALIE_MULT_EDGES):
                found.append(Problem(WARNING, "PROB", where,
                                     f"maalivahdin {side} kerroin {goalie['mult']:.2f} rajalla",
                                     "tarkista pipeline/adjust.py rajaus"))
    return found


def check_roster(ctx: Ctx) -> list[Problem]:
    found = []
    for g in _games(ctx):
        if g.get("state") != "upcoming":
            continue
        for side in ("home", "away"):
            if (g.get("goalies") or {}).get(side) is None:
                found.append(Problem(WARNING, "ROSTER", _gname(g),
                                     f"{g[side]['abbr']}: ei maalivahtitietoa",
                                     "tarkista pipeline/adjust.py project_starters"))
    return found


def _memo(ctx: Ctx, key: tuple, build: Callable[[], object]):
    if key not in ctx.memo:
        ctx.memo[key] = build()
    return ctx.memo[key]


def _cached(ctx: Ctx, game_id: int) -> bool:
    season = tracking.season_of(game_id)
    ids = _memo(ctx, ("cached", season), lambda: cache.cached_ids(season))
    return game_id in ids


def _history_file(ctx: Ctx, kind: str, season: str) -> Path:
    return ctx.root / "data" / "history" / kind / f"{season}.jsonl"


def _history(ctx: Ctx, kind: str, season: str) -> tuple[list[dict], list[int]]:
    """Rows of one history log and the numbers of its unparseable lines."""

    def build():
        rows: list[dict] = []
        bad: list[int] = []
        f = _history_file(ctx, kind, season)
        try:
            lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
        except (OSError, UnicodeDecodeError):
            return rows, [0]
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad.append(number)
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                bad.append(number)
        return rows, bad

    return _memo(ctx, ("history", kind, season), build)


def _has_result(ctx: Ctx, game_id: int) -> bool:
    rows, _ = _history(ctx, "results", tracking.season_of(game_id))
    ids = _memo(ctx, ("result_ids", tracking.season_of(game_id)),
                lambda: {r.get("game_id") for r in rows})
    return game_id in ids


def check_sched(ctx: Ctx) -> list[Problem]:
    ids = _read_json(ctx.root / Path(*SCHEDULE_IDS), None)
    if ids is None:
        if ctx.mode == "data":
            return []
        return [Problem(WARNING, "SCHED", "data/state/schedule_ids.json", "tiedosto puuttuu",
                        "aja pipeline: python -m pipeline.run")]
    present = {g["id"] for g in _games(ctx)}
    return [
        Problem(ERROR, "SCHED", f"game {game_id}",
                "ohjelman ottelu puuttuu latest.json:sta eikä ole päättynyt",
                "tarkista pipeline/run.py ennustevaihe ja lokit")
        for game_id in ids
        if game_id not in present and not _cached(ctx, game_id) and not _has_result(ctx, game_id)
    ]


def _prediction_rows(ctx: Ctx) -> list[dict]:
    folder = ctx.root / "data" / "history" / "predictions"
    rows: list[dict] = []
    for f in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        rows.extend(_history(ctx, "predictions", f.stem)[0])
    return rows


def _ended_over_a_day_ago(row: dict, now: datetime) -> bool:
    return now - (_parse(row["start_utc"]) + GAME_LENGTH) > LATE_AFTER


def _long_over(ctx: Ctx) -> list[int]:
    seen: dict[int, None] = {}
    for row in _prediction_rows(ctx):
        if _ended_over_a_day_ago(row, ctx.now):
            seen.setdefault(row["game_id"])
    return list(seen)


def check_fresh(ctx: Ctx) -> list[Problem]:
    found = []
    stamp = (ctx.latest or {}).get("generated_at")
    if ctx.mode == "data" and stamp:
        age = ctx.now - _parse(stamp)
        if age > MAX_AGE_DATA:
            found.append(Problem(ERROR, "FRESH", "site/data/latest.json",
                                 f"generated_at on {age.total_seconds() / 3600:.1f} h vanha",
                                 "aja pipeline uudelleen"))
    found += [
        Problem(WARNING, "FRESH", f"game {gid}",
                "yli 24 h sitten päättynyt ottelu puuttuu välimuistista",
                "aja python -m pipeline.backfill")
        for gid in _long_over(ctx) if not _cached(ctx, gid)
    ]
    return found


def check_source(ctx: Ctx) -> list[Problem]:
    sources = {s["id"]: s for s in (ctx.latest or {}).get("sources") or []}
    state = _read_json(ctx.root / Path(*SOURCES_FILE), {}) or {}
    sources.update({k: v for k, v in state.items() if isinstance(v, dict)})
    return [
        Problem(WARNING, "SOURCE", src_id,
                f"tila {s['status']}: {s.get('note') or '-'} "
                f"(viimeisin onnistunut {s.get('last_ok_at') or '-'})",
                "tarkista lähteen saatavuus")
        for src_id, s in sources.items() if s.get("status") in BAD_SOURCE_STATUSES
    ]


def check_track(ctx: Ctx) -> list[Problem]:
    found = []
    for kind in ("predictions", "results"):
        folder = ctx.root / "data" / "history" / kind
        for f in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
            bad = _history(ctx, kind, f.stem)[1]
            if bad:
                first = f"rivi {bad[0]}" if bad[0] else "tiedostoa ei voi lukea"
                found.append(Problem(WARNING, "TRACK", f"data/history/{kind}/{f.name}",
                                     f"{len(bad)} jäsentymätöntä riviä ({first})",
                                     "korjaa tai poista rikkinäinen rivi"))
    rows = _prediction_rows(ctx)
    if rows and _games(ctx):
        newest = max(_parse(r["ts"]) for r in rows)
        if ctx.now - newest > LATE_AFTER:
            found.append(Problem(WARNING, "TRACK", "data/history/predictions",
                                 f"uusin ennuste {newest:%Y-%m-%dT%H:%MZ} on yli 24 h vanha",
                                 "tarkista pipeline-ajon ajastus"))
    found += [
        Problem(WARNING, "TRACK", f"game {gid}",
                "yli 24 h sitten päättyneellä ottelulla ei ole tulosta",
                "tarkista tracking.record_results")
        for gid in _long_over(ctx) if not _has_result(ctx, gid)
    ]
    return found


def _season_files(ctx: Ctx) -> list[tuple[Path, dict | None]]:
    """Cache files of the current (newest) season, read once; None marks an unreadable one."""

    def build():
        base = ctx.root / Path(*cache.GAMES_DIR)
        seasons = sorted(p.name for p in base.iterdir()
                         if p.is_dir() and re.fullmatch(r"\d{8}", p.name)) if base.is_dir() else []
        if not seasons:
            return []
        out: list[tuple[Path, dict | None]] = []
        for f in sorted((base / seasons[-1]).glob(f"*{cache.SUFFIX}")):
            try:
                out.append((f, cache.read(f)))
            except cache.UNREADABLE:
                out.append((f, None))
        return out

    return _memo(ctx, ("season_files",), build)


def check_cache(ctx: Ctx) -> list[Problem]:
    return [
        Problem(ERROR, "CACHE", f.relative_to(ctx.root).as_posix(),
                "välimuistitiedosto ei ole luettavissa",
                "poista tiedosto ja aja python -m pipeline.backfill")
        for f, rec in _season_files(ctx) if rec is None
    ]


def check_xg(ctx: Ctx) -> list[Problem]:
    coefs_file = ctx.root / "model" / "xg_coefs.json"
    if not coefs_file.exists():
        return []
    cutoff = (ctx.now - XG_WINDOW).date().isoformat()
    records = [rec for _, rec in _season_files(ctx)
               if rec is not None and rec.get("date", "") >= cutoff]
    if len(records) < XG_MIN_GAMES:
        return []
    df = xg.shots_frame(records)
    scored = xg.score(df, xg.load_coefs(coefs_file))
    total_xg = float(scored[~(scored != scored)].sum())
    is_goal = (df["ev"] == "goal") & ~df["empty_net"].astype(bool)
    goals = int(is_goal.sum())
    if goals == 0:
        return []
    ratio = total_xg / goals
    if not XG_RATIO_RANGE[0] <= ratio <= XG_RATIO_RANGE[1]:
        return [Problem(WARNING, "XG", "model/xg_coefs.json",
                        f"7 pv xG/maalit = {ratio:.2f} ({total_xg:.0f}/{goals})",
                        "kouluta xG-malli uudelleen: python -m pipeline.xg")]
    return []


def _text_files(ctx: Ctx) -> list[Path]:
    root = ctx.root
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, check=True,
        ).stdout.decode("utf-8", errors="replace")
        rels = [p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        rels = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            rels.extend((Path(dirpath) / f).relative_to(root).as_posix() for f in files)
    return [root / r for r in rels
            if not r.startswith(SKIP_PREFIXES) and not r.endswith(SKIP_SUFFIXES)]


def check_secret(ctx: Ctx) -> list[Problem]:
    files: list[Path] = sorted((ctx.root / "site" / "data").glob("*.json"))
    if ctx.mode != "data":
        files += [f for f in _text_files(ctx) if f not in files]
    found = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if SECRET_RE.search(line):
                rel = f.relative_to(ctx.root).as_posix()
                found.append(Problem(ERROR, "SECRET", f"{rel}:{number}",
                                     "32 merkin heksamerkkijono (avaimen muotoinen)",
                                     "poista avain ja kierrätä se"))
    return found


CHECKS: list[Callable[[Ctx], list[Problem]]] = [
    check_lint, check_test, check_site, check_prob, check_roster, check_sched, check_fresh,
    check_source, check_track, check_cache, check_xg, check_secret,
]


# --- report -----------------------------------------------------------------------------


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def render(problems: list[Problem], now: datetime) -> str:
    if not problems:
        return OK_TEXT
    errors = sum(p.level == ERROR for p in problems)
    warnings = len(problems) - errors
    header = (f"# Tarkistusraportti {now.astimezone(UTC):%Y-%m-%dT%H:%M}Z — "
              f"{_plural(errors, 'virhe', 'virhettä')}, "
              f"{_plural(warnings, 'varoitus', 'varoitusta')}")
    groups: dict[tuple[str, str, str], list[Problem]] = {}
    for p in sorted(problems, key=lambda p: p.level != ERROR):
        groups.setdefault((p.level, p.group, p.hint), []).append(p)
    lines = []
    for items in groups.values():
        first = items[0]
        line = (f"[{first.level}] {first.group} {first.where} — {first.message}"
                f" — vihje: {first.hint}")
        if len(items) > 1:
            line += f" (+{len(items) - 1} muuta samanlaista)"
        lines.append(line)
    room = MAX_LINES - 1  # the header takes one line
    if len(lines) > room:
        dropped = len(lines) - (room - 1)
        lines = [*lines[: room - 1], f"(+{dropped} riviä jätetty pois)"]
    return "\n".join([header, *lines]) + "\n"


def exit_code(problems: list[Problem]) -> int:
    if any(p.level == ERROR for p in problems):
        return 2
    return 1 if problems else 0


def _write_review_field(root: Path, latest: dict, problems: list[Problem]) -> None:
    worst = "error" if exit_code(problems) == 2 else "warning" if problems else None
    latest["review"] = {"problems": len(problems), "worst": worst}
    target = root / Path(*LATEST)
    text = json.dumps(latest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools/review.py")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--data-only", action="store_true")
    args = parser.parse_args(argv)

    root = config.path()
    now = datetime.now(UTC)
    latest = _read_json(root / Path(*LATEST), None)
    if not isinstance(latest, dict):
        latest = None
    ctx = Ctx(root, latest, now, "data" if args.data_only else "full", args.skip_tests)

    problems: list[Problem] = []
    for check in CHECKS:
        try:
            problems.extend(check(ctx))
        except Exception as exc:  # noqa: BLE001 - a broken check is itself a finding
            problems.append(Problem(ERROR, check.__name__.removeprefix("check_").upper(),
                                    "tools/review.py", f"tarkistus kaatui: {exc!r}",
                                    "korjaa tarkistus tai sen syöte"))
    report = config.path(*REPORT)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render(problems, now), encoding="utf-8", newline="\n")
    if latest is not None:
        _write_review_field(root, latest, problems)
    return exit_code(problems)


if __name__ == "__main__":
    sys.exit(main())
