// Own bets: storage, settlement and statistics. Pure module, no DOM.
// Settlement mirrors pipeline/performance.settle (+ value.effective_price).

export const STORAGE_KEY = "nhl.bets.v1";
const COMMISSION = { betfair_ex_eu: 0.05 };
const MARKETS = ["h2h", "1x2", "totals", "spreads"];
const OUTCOMES = ["home", "away", "draw", "over", "under"];

const isNum = (x) => typeof x === "number" && Number.isFinite(x);

export function validBet(b) {
  return !!b && typeof b === "object" && typeof b.id === "string" && b.id !== ""
    && Number.isInteger(b.game_id)
    && MARKETS.includes(b.market) && OUTCOMES.includes(b.outcome)
    && isNum(b.price) && b.price > 1
    && isNum(b.stake) && b.stake > 0
    && (b.line === null || isNum(b.line));
}

function newId() {
  try {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
  } catch { /* fall through */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function loadBets(storage) {
  try {
    if (!storage) return [];
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const data = JSON.parse(raw);
    if (!data || data.v !== 1 || !Array.isArray(data.bets)) return [];
    return data.bets.filter(validBet);
  } catch {
    return [];
  }
}

export function saveBets(storage, bets) {
  try {
    if (!storage) return false;
    storage.setItem(STORAGE_KEY, JSON.stringify({ v: 1, bets }));
    return true;
  } catch {
    return false;
  }
}

export function addBet(bets, bet) {
  const b = { ...bet, id: newId() };
  if (!validBet(b)) throw new Error("Virheellinen veto");
  return [...bets, b];
}

export function removeBet(bets, id) {
  return bets.filter((b) => b.id !== id);
}

function effectivePrice(book, price) {
  return 1 + (price - 1) * (1 - (COMMISSION[book] || 0));
}

const VOID = { status: "void", profit: 0 };

export function settle(bet, result) {
  if (!bet || !result || typeof result !== "object") return VOID;
  const { market, outcome, line, stake } = bet;
  const hg = result.home_goals, ag = result.away_goals;
  let won;
  if (market === "h2h") {
    if (!isNum(hg) || !isNum(ag) || hg === ag) return VOID;
    if (outcome !== "home" && outcome !== "away") return VOID;
    won = (hg > ag) === (outcome === "home");
  } else if (market === "1x2") {
    const rh = result.reg_home_goals, ra = result.reg_away_goals;
    if (!isNum(rh) || !isNum(ra) || !["home", "draw", "away"].includes(outcome)) return VOID;
    won = (rh > ra ? "home" : ra > rh ? "away" : "draw") === outcome;
  } else if (market === "totals") {
    if (!isNum(line) || !isNum(hg) || !isNum(ag) || (outcome !== "over" && outcome !== "under")) return VOID;
    const total = hg + ag;
    if (total === line) return { status: "push", profit: 0 };
    won = (total > line) === (outcome === "over");
  } else if (market === "spreads") {
    if (!isNum(line) || !isNum(hg) || !isNum(ag) || (outcome !== "home" && outcome !== "away")) return VOID;
    const cover = hg - ag + line;
    if (cover === 0) return { status: "push", profit: 0 };
    won = (cover > 0) === (outcome === "home");
  } else {
    return VOID;
  }
  if (!won) return { status: "lost", profit: -stake };
  return { status: "won", profit: stake * (effectivePrice(bet.book, bet.price) - 1) };
}

function summarize(rows) {
  let n_settled = 0, staked = 0, profit = 0, hits = 0, decided = 0;
  for (const r of rows) {
    if (r.status === "won" || r.status === "lost" || r.status === "push") {
      n_settled += 1;
      staked += r.bet.stake;
      profit += r.profit;
      if (r.status !== "push") decided += 1;
      if (r.status === "won") hits += 1;
    }
  }
  return {
    n: rows.length, n_settled, staked, profit,
    roi: staked > 0 ? profit / staked : null,
    hits, hit_rate: decided > 0 ? hits / decided : null,
  };
}

export function evaluate(bets, results, now) {
  const games = results && Array.isArray(results.games) ? results.games : [];
  const byId = new Map();
  for (const g of games) if (g && g.id !== undefined) byId.set(g.id, g);
  const nowMs = now instanceof Date ? now.getTime() : Number(now);
  const rows = bets.map((bet) => {
    const g = byId.get(bet.game_id);
    if (g) {
      const s = settle(bet, g);
      return { bet, status: s.status, profit: s.profit };
    }
    const start = Date.parse(bet.start_utc);
    const status = Number.isFinite(start) && Number.isFinite(nowMs) && start > nowMs ? "open" : "waiting";
    return { bet, status, profit: 0 };
  });
  const byMarket = {};
  for (const m of MARKETS) {
    const sub = rows.filter((r) => r.bet.market === m);
    if (sub.length) byMarket[m] = summarize(sub);
  }
  return { rows, totals: summarize(rows), byMarket };
}

export function exportJson(bets) {
  return JSON.stringify({ v: 1, bets }, null, 2);
}

export function importJson(text, bets) {
  const fail = (error) => ({ bets, added: 0, skipped: 0, error });
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return fail("Tiedosto ei ole kelvollista JSONia.");
  }
  if (!data || typeof data !== "object" || data.v !== 1 || !Array.isArray(data.bets)) {
    return fail("Tiedosto ei ole tämän sivun vetovarmuuskopio.");
  }
  const have = new Set(bets.map((b) => b.id));
  const out = [...bets];
  let added = 0, skipped = 0;
  for (const b of data.bets) {
    if (!validBet(b) || have.has(b.id)) { skipped += 1; continue; }
    have.add(b.id);
    out.push(b);
    added += 1;
  }
  return { bets: out, added, skipped };
}
