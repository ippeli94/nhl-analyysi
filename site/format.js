// Pure formatting helpers (no DOM). Finnish locale, Europe/Helsinki.
export const SUPPORTED_SCHEMA = 1;
const TZ = "Europe/Helsinki";
const STALE_MS = 14 * 3_600_000;

const timeFmt = new Intl.DateTimeFormat("fi-FI", {
  timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const dateFmt = new Intl.DateTimeFormat("fi-FI", {
  timeZone: "UTC", weekday: "short", day: "numeric", month: "numeric",
});

function parts(fmt, d) {
  const o = {};
  for (const p of fmt.formatToParts(d)) o[p.type] = p.value;
  return o;
}

export function fmtTime(iso) {
  const p = parts(timeFmt, new Date(iso));
  return `${p.hour}.${p.minute}`;
}

// nhlDate is a calendar date ("2026-11-15"); it is formatted as given.
export function fmtDate(nhlDate) {
  const p = parts(dateFmt, new Date(`${nhlDate}T12:00:00Z`));
  return `${p.weekday.replace(/\.$/, "")} ${p.day}.${p.month}.`;
}

export const fmtPct = (p) => `${Math.round(p * 100)} %`;

export function fmtNum(x, digits = 1) {
  return x.toFixed(digits).replace(".", ",");
}

// Shown next to team-ordered UI, which lists the away team on the left: away–home.
export const fmtScore = ({ home, away }) => `${away}–${home}`;

export function fmtSignedPct(mult) {
  const pct = (mult - 1) * 100;
  const rounded = Math.round(Math.abs(pct) * 10) / 10;
  const sign = pct < 0 && rounded > 0 ? "−" : "+";
  return `${sign}${fmtNum(rounded, 1)} %`;
}

export function ageText(generatedAt, now = new Date()) {
  const mins = Math.max(0, Math.floor((now - new Date(generatedAt)) / 60_000));
  if (mins < 60) return `${mins} min sitten`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours} h sitten`;
  return `${Math.floor(hours / 24)} pv sitten`;
}

export function freshness(latest, now = new Date()) {
  const sources = latest.sources || [];
  const nhl = sources.find((s) => s.id === "nhl");
  const age = now - new Date(latest.generated_at);
  const old = !Number.isFinite(age) || age > STALE_MS;
  if (old || !nhl || nhl.status !== "ok") return "stale";
  if (sources.some((s) => s.status === "failed" || s.status === "stale") || latest.review?.worst === "error") return "warn";
  return "ok";
}

export function groupByDate(games) {
  const map = new Map();
  for (const g of games) {
    if (!map.has(g.nhl_date)) map.set(g.nhl_date, []);
    map.get(g.nhl_date).push(g);
  }
  return [...map.keys()].sort().map((d) => ({ nhl_date: d, games: map.get(d) }));
}

const dtFmt = new Intl.DateTimeFormat("fi-FI", {
  timeZone: TZ, day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

// "17.11. 02.00" in Helsinki time, for source timestamps.
export function fmtDateTime(iso) {
  const p = parts(dtFmt, new Date(iso));
  return `${p.day}.${p.month}. ${p.hour}.${p.minute}`;
}

/* ---------- odds helpers ---------- */
// Must equal config.ODDS_EV_MIN (pinned by tests/test_value.py).
export const VALUE_EV_MIN = 0.03;

export const fmtOdds = (x) => x.toFixed(2).replace(".", ",");
export const fmtPct1 = (p) => `${(p * 100).toFixed(1).replace(".", ",")} %`;

function signedOne(x) {
  const r = Math.round(Math.abs(x) * 10) / 10;
  const sign = x < 0 && r > 0 ? "−" : "+";
  return `${sign}${r.toFixed(1).replace(".", ",")}`;
}
// Probability difference in percentage points, one decimal.
export const fmtPp = (d) => signedOne(d * 100);
export const fmtSeason = (s) => `${s.slice(0, 4)}–${s.slice(6)}`;
export const drawGapLine = (d) =>
  `Malli ${fmtPct1(d.model)} · toteutunut ${fmtPct1(d.actual)} · ero ${fmtPp(d.gap)} %-yks.`;
export const fmtEv = (ev) => `${signedOne(ev * 100)} %`;

const BOOKS = {
  veikkaus_fi: "Veikkaus", unibet_fi: "Unibet", leovegas_fi: "LeoVegas", pinnacle: "Pinnacle",
  betsson: "Betsson", coolbet: "Coolbet", nordicbet: "NordicBet", unibet_se: "Unibet (SE)",
  betfair_ex_eu: "Betfair Exchange", marathonbet: "Marathonbet",
};
export const bookName = (key) => BOOKS[key] ?? key;

export const fairOdds = (p) => Math.round(100 / p) / 100;
export const minValuePrice = (p) => Math.ceil(((1 + VALUE_EV_MIN) / p) * 100 - 1e-9) / 100;

const lineText = (x) => fmtNum(Math.abs(x), 1);
const signedLine = (x) => `${x < 0 ? "−" : "+"}${lineText(x)}`;

export function pickLabel(pick, game) {
  const { market, outcome, line } = pick;
  const abbr = outcome === "home" ? game.home.abbr : game.away.abbr;
  if (market === "h2h") return `${abbr} voittaa`;
  if (market === "1x2") {
    return outcome === "draw"
      ? "Tasapeli varsinaisella peliajalla" : `${abbr} voittaa varsinaisella peliajalla`;
  }
  if (market === "totals") return `${outcome === "over" ? "Yli" : "Alle"} ${lineText(line)} maalia`;
  return `${abbr} ${signedLine(outcome === "home" ? line : -line)}`;
}

export function marketTitle(market, game) {
  switch (market.key) {
    case "h2h": return "Voittaja (sis. jatkoaika)";
    case "1x2": return "1X2 (varsinainen peliaika)";
    case "totals": return `Yli/alle ${lineText(market.line)}`;
    default: return `Tasoitus ${game.home.abbr} ${signedLine(market.line)}`;
  }
}

export function collectPicks(latest, { market = "all", veikkausOnly = false, showFlagged = false } = {}) {
  const out = [];
  for (const game of latest.games || []) {
    if (game.state === "started" || !game.odds) continue;
    for (const pick of game.odds.picks || []) {
      if (market !== "all" && pick.market !== market) continue;
      if (veikkausOnly && !pick.veikkaus?.value) continue;
      out.push({ game, pick });
    }
  }
  const key = (x) => (veikkausOnly ? x.pick.veikkaus.ev : x.pick.ev);
  const byEv = (a, b) => key(b) - key(a);
  const clean = out.filter((x) => !x.pick.flag).sort(byEv);
  if (!showFlagged) return clean;
  return clean.concat(out.filter((x) => x.pick.flag).sort(byEv));
}

// Newest odds fetch time among the games' odds blocks; falls back to the source's own time.
export function oddsFetchedAt(latest, fallback) {
  let best = null;
  for (const g of latest.games || []) {
    const t = g.odds?.fetched_at;
    if (t && (best === null || Date.parse(t) > Date.parse(best))) best = t;
  }
  return best ?? fallback ?? null;
}

/* ---------- player helpers ---------- */
export const playerName = (p) => p.name || `#${p.id}`;
export const fmtToi = (min) => `${fmtNum(min, 1)} min`;
export const pKey = (kind) => (kind === "goals" ? "p_goals" : "p_points");

export function topOf(list, kind, n = 5) {
  const k = pKey(kind);
  return [...list]
    .sort((a, b) => (b[k][0] - a[k][0]) || playerName(a).localeCompare(playerName(b), "fi"))
    .slice(0, n);
}

export function gamePlayers(game, scope) {
  const pl = game?.players;
  if (!pl) return [];
  const top = (t) => pl[t]?.top ?? [];
  if (scope === "away") return top("away");
  if (scope === "home") return top("home");
  return top("away").concat(top("home"));
}

export function tonightPlayers(latest, kind, n = 10) {
  const upcoming = (latest.games || []).filter((g) => g.state !== "started");
  if (!upcoming.length) return [];
  const first = groupByDate(upcoming)[0];
  const rows = [];
  for (const game of first.games) for (const player of gamePlayers(game, "game")) rows.push({ game, player });
  const k = pKey(kind);
  rows.sort((a, b) => (b.player[k][0] - a.player[k][0]) || playerName(a.player).localeCompare(playerName(b.player), "fi"));
  return rows.slice(0, n);
}
