"""Rule-based insights per game (spec section 7). No model, only data the page already has."""

from __future__ import annotations

import dataclasses
from datetime import date

from pipeline import config, teams, teamstats
from pipeline.teamstats import TeamStats

ABSENCE_KINDS = ("out", "doubtful", "traded_away")
RETURN_KINDS = ("returning", "acquired")
MARKET_KEYS = ("h2h", "1x2")
ORDINALS = {3: "kolmas", 4: "neljäs", 5: "viides", 6: "kuudes", 7: "seitsemäs", 8: "kahdeksas"}


def _pct(x: float) -> str:
    return f"{round(x * 100)} %"


def _dec(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", ",").replace("-", "−")


def _sample(text: str, n: int) -> str:
    if n < config.INSIGHT_SMALL_SAMPLE:
        return text[:-1] + f" ({n} ottelua)."
    return text


class _Side:
    """One team of the game with the names the copy needs."""

    def __init__(self, block: dict, stats: TeamStats | None):
        self.abbr = block["abbr"]
        self.short = teams.short_name(self.abbr, block.get("name") or self.abbr)
        self.gen = teams.genitive(self.short)
        self.ts = stats

    @property
    def ok(self) -> bool:
        """Enough games for a stats-based rule."""
        return self.ts is not None and self.ts.gp >= config.INSIGHT_MIN_GAMES


def _ins(rule: str, sev: int, team: str | None, text: str) -> dict:
    return {"id": rule, "team": team, "severity": sev, "text": text}


def _best_worst(shares: list[float | None]) -> tuple[int, int] | None:
    if not shares or any(s is None for s in shares):
        return None
    return max(range(3), key=lambda i: shares[i]), min(range(3), key=lambda i: shares[i])


def _changes(game: dict, team: str, kinds: tuple, sign: int) -> dict | None:
    """The change of the team with the largest impact in the wanted direction."""
    best = None
    for c in game.get("lineup_changes") or []:
        impact = c.get("impact_attack")
        if c.get("team") != team or c.get("kind") not in kinds or impact is None:
            continue
        if sign < 0 and impact > config.INSIGHT_KEY_ABSENCE_IMPACT + 1e-9:
            continue
        if sign > 0 and impact < config.INSIGHT_RETURN_IMPACT - 1e-9:
            continue
        if best is None or abs(impact) > abs(best["impact_attack"]):
            best = c
    return best


def _key_absence(game, sides):
    out = []
    for s, _ in sides:
        c = _changes(game, s.abbr, ABSENCE_KINDS, -1)
        if c:
            state = {"out": "poissa", "doubtful": "epävarma", "traded_away": "siirtynyt pois"}
            out.append(_ins("key_absence", 3, s.abbr, (
                f"{s.gen} {c.get('player') or 'avainpelaaja'} {state[c['kind']]}: "
                f"maaliodotus −{round(abs(c['impact_attack']) * 100)} %.")))
    return out


def _model_vs_market(game, sides):
    for m in (game.get("odds") or {}).get("markets") or []:
        if m.get("key") in MARKET_KEYS and any(
            o.get("flag") == "suspicious" for o in m.get("outcomes") or []
        ):
            return [_ins("model_vs_market", 3, None,
                         "Malli ja markkina ovat eri mieltä yli 10 prosenttiyksikköä "
                         "— tarkista poissaolot.")]
    return []


def _b2b(game, sides):
    out = []
    for s, o in sides:
        if s.ts and o.ts and s.ts.played_yesterday and not o.ts.played_yesterday:
            out.append(_ins("b2b", 2, s.abbr,
                            f"{s.short} pelaa toisena iltana peräkkäin, {o.short} levänneenä."))
    return out


def _goalie_edge(game, sides):
    f = next((f for f in game.get("factors") or [] if f.get("key") == "goalie"), None)
    if not f or not f.get("home") or not f.get("away"):
        return []
    # factor.home scales the home goal rate (effect of the away goalie), factor.away the away rate:
    # a high home/away ratio means the home side's goal rate gains relative to the away side's.
    ratio = f["home"] / f["away"]
    lim = config.INSIGHT_GOALIE_RATIO
    if ratio >= lim:
        s, gain = sides[0][0], ratio - 1
    elif ratio <= 1 / lim:
        s, gain = sides[1][0], 1 / ratio - 1
    else:
        return []
    return [_ins("goalie_edge", 2, s.abbr,
                 f"Maalivahtiero {s.gen} eduksi (maaliodotus noin {_pct(gain)} suurempi).")]


def _return_or_trade(game, sides):
    out = []
    for s, _ in sides:
        c = _changes(game, s.abbr, RETURN_KINDS, 1)
        if c:
            what = "palaa kokoonpanoon" if c["kind"] == "returning" else "on uusi hankinta"
            out.append(_ins("return_or_trade", 2, s.abbr, (
                f"{c.get('player') or 'Pelaaja'} {what}: "
                f"{s.gen} maaliodotus +{round(c['impact_attack'] * 100)} %.")))
    return out


def _streak(game, sides):
    out = []
    for s, _ in sides:
        if not s.ok:
            continue
        st = s.ts.streak or ""
        run = int(st[1:]) if st[1:].isdigit() else 0
        if st[:1] == "V" and run >= config.INSIGHT_STREAK_RESULT:
            text = f"{s.short} on voittanut {run} peräkkäin."
        elif st[:1] == "H" and run >= config.INSIGHT_STREAK_RESULT:
            text = f"{s.short} on hävinnyt {run} peräkkäin."
        elif s.ts.point_streak >= config.INSIGHT_STREAK_POINTS:
            text = f"{s.short} on ottanut pisteen {s.ts.point_streak} ottelussa peräkkäin."
        else:
            continue
        out.append(_ins("streak", 2, s.abbr, text))
    return out


def _pp_vs_pk(game, sides):
    out = []
    for s, o in sides:
        if not (s.ok and o.ok) or s.ts.pp_rank is None or o.ts.pk_rank is None:
            continue
        if (s.ts.pp_rank <= config.INSIGHT_PP_TOP
                and o.ts.pk_rank > o.ts.n_ranked - config.INSIGHT_PK_BOTTOM):
            out.append(_ins("pp_vs_pk", 2, s.abbr, (
                f"{s.gen} ylivoima ({_pct(s.ts.pp)}, {s.ts.pp_rank}.) kohtaa "
                f"{o.gen} alivoiman ({_pct(o.ts.pk)}, {o.ts.pk_rank}.).")))
    return out


def _collapse(game, sides):
    out = []
    for s, _ in sides:
        t = s.ts
        if (s.ok and t.led_after2 >= config.INSIGHT_COLLAPSE_MIN
                and t.led_after2_won / t.led_after2 < config.INSIGHT_COLLAPSE_WON_SHARE):
            out.append(_ins("third_period_collapse", 2, s.abbr, (
                f"{s.short} on voittanut vain {t.led_after2_won} niistä {t.led_after2} ottelusta, "
                f"joissa se on johtanut 2. erän jälkeen.")))
    return out


def _period_name(i: int) -> str:
    return f"{i + 1}."


def _period_rules(game, sides):
    """period_matchup, period_swing, period_strength, period_weakness in rule order."""
    matchup, swing, strength, weakness = [], [], [], []
    for s, o in sides:
        bw = _best_worst(s.ts.period_xg_share) if s.ok else None
        if bw is None:
            continue
        best, worst = bw
        shares = s.ts.period_xg_share
        if max(shares) - min(shares) >= config.INSIGHT_PERIOD_SWING - 1e-9:
            swing.append(_ins("period_swing", 1, s.abbr, (
                f"{s.gen} xG-osuus vaihtelee erien välillä: vahvin on {_period_name(best)} erä "
                f"({_pct(shares[best])}), heikoin {_period_name(worst)} erä "
                f"({_pct(shares[worst])}).")))
        else:
            if shares[best] >= config.INSIGHT_PERIOD_STRONG:
                strength.append(_ins("period_strength", 1, s.abbr, (
                    f"{s.short} hallitsee {_period_name(best)} erää "
                    f"(xG-osuus {_pct(shares[best])}).")))
            if shares[worst] <= config.INSIGHT_PERIOD_WEAK:
                weakness.append(_ins("period_weakness", 1, s.abbr, (
                    f"{_period_name(worst)} erässä vastustajat luovat {s.gen} vastaan enemmän "
                    f"(xG-osuus {_pct(shares[worst])}).")))
        ob = _best_worst(o.ts.period_xg_share) if o.ok else None
        if (ob and shares[best] >= config.INSIGHT_PERIOD_STRONG and best == ob[1]
                and o.ts.period_xg_share[ob[1]] <= config.INSIGHT_PERIOD_WEAK):
            matchup.append(_ins("period_matchup", 1, s.abbr, (
                f"{o.gen} heikoin erä ({_period_name(best)}) on {s.gen} vahvin.")))
    return matchup, swing, strength, weakness


def _comeback(game, sides):
    out = []
    for s, _ in sides:
        t = s.ts
        if (s.ok and t.trailed_after2 >= config.INSIGHT_COMEBACK_MIN
                and t.trailed_after2_won / t.trailed_after2 >= config.INSIGHT_COMEBACK_WON_SHARE):
            out.append(_ins("comeback", 1, s.abbr, (
                f"{s.short} on voittanut {t.trailed_after2_won} niistä "
                f"{t.trailed_after2} ottelusta, joissa se on ollut tappiolla 2. erän jälkeen.")))
    return out


def _first_goal(game, sides):
    out = []
    for s, _ in sides:
        t = s.ts
        if not (s.ok and t.scored_first >= config.INSIGHT_FIRST_GOAL_MIN):
            continue
        share = t.scored_first_won / t.scored_first
        if share >= config.INSIGHT_FIRST_GOAL_HIGH:
            text = (f"Kun {s.short} tekee avausmaalin, se voittaa {_pct(share)} otteluista "
                    f"(otos {t.scored_first} ottelua).")
        elif share <= config.INSIGHT_FIRST_GOAL_LOW:
            text = (f"{s.short} voittaa vain {_pct(share)} otteluista, "
                    f"joissa se tekee avausmaalin (otos {t.scored_first} ottelua).")
        else:
            continue
        out.append(_ins("first_goal", 1, s.abbr, text))
    return out


def _penalty_prone(game, sides):
    out = []
    for s, o in sides:
        if not (s.ok and o.ok) or s.ts.pp_rank is None or o.ts.pim_rank is None:
            continue
        if (o.ts.pim_rank <= config.INSIGHT_PENALTY_OPP_RANK
                and s.ts.pp_rank <= config.INSIGHT_PENALTY_OWN_PP_RANK):
            out.append(_ins("penalty_prone", 1, s.abbr, (
                f"{o.short} ottaa paljon jäähyjä ({o.ts.pim_rank}. eniten) "
                f"— {s.gen} ylivoimalle tilaisuuksia.")))
    return out


def _schedule_load(game, sides):
    out = []
    for s, _ in sides:
        t = s.ts
        if t is None:
            continue
        if t.road_run >= config.INSIGHT_LOAD_ROAD_RUN:
            n = ORDINALS.get(t.road_run, f"{t.road_run}.")
            out.append(_ins("schedule_load", 1, s.abbr,
                            f"{s.gen} {n} peräkkäinen vierasottelu."))
        elif t.games_last_4d >= config.INSIGHT_LOAD_GAMES_4D:
            n = ORDINALS.get(t.games_last_4d, f"{t.games_last_4d}.")
            out.append(_ins("schedule_load", 1, s.abbr,
                            f"{s.gen} {n} ottelu neljässä päivässä."))
    return out


def _form_vs_season(game, sides):
    out = []
    for s, _ in sides:
        t = s.ts
        if not s.ok or t.xgf_pct_last10 is None or t.xgf_pct_season is None:
            continue
        diff = t.xgf_pct_last10 - t.xgf_pct_season
        if abs(diff) >= config.INSIGHT_FORM_DIFF - 1e-9:
            word = "parantunut" if diff > 0 else "heikentynyt"
            out.append(_ins("form_vs_season", 1, s.abbr, (
                f"{s.gen} peli on {word}: xG-osuus viime 10 ottelussa "
                f"{_pct(t.xgf_pct_last10)} (kausi {_pct(t.xgf_pct_season)}).")))
    return out


def _luck(game, sides):
    out = []
    for s, _ in sides:
        t = s.ts
        if not s.ok or t.luck_last10 is None or abs(t.luck_last10) < config.INSIGHT_LUCK:
            continue
        if t.luck_last10 > 0:
            text = (f"{s.short} on tehnyt selvästi enemmän maaleja kuin paikat edellyttävät "
                    f"(+{_dec(t.luck_last10)} maalia ottelua kohden viime 10 ottelussa) "
                    f"— tulokset voivat tasaantua.")
        else:
            text = (f"{s.short} on tehnyt selvästi vähemmän maaleja kuin paikat edellyttävät "
                    f"({_dec(t.luck_last10)} maalia ottelua kohden viime 10 ottelussa) "
                    f"— tulokset voivat parantua.")
        out.append(_ins("luck", 1, s.abbr, text))
    return out


def _vs_last_season(game, sides):
    out = []
    for s, _ in sides:
        t = s.ts
        if (s.ts is None or t.gp < config.INSIGHT_LAST_SEASON_MIN_GAMES
                or t.pts_pct is None or t.pts_pct_last is None):
            continue
        diff = t.pts_pct - t.pts_pct_last
        if abs(diff) >= config.INSIGHT_LAST_SEASON_DIFF - 1e-9:
            word = "parempi" if diff > 0 else "heikompi"
            out.append(_ins("vs_last_season", 1, s.abbr, (
                f"{s.short} on selvästi viime kautta {word} "
                f"({_pct(t.pts_pct)} vs. {_pct(t.pts_pct_last)}).")))
    return out


def _h2h_dominance(game, sides):
    games = ((game.get("h2h") or {}).get("games") or [])[: config.INSIGHT_H2H_WINDOW]
    if len(games) < config.INSIGHT_H2H_WINDOW:
        return []
    wins: dict[str, int] = {}
    for g in games:
        try:
            hs, a_s = (int(x) for x in g["score"].split("-"))
        except (KeyError, ValueError, AttributeError):
            return []
        w = g["home"] if hs > a_s else g["away"]
        wins[w] = wins.get(w, 0) + 1
    for s, _ in sides:
        if wins.get(s.abbr, 0) >= config.INSIGHT_H2H_WINS:
            return [_ins("h2h_dominance", 1, s.abbr, (
                f"{s.short} on voittanut {wins[s.abbr]} viimeisestä "
                f"{config.INSIGHT_H2H_WINDOW} kohtaamisesta (pieni otos)."))]
    return []


def _home_road_split(game, sides):
    out = []
    for s, _ in sides:
        t = s.ts
        if (not s.ok or t.home_pts_pct is None or t.road_pts_pct is None
                or min(t.home_gp, t.road_gp) < config.INSIGHT_SPLIT_MIN_GAMES):
            continue
        if abs(t.home_pts_pct - t.road_pts_pct) >= config.INSIGHT_SPLIT_DIFF - 1e-9:
            where = "vieraissa" if t.road_pts_pct < t.home_pts_pct else "kotona"
            out.append(_ins("home_road_split", 1, s.abbr, (
                f"{s.short} on {where} selvästi heikompi "
                f"(kotona {_pct(t.home_pts_pct)}, vieraissa {_pct(t.road_pts_pct)}).")))
    return out


def game_insights(game: dict, stats: dict[str, TeamStats]) -> list[dict]:
    home = _Side(game["home"], stats.get(game["home"]["abbr"]))
    away = _Side(game["away"], stats.get(game["away"]["abbr"]))
    sides = [(home, away), (away, home)]
    matchup, swing, strength, weakness = _period_rules(game, sides)
    # rule order within a severity: the order of the spec table
    found = [
        *_key_absence(game, sides), *_model_vs_market(game, sides),
        *_b2b(game, sides), *_goalie_edge(game, sides), *_return_or_trade(game, sides),
        *_streak(game, sides), *_pp_vs_pk(game, sides), *_collapse(game, sides),
        *matchup, *swing, *strength, *weakness,
        *_comeback(game, sides), *_first_goal(game, sides), *_penalty_prone(game, sides),
        *_schedule_load(game, sides), *_form_vs_season(game, sides), *_luck(game, sides),
        *_vs_last_season(game, sides), *_h2h_dominance(game, sides),
        *_home_road_split(game, sides),
    ]
    found = [
        dict(i, text=_sample(i["text"], stats[i["team"]].gp))
        if i["id"] in _SAMPLE_RULES and i["team"] in stats else i
        for i in found
    ]
    found.sort(key=lambda i: -i["severity"])  # stable: keeps rule order
    out: list[dict] = []
    per_team: dict[str, int] = {}
    for i in found:
        if len(out) >= config.INSIGHT_MAX:
            break
        t = i["team"]
        if t is not None:
            if per_team.get(t, 0) >= config.INSIGHT_MAX_PER_TEAM:
                continue
            per_team[t] = per_team.get(t, 0) + 1
        out.append(i)
    return out


# Rules whose text states a season figure: "(n ottelua)" is appended while n < 20.
_SAMPLE_RULES = frozenset({
    "period_strength", "period_weakness", "period_swing", "period_matchup",
    "form_vs_season", "vs_last_season", "home_road_split",
})


def schedule_stats(
    stats: dict[str, TeamStats], tg, game: dict, season: str
) -> dict[str, TeamStats]:
    """Per-game copy of the stats with the schedule fields filled from schedule_load."""
    day = date.fromisoformat(game["nhl_date"])
    out = dict(stats)
    for side, is_home in (("home", True), ("away", False)):
        abbr = game[side]["abbr"]
        if abbr not in stats:
            continue
        b2b, last4, run = teamstats.schedule_load(tg, abbr, day, is_home, season)
        out[abbr] = dataclasses.replace(
            stats[abbr], played_yesterday=b2b, games_last_4d=last4, road_run=run)
    return out
