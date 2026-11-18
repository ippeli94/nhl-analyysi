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

export const fmtScore = ({ home, away }) => `${home}–${away}`;

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
  const old = now - new Date(latest.generated_at) > STALE_MS;
  if (old || !nhl || nhl.status !== "ok") return "stale";
  if (sources.some((s) => s.status !== "ok") || latest.review?.worst === "error") return "warn";
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
