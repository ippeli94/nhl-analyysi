import {
  SUPPORTED_SCHEMA, fmtTime, fmtDate, fmtDateTime, fmtPct, fmtNum, fmtScore,
  fmtSignedPct, ageText, freshness, groupByDate,
  fmtOdds, fmtPct1, fmtPp, fmtEv, fmtSeason, drawGapLine, bookName, fairOdds, minValuePrice, pickLabel, marketTitle, collectPicks, oddsFetchedAt,
  playerName, fmtToi, topOf, gamePlayers, tonightPlayers,
  formSeq, pct1, signedPct, kindLabel, sourceLabel, stakeText, parseBankroll, fmtEur, fmtEurSigned,
} from "./format.js";
import { loadBets, saveBets, addBet, removeBet, evaluate, exportJson, importJson } from "./bets.js";

const DATA_URL = "data/latest.json";
const PERF_URL = "data/performance.json";
const RESULTS_URL = "data/results.json";
const RELOAD_AFTER_MS = 5 * 60_000;

const $ = (id) => document.getElementById(id);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

/* ---------- navigation (later phases add entries) ---------- */
const ICON_BALL =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/>' +
  '<path d="M3.5 12h17M12 3.5c3 2.6 3 14.4 0 17M12 3.5c-3 2.6-3 14.4 0 17"/></svg>';
const ICON_STAR =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9 6.8 19.7l1-5.9L3.5 9.7l5.9-.8z"/></svg>';
const ICON_PERSON =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.6"/>' +
  '<path d="M4.5 20c0-4 3.4-6.5 7.5-6.5s7.5 2.5 7.5 6.5"/></svg>';
const ICON_CHART =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V4M4 20h16"/><path d="M8 15l3.5-4 3 2.5L19 7"/></svg>';
const ICON_BET =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="3.5" width="14" height="17" rx="2"/>' +
  '<path d="M8.5 9h7M8.5 13h7M8.5 16.5h4"/></svg>';
const TABS = [
  { id: "games", label: "Ottelut", href: "#/", icon: ICON_BALL },
  { id: "players", label: "Pelaajat", href: "#/pelaajat", icon: ICON_PERSON },
  { id: "value", label: "Ylikertoimet", href: "#/ylikertoimet", icon: ICON_STAR },
  { id: "model", label: "Malli", href: "#/malli", icon: ICON_CHART },
  { id: "bets", label: "Vedot", href: "#/vedot", icon: ICON_BET },
];

/* ---------- state ---------- */
let state = { latest: null, error: null, schemaError: null, loadedAt: 0 };

async function load() {
  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.schema_version !== SUPPORTED_SCHEMA) {
      state = { latest: null, error: null, schemaError: data.schema_version, loadedAt: Date.now() };
    } else {
      state = { latest: data, error: null, schemaError: null, loadedAt: Date.now() };
    }
  } catch (e) {
    state = { latest: null, error: e, schemaError: null, loadedAt: Date.now() };
  }
}

/* ---------- small building blocks ---------- */
function logo(team, cls = "") {
  const fallback = `<span class="logo logo-fb ${cls}" aria-hidden="true">${esc(team.abbr)}</span>`;
  if (!team.logo && !team.logo_dark) return fallback;
  const dark = team.logo_dark
    ? `<source media="(prefers-color-scheme: dark)" srcset="${esc(team.logo_dark)}">` : "";
  const light = team.logo || team.logo_dark;
  return `<picture class="logo-wrap ${cls}" data-abbr="${esc(team.abbr)}">${dark}` +
    `<img class="logo" src="${esc(light)}" alt="${esc(team.name || team.abbr)} -logo" loading="lazy" width="48" height="48"></picture>`;
}

// Remote logos are the only remote assets: replace a broken one with the abbreviation.
document.addEventListener("error", (ev) => {
  const img = ev.target;
  if (!(img instanceof HTMLImageElement) || !img.classList.contains("logo")) return;
  const wrap = img.closest("picture") || img;
  const abbr = wrap.dataset?.abbr || img.alt || "";
  const span = document.createElement("span");
  span.className = "logo logo-fb";
  span.textContent = abbr.slice(0, 3);
  wrap.replaceWith(span);
}, true);

function badge(text, kind = "") {
  return `<span class="badge ${kind}">${esc(text)}</span>`;
}

function confidenceBadge(game) {
  return game.prob.confidence === "low" ? badge("Pieni otos", "warn") : "";
}

function probBar(away, home) {
  const a = Math.round(away * 1000) / 10;
  const h = Math.round(home * 1000) / 10;
  return `<div class="pbar" role="img" aria-label="Vierasjoukkue ${fmtPct(away)}, kotijoukkue ${fmtPct(home)}">` +
    `<span class="pbar-a${away > home ? " lead" : ""}" style="flex:${a}"></span>` +
    `<span class="pbar-h${home >= away ? " lead" : ""}" style="flex:${h}"></span></div>`;
}

/* ---------- chrome ---------- */
function renderChrome(route) {
  const latest = state.latest;
  const top = $("topbar");
  const banner = $("banner");
  if (!latest) {
    top.innerHTML = `<span class="brand">NHL-analyysi</span>`;
    banner.innerHTML = "";
  } else {
    const now = new Date();
    const fresh = freshness(latest, now);
    const label = { ok: "Data ajan tasalla", warn: "Osa lähteistä ei kunnossa", stale: "Data vanhentunut" }[fresh];
    top.innerHTML =
      `<span class="brand">NHL-analyysi</span>` +
      `<a class="updated" href="#/tila" aria-label="${esc(label)}. Avaa lähteiden tila">` +
      `<span class="updated-text">Päivitetty ${esc(fmtTime(latest.generated_at))} (${esc(ageText(latest.generated_at, now))})</span>` +
      `<span class="dot dot-${fresh}" aria-hidden="true"></span></a>`;
    banner.innerHTML = fresh === "stale"
      ? `<div class="banner" role="alert">Data on yli 14 tuntia vanhaa tai NHL-haku epäonnistui — ennusteet voivat olla vanhentuneita.</div>`
      : "";
  }
  $("nav").innerHTML = TABS.map((t) => {
    const tab = { value: "value", players: "players", model: "model", bets: "bets", list: "games", game: "games" }[route.name] ?? null;
    const active = t.id === tab;
    return `<a href="${esc(t.href)}" class="${active ? "active" : ""}"${active ? ' aria-current="page"' : ""}>${t.icon}<span>${esc(t.label)}</span></a>`;
  }).join("");
}

/* ---------- list view ---------- */
function gameCard(g) {
  // Max two badges, by priority: live, ylikerroin, first notable insight, small sample.
  const top = (g.insights || []).find((i) => i.severity >= 2);
  const flags = [
    g.state === "started" ? badge("Käynnissä — ennuste jäädytetty", "live") : "",
    g.state !== "started" && (g.odds?.picks || []).some((p) => !p.flag) ? badge("Ylikertoimia", "ok") : "",
    top ? `<span class="badge insight" title="${esc(top.text)}">${esc(top.text)}</span>` : "",
    confidenceBadge(g),
  ].filter(Boolean).slice(0, 2).join("");
  const awayLead = g.prob.away_win > g.prob.home_win;
  return `<a class="card" href="#/ottelu/${esc(g.id)}">` +
    `<div class="card-time">${esc(fmtTime(g.start_utc))}</div>` +
    `<div class="card-main">` +
      `<div class="side side-a"><span class="abbr">${esc(g.away.abbr)}</span><span class="pct${awayLead ? " lead" : ""}">${esc(fmtPct(g.prob.away_win))}</span></div>` +
      probBar(g.prob.away_win, g.prob.home_win) +
      `<div class="side side-h"><span class="abbr">${esc(g.home.abbr)}</span><span class="pct${awayLead ? "" : " lead"}">${esc(fmtPct(g.prob.home_win))}</span></div>` +
    `</div>` +
    `<div class="card-meta"><span>xG ${esc(fmtNum(g.xg.away))} – ${esc(fmtNum(g.xg.home))}</span>` +
    `<span>todennäk. ${esc(fmtScore(g.score.most_likely))}</span></div>` +
    (flags ? `<div class="card-flags">${flags}</div>` : "") +
    `</a>`;
}

function listView(latest) {
  if (!latest.games.length) {
    return `<section class="empty"><h1>Ei otteluita.</h1>` +
      (latest.next_game_date ? `<p>Seuraava pelipäivä: ${esc(fmtDate(latest.next_game_date))}</p>` : "") +
      `</section>`;
  }
  return groupByDate(latest.games).map((grp, i) =>
    `<section class="day"><h1 class="day-title">${i === 0 ? "Ensi yön ottelut · " : ""}${esc(fmtDate(grp.nhl_date))}</h1>` +
    `<div class="cards">${grp.games.map(gameCard).join("")}</div></section>`).join("");
}

/* ---------- value view ---------- */
const oddsSource = (latest) => (latest.sources || []).find((s) => s.id === "odds") || null;
const VALUE_MARKETS = [
  ["all", "Kaikki"], ["h2h", "Voittaja"], ["1x2", "1X2"], ["totals", "Yli/alle"], ["spreads", "Tasoitus"],
];
let valueFilter = { market: "all", veikkausOnly: false, showFlagged: false };
const BANKROLL_KEY = "nhl.bankroll";
function loadBankroll() {
  try { return parseBankroll(localStorage.getItem(BANKROLL_KEY)); } catch { return null; }
}
function saveBankroll(text) {
  try {
    const n = parseBankroll(text);
    if (n == null) localStorage.removeItem(BANKROLL_KEY); else localStorage.setItem(BANKROLL_KEY, String(n));
  } catch { /* storage unavailable */ }
}
let bankroll = loadBankroll();

/* ---------- own (imaginary) bets: placement UI ---------- */
function storage() { try { return localStorage; } catch { return null; } }
let bets = loadBets(storage());
let betTargets = [];                 // registry rebuilt on every render; buttons refer by index
let betForm = null;                  // { key, book, price, stake, error, ... }
let betFlash = null;                 // { key, until }

const targetKey = (t) => `${t.game.id}|${t.market}|${t.line ?? ""}|${t.outcome}`;
const betKey = (b) => `${b.game_id}|${b.market}|${b.line ?? ""}|${b.outcome}`;
const isPlaced = (t) => bets.some((b) => betKey(b) === targetKey(t));

function betLabel(t, book) {
  return `${t.game.away.abbr} @ ${t.game.home.abbr} · ${pickLabel(t, t.game)} · ${bookName(book)}`;
}

// options: [{src, book, price, stake (share of bankroll | null), text}]
function betControls(t, options) {
  if (t.game.state !== "upcoming") return "";
  const key = targetKey(t);
  const placed = isPlaced(t);
  const flash = betFlash && betFlash.key === key && betFlash.until > Date.now()
    ? `<span class="bet-ok" role="status">Veto tallennettu</span>` : "";
  const btns = options.map((o, i) => {
    const idx = betTargets.push({ ...t, ...o, key }) - 1;
    const done = i === 0 && placed;
    return `<button type="button" class="bet-btn${done ? " done" : ""}" data-bet-open="${idx}">${esc(done ? "Vedetty ✓" : o.text)}</button>`;
  }).join("");
  const form = betForm && betForm.key === key ? betFormHtml(t) : "";
  return `<div class="bet-area"><div class="bet-actions">${btns}${flash}</div>${form}</div>`;
}

function betFormHtml(t) {
  return `<form class="bet-form" data-bet-form novalidate>` +
    `<p class="bet-label">${esc(betLabel(t, betForm.book))}</p>` +
    `<div class="bet-fields"><label>Panos (€)<input type="text" inputmode="decimal" id="bet-stake" value="${esc(betForm.stake)}" autocomplete="off"></label>` +
    `<label>Kerroin<input type="text" inputmode="decimal" id="bet-price" value="${esc(betForm.price)}" autocomplete="off"></label></div>` +
    `<p class="note">Välittäjä: ${esc(bookName(betForm.book))}</p>` +
    (betForm.error ? `<p class="bet-err" role="alert">${esc(betForm.error)}</p>` : "") +
    `<div class="bet-actions"><button type="submit" class="bet-btn primary">Tallenna veto</button>` +
    `<button type="button" class="bet-btn" data-bet-cancel>Peruuta</button></div></form>`;
}

function openBetForm(idx) {
  const o = betTargets[idx];
  if (!o) return;
  const stake = bankroll != null && o.stake != null ? Math.round(o.stake * bankroll) : 0;
  betForm = {
    key: o.key, book: o.book, stake: stake > 0 ? String(stake) : "", price: String(o.price).replace(".", ","), error: null,
    p_model: o.p_model, market: o.market, line: o.line, outcome: o.outcome, gameId: o.game.id,
  };
  render();
  const el = $("bet-stake");
  if (el) el.focus({ preventScroll: true });
}

function saveBetForm() {
  const f = betForm;
  if (!f) return;
  f.stake = $("bet-stake")?.value ?? f.stake;
  f.price = $("bet-price")?.value ?? f.price;
  const g = state.latest?.games.find((x) => x.id === f.gameId);
  const stake = parseBankroll(f.stake);
  const price = parseBankroll(f.price);
  if (!g || g.state !== "upcoming") { f.error = "Ottelu on jo alkanut."; return render(); }
  if (stake == null) { f.error = "Anna panos euroina (esim. 10 tai 12,5)."; return render(); }
  if (price == null || price <= 1) { f.error = "Kertoimen pitää olla yli 1 (esim. 1,95)."; return render(); }
  const t = { game: g, market: f.market, line: f.line, outcome: f.outcome };
  let next;
  try {
    next = addBet(bets, {
      placed_at: new Date().toISOString(), game_id: g.id, start_utc: g.start_utc,
      home: g.home.abbr, away: g.away.abbr, market: f.market, line: f.line, outcome: f.outcome,
      book: f.book, price, stake, p_model: f.p_model, label: betLabel(t, f.book),
    });
  } catch {
    f.error = "Vedon tiedot eivät kelpaa.";
    return render();
  }
  if (!saveBets(storage(), next)) { f.error = "Tallennus selaimeen ei onnistunut."; return render(); }
  bets = next;
  betFlash = { key: f.key, until: Date.now() + 4000 };
  betForm = null;
  render();
  setTimeout(() => { if (betFlash && betFlash.until <= Date.now()) { betFlash = null; render(); } }, 4100);
}

function pickCard({ game, pick }) {  // uses module-level bankroll
  const v = pick.veikkaus;
  const flagged = !!pick.flag;
  const target = { game, market: pick.market, line: pick.line ?? null, outcome: pick.outcome, p_model: pick.p_model ?? null };
  const betUi = betControls(target, [
    { src: "best", book: pick.book, price: pick.price, stake: pick.stake, text: "Lyö veto" },
    ...(v ? [{ src: "veik", book: "veikkaus_fi", price: v.price, stake: v.stake, text: `Veikkaus ${fmtOdds(v.price)}` }] : []),
  ]);
  return `<div class="pick-wrap"><a class="card pick${flagged ? " flagged" : ""}" href="#/ottelu/${esc(game.id)}">` +
    `<div class="pick-row"><span class="pick-label">${esc(pickLabel(pick, game))} · ${esc(bookName(pick.book))} ${esc(fmtOdds(pick.price))}</span>` +
      `<span class="pick-ev">EV ${esc(fmtEv(pick.ev))}</span></div>` +
    `<div class="pick-sub">Malli ${esc(fmtPct1(pick.p_model))} · Markkina ${esc(fmtPct1(pick.p_market))} · edge ${esc(fmtPp(pick.edge))}</div>` +
    `<div class="pick-sub">Panos: ${esc(stakeText(pick.stake, bankroll))}</div>` +
    `<div class="pick-sub">${v ? `Veikkaus ${esc(fmtOdds(v.price))} (EV ${esc(fmtEv(v.ev))}) · Panos: ${esc(stakeText(v.stake, bankroll))}` : "Veikkaus: ei kerrointa"}</div>` +
    `<div class="pick-sub">${esc(game.away.abbr)} @ ${esc(game.home.abbr)} · ${esc(fmtDate(game.nhl_date))} ${esc(fmtTime(game.start_utc))}</div>` +
    (flagged
      ? `<div class="pick-flag">${badge("Todennäköisemmin mallin virhe", "warn")}` +
        `<p class="note">Suuri ero markkinaan — todennäköisemmin mallin virhe tai puuttuva tieto kuin löytö.</p></div>`
      : "") +
    `</a>${betUi}</div>`;
}

function valueControls() {
  const seg = VALUE_MARKETS.map(([id, label]) =>
    `<button type="button" class="seg-btn" data-vmarket="${esc(id)}" aria-pressed="${valueFilter.market === id}">${esc(label)}</button>`).join("");
  const tog = (key, label) =>
    `<button type="button" class="toggle" data-vtoggle="${esc(key)}" aria-pressed="${valueFilter[key]}">${esc(label)}</button>`;
  return `<div class="seg" role="group" aria-label="Markkina">${seg}</div>` +
    `<div class="toggles">${tog("veikkausOnly", "Vain Veikkaus")}${tog("showFlagged", "Näytä varoitusleimalliset")}</div>` +
    `<label class="bankroll">Kassa (€) <input type="text" inputmode="decimal" id="bankroll" size="8" value="${esc(bankroll ?? "")}"></label>`;
}

function valueView(latest) {
  const src = oddsSource(latest);
  const head = `<h1 class="day-title">Ylikertoimet</h1>`;
  if (!src) return `${head}<section class="empty"><p>Kertoimia ei ole vielä haettu.</p></section>`;
  const fetchedAt = oddsFetchedAt(latest, src.fetched_at);
  const fetched = fetchedAt ? `<p class="note fetched">Kertoimet haettu ${esc(fmtDateTime(fetchedAt))}</p>` : "";
  const picks = collectPicks(latest, valueFilter);
  const body = picks.length
    ? `<div class="cards">${picks.map(pickCard).join("")}</div>`
    : `<section class="empty"><p>Ei ylikertoimia juuri nyt.</p></section>`;
  return head + fetched + valueControls() + body +
    `<p class="note foot">Markkina = Pinnaclen marginaaliton todennäköisyys, tai välittäjien mediaani jos Pinnacle puuttuu. ` +
    `Ylikerroin: EV vähintään 3 % ja ero markkinaan vähintään 2 prosenttiyksikköä.</p>` +
    `<p class="note foot">Panos = ¼ Kelly kutistetulla todennäköisyydellä (malli ja markkina puoliksi), enintään 3 % kassasta. Ei sijoitusneuvo.</p>`;
}

/* ---------- players ---------- */
let playerFilter = { kind: "points", scope: "game" };
const PLAYER_KINDS = [["points", "Pisteet"], ["goals", "Maalit"]];

function playerRow(p, kind) {
  const k = kind === "goals" ? p.p_goals : p.p_points;
  return `<tr><th scope="row"><span class="pname">${esc(playerName(p))}</span>` +
    `<span class="psub">${esc(p.team)} · ${esc(p.pos)} · ${esc(fmtToi(p.toi_proj))}</span></th>` +
    `<td>${esc(fmtPct(k[0]))}</td><td>${esc(fmtPct(k[1]))}</td><td>${esc(fmtPct(k[2]))}</td></tr>`;
}

function playersSection(g) {
  if (!g.players) return null;
  const { kind, scope } = playerFilter;
  const scopes = [["game", "Ottelu"], ["away", g.away.abbr], ["home", g.home.abbr]];
  const kinds = PLAYER_KINDS.map(([id, label]) =>
    `<button type="button" class="seg-btn" data-pkind="${esc(id)}" aria-pressed="${kind === id}">${esc(label)}</button>`).join("");
  const scs = scopes.map(([id, label]) =>
    `<button type="button" class="seg-btn" data-pscope="${esc(id)}" aria-pressed="${scope === id}">${esc(label)}</button>`).join("");
  let body;
  if (scope !== "game" && !g.players[scope]) {
    body = `<p class="note">Ei pelaajadataa: joukkueella ei ole vielä pelattuja otteluita tällä kaudella.</p>`;
  } else {
    const list = topOf(gamePlayers(g, scope), kind, Infinity);
    const head = `<thead><tr><th scope="col">Pelaaja</th><th scope="col">1+</th><th scope="col">2+</th><th scope="col">3+</th></tr></thead>`;
    const table = (rows) => `<div class="twrap"><table class="otable ptable">${head}<tbody>${rows.map((p) => playerRow(p, kind)).join("")}</tbody></table></div>`;
    const rest = list.slice(5);
    body = table(list.slice(0, 5)) +
      (rest.length ? `<details class="books"><summary>Näytä kaikki (${esc(list.length)})</summary>${table(rest)}</details>` : "");
  }
  return `<div class="seg" role="group" aria-label="Pelaajaennuste">${kinds}</div>` +
    `<div class="seg" role="group" aria-label="Rajaus">${scs}</div>` +
    `<div class="pbadge">${badge("Kokoonpano: arvio", "muted")}</div>` + body +
    `<p class="note foot">Kokoonpano ja peliajat on arvioitu joukkueen viimeisimmästä ottelusta. ` +
    `Luvut ovat todennäköisyyksiä saada ottelussa vähintään 1, 2 tai 3 pistettä (tai maalia), jatkoaika mukaan lukien.</p>`;
}

function playersView(latest) {
  const first = groupByDate((latest.games || []).filter((g) => g.state !== "started"))[0];
  const head = `<h1 class="day-title">Pelaajat</h1>` +
    (first ? `<p class="note fetched">Ensi yön ottelut · ${esc(fmtDate(first.nhl_date))}</p>` : "");
  const card = (title, sub, kind) => {
    const rows = tonightPlayers(latest, kind, 10);
    const list = rows.length
      ? `<ol class="plist">${rows.map(({ game, player }, i) => {
        const k = kind === "goals" ? player.p_goals : player.p_points;
        return `<li><a href="#/ottelu/${esc(game.id)}"><span class="prank">${i + 1}.</span>` +
          `<span class="pmain"><span class="pname">${esc(playerName(player))}</span>` +
          `<span class="psub">${esc(player.team)} · ${esc(game.away.abbr)} @ ${esc(game.home.abbr)} · ${esc(fmtTime(game.start_utc))}</span></span>` +
          `<span class="ppct">${esc(fmtPct(k[0]))}</span></a></li>`;
      }).join("")}</ol>`
      : `<p class="note">Pelaajaennusteita ei ole vielä saatavilla.</p>`;
    return `<section class="pcard"><h2 class="ptitle">${esc(title)}</h2><p class="psub">${esc(sub)}</p>${list}</section>`;
  };
  return head + card("Todennäköisimmät pistemiehet", "vähintään 1 piste", "points") +
    card("Todennäköisimmät maalintekijät", "vähintään 1 maali", "goals") +
    `<p class="note foot">Kokoonpanot ovat arvioita. Pelaajavetojen kertoimia ei vertailla — ilmainen kerroinlähde ei tarjoa niitä.</p>`;
}

/* ---------- detail sections ---------- */
function headerSection(g) {
  const awayLead = g.prob.away_win > g.prob.home_win;
  return `<div class="hero">` +
    `<div class="hero-meta"><span>${esc(fmtDate(g.nhl_date))} · ${esc(fmtTime(g.start_utc))}</span>${confidenceBadge(g)}` +
    (g.state === "started" ? badge("Käynnissä — ennuste jäädytetty", "live") : "") + `</div>` +
    `<div class="hero-teams">` +
      `<div class="hero-team">${logo(g.away, "lg")}<div class="hero-name">${esc(g.away.name || g.away.abbr)}</div>` +
        `<div class="hero-pct${awayLead ? " lead" : ""}">${esc(fmtPct(g.prob.away_win))}</div></div>` +
      `<div class="hero-at" aria-hidden="true">@</div>` +
      `<div class="hero-team">${logo(g.home, "lg")}<div class="hero-name">${esc(g.home.name || g.home.abbr)}</div>` +
        `<div class="hero-pct${awayLead ? "" : " lead"}">${esc(fmtPct(g.prob.home_win))}</div></div>` +
    `</div>` + probBar(g.prob.away_win, g.prob.home_win) +
    `<p class="reg">Varsinainen peliaika: 1 ${esc(fmtPct(g.prob.reg_home))} · X ${esc(fmtPct(g.prob.reg_draw))} · 2 ${esc(fmtPct(g.prob.reg_away))}</p>` +
    `</div>`;
}

function xgBars(g) {
  const max = Math.max(g.xg.home, g.xg.away, 0.01);
  const row = (abbr, v, cls) =>
    `<div class="xrow"><span class="xabbr">${esc(abbr)}</span><span class="xtrack"><span class="xfill ${cls}" style="width:${(v / max * 100).toFixed(1)}%"></span></span><span class="xval">${esc(fmtNum(v, 2))}</span></div>`;
  return `<div class="xg"><h3 class="sub">Odotetut maalit (xG)</h3>${row(g.away.abbr, g.xg.away, "a")}${row(g.home.abbr, g.xg.home, "h")}</div>`;
}

function heatMap(g) {
  const m = g.score.matrix;
  const max = Math.max(...m.flat(), 0.0001);
  const best = g.score.most_likely;
  const C = 38, off = 26, size = off + 8 * C;
  const lab = (i) => (i === 7 ? "7+" : String(i));
  let svg = `<svg class="heat" viewBox="0 0 ${size} ${size}" role="img" aria-label="Tulosmatriisi: kotijoukkueen maalit riveinä, vierasjoukkueen sarakkeina">`;
  for (let i = 0; i < 8; i++) {
    svg += `<text class="axis" x="${off + i * C + C / 2}" y="${off - 9}" text-anchor="middle">${lab(i)}</text>`;
    svg += `<text class="axis" x="${off - 8}" y="${off + i * C + C / 2 + 4}" text-anchor="end">${lab(i)}</text>`;
  }
  for (let h = 0; h < 8; h++) {
    for (let a = 0; a < 8; a++) {
      const p = m[h][a];
      const x = off + a * C, y = off + h * C;
      const isBest = best.home === h && best.away === a;
      svg += `<rect class="cell${isBest ? " best" : ""}" x="${x + 1}" y="${y + 1}" width="${C - 2}" height="${C - 2}" rx="5" style="fill-opacity:${(0.06 + 0.94 * p / max).toFixed(3)}"/>`;
      if (p >= 0.01) {
        svg += `<text class="cellv${p / max > 0.55 ? " on" : ""}" x="${x + C / 2}" y="${y + C / 2 + 4}" text-anchor="middle">${esc(fmtNum(p * 100, p >= 0.1 ? 0 : 1))}</text>`;
      }
    }
  }
  svg += `</svg>`;
  return `<div class="heatwrap"><h3 class="sub">Tulosmatriisi (%)</h3>` +
    `<div class="heat-axes"><span>↓ ${esc(g.home.abbr)} (koti)</span><span>${esc(g.away.abbr)} (vieras) →</span></div>${svg}</div>`;
}

function scoreSection(g) {
  const s = g.score;
  const top = s.top.map((c) =>
    `<li><span class="sc">${esc(fmtScore(c))}</span><span class="scbar"><span style="width:${(c.p / s.top[0].p * 100).toFixed(1)}%"></span></span><span class="scp">${esc(fmtNum(c.p * 100))} %</span></li>`).join("");
  const sp = s.spread;
  return xgBars(g) +
    `<div class="ml"><span class="k">Todennäköisin tulos</span><span class="v">${esc(fmtScore(s.most_likely))}</span><span class="k2">${esc(fmtNum(s.most_likely.p * 100))} %</span></div>` +
    `<h3 class="sub">5 todennäköisintä tulosta</h3><ol class="top">${top}</ol>` +
    heatMap(g) +
    `<div class="stats">` +
      `<div class="stat"><span class="k">Maaleja keskimäärin</span><span class="v">${esc(fmtNum(s.total.mean))}</span></div>` +
      `<div class="stat"><span class="k">Yli 5,5</span><span class="v">${esc(fmtPct(s.total.over["5.5"]))}</span></div>` +
      `<div class="stat"><span class="k">Yli 6,5</span><span class="v">${esc(fmtPct(s.total.over["6.5"]))}</span></div>` +
      `<div class="stat"><span class="k">${esc(g.away.abbr)} −1,5</span><span class="v">${esc(fmtPct(sp["away_-1.5"]))}</span></div>` +
      `<div class="stat"><span class="k">${esc(g.home.abbr)} −1,5</span><span class="v">${esc(fmtPct(sp["home_-1.5"]))}</span></div>` +
    `</div>`;
}

function factorsSection(g) {
  const val = (f, v) => {
    if (f.unit === "goals") return `<span class="fv">${esc(fmtNum(v, 2))}</span>`;
    const txt = fmtSignedPct(v);
    const cls = Math.abs(v - 1) < 0.0005 ? "" : txt.startsWith("−") ? " neg" : " pos";
    return `<span class="fv${cls}">${esc(txt)}</span>`;
  };
  const rows = g.factors.map((f) =>
    `<div class="frow">${val(f, f.away)}<span class="fl">${esc(f.label)}${f.unit === "goals" ? "<small>maalia</small>" : ""}</span>${val(f, f.home)}</div>`).join("");
  return `<div class="fhead"><span>${esc(g.away.abbr)}</span><span></span><span>${esc(g.home.abbr)}</span></div>${rows}` +
    `<p class="note">Joukkuevahvuus on maaliodotus ennen muita tekijöitä; muut rivit ovat prosenttivaikutuksia maaliodotukseen.</p>`;
}

function periodsSection(g) {
  return g.periods.map((p) => {
    const a = p.p_away_wins_period * 100, d = p.p_draw * 100, h = p.p_home_wins_period * 100;
    return `<div class="period"><div class="period-h"><span class="pn">${esc(p.n)}. erä</span>` +
      `<span class="pxg">xG ${esc(fmtNum(p.xg_away, 2))} – ${esc(fmtNum(p.xg_home, 2))}</span></div>` +
      `<div class="tri" role="img" aria-label="${esc(g.away.abbr)} ${esc(fmtPct(p.p_away_wins_period))}, tasan ${esc(fmtPct(p.p_draw))}, ${esc(g.home.abbr)} ${esc(fmtPct(p.p_home_wins_period))}">` +
      `<span class="a" style="flex:${a}"></span><span class="d" style="flex:${d}"></span><span class="h" style="flex:${h}"></span></div>` +
      `<div class="tri-l"><span>${esc(g.away.abbr)} ${esc(fmtPct(p.p_away_wins_period))}</span><span>Tasan ${esc(fmtPct(p.p_draw))}</span><span>${esc(g.home.abbr)} ${esc(fmtPct(p.p_home_wins_period))}</span></div></div>`;
  }).join("");
}

const GOALIE_STATUS = {
  confirmed: ["Vahvistettu", "ok"], likely: ["Todennäköinen", "info"], projected: ["Arvio", "muted"],
};
function signed(x, digits = 2) {
  const t = fmtNum(Math.abs(x), digits);
  return x < 0 && Number(t.replace(",", ".")) !== 0 ? `−${t}` : `+${t}`;
}

function goalieCard(team, goalie) {
  if (!goalie) {
    return `<div class="goalie"><div class="goalie-h"><span class="abbr">${esc(team.abbr)}</span></div><p class="muted">Ei maalivahtitietoa</p></div>`;
  }
  const [stText, stKind] = GOALIE_STATUS[goalie.status] || [goalie.status, "muted"];
  return `<div class="goalie"><div class="goalie-h"><span class="abbr">${esc(team.abbr)}</span>` +
    `<span class="gname">${esc(goalie.name ?? "Tuntematon")}</span>${badge(stText, stKind)}</div>` +
    (goalie.p_start < 1 ? `<p>aloittaa ${esc(fmtPct(goalie.p_start))} todennäköisyydellä</p>` : "") +
    (goalie.gsax_per60 != null ? `<p>Torjunnat yli odotuksen / 60 min: <strong>${esc(signed(goalie.gsax_per60))}</strong></p>` : "") +
    `<p>Vaikutus vastustajan maaliodotukseen: <strong>${esc(fmtSignedPct(goalie.mult))}</strong></p></div>`;
}

const dash = "—";
const ONE_X_TWO = [["home", "1", "reg_home"], ["draw", "X", "reg_draw"], ["away", "2", "reg_away"]];

function oneXTwoBlock(g) {
  const market = (g.odds?.markets || []).find((m) => m.key === "1x2");
  const cols = ONE_X_TWO.map(([key, label, probKey]) => {
    const p = g.prob[probKey];
    const o = market?.outcomes.find((x) => x.key === key);
    return `<div class="ox-col"><span class="ox-h">${label}</span>` +
      `<span class="ox-v">Malli ${esc(fmtPct1(p))}</span>` +
      `<span class="ox-v">Reilu kerroin ${esc(fmtOdds(fairOdds(p)))}</span>` +
      `<span class="ox-v muted">Ylikerroin, kun kerroin ≥ ${esc(fmtOdds(minValuePrice(p)))}</span>` +
      (market
        ? `<span class="ox-v">Paras ${o?.best ? `${esc(bookName(o.best.book))} ${esc(fmtOdds(o.best.price))}` : dash}</span>` +
          `<span class="ox-v">EV ${o?.ev != null ? esc(fmtEv(o.ev)) : dash}</span>`
        : "") +
      `</div>`;
  }).join("");
  return `<h3 class="sub first">1X2 · varsinainen peliaika</h3><div class="ox">${cols}</div>` +
    (market ? "" : `<p class="note">Kerroinlähde ei tarjoa NHL:n 1X2-markkinaa. Vertaa Veikkauksen 1X2-kerrointa reiluun kertoimeen.</p>`);
}

function marketTable(m, g) {
  const rows = m.outcomes.map((o) => {
    const label = pickLabel({ market: m.key, outcome: o.key, line: m.line }, g);
    const t = { game: g, market: m.key, line: m.line ?? null, outcome: o.key, p_model: o.p_model ?? null };
    const opts = [
      ...(o.best ? [{ src: "best", book: o.best.book, price: o.best.price, stake: o.stake ?? null, text: "Lyö veto" }] : []),
      ...(o.veikkaus ? [{ src: "veik", book: "veikkaus_fi", price: o.veikkaus.price, stake: o.veikkaus.stake ?? null, text: `Veikkaus ${fmtOdds(o.veikkaus.price)}` }] : []),
    ];
    const ui = opts.length ? betControls(t, opts) : "";
    return `<tr class="${o.value ? "is-value" : ""}"><th scope="row">${esc(label)}` +
      (o.flag ? `<br>${badge("Todennäköisemmin mallin virhe", "warn")}` : "") + `</th>` +
      `<td>${o.p_model != null ? esc(fmtPct1(o.p_model)) : dash}</td>` +
      `<td>${o.p_market != null ? esc(fmtPct1(o.p_market)) : dash}</td>` +
      `<td>${o.best ? `${esc(fmtOdds(o.best.price))} ${esc(bookName(o.best.book))}` : dash}</td>` +
      `<td>${o.veikkaus ? esc(fmtOdds(o.veikkaus.price)) : dash}</td>` +
      `<td>${o.ev != null ? esc(fmtEv(o.ev)) : dash}</td></tr>` +
      (ui ? `<tr class="bet-row"><td colspan="6">${ui}</td></tr>` : "");
  }).join("");
  const books = (m.books || []).map((b) =>
    `<li>${esc(bookName(b.book))}: ${esc((b.prices || []).map(fmtOdds).join(" / "))}` +
    `${b.vig != null ? ` · marginaali ${esc(fmtPct1(b.vig))}` : ""}${b.valid ? "" : " (ei käytetä)"}</li>`).join("");
  return `<h3 class="sub">${esc(marketTitle(m, g))}</h3>` +
    `<div class="twrap"><table class="otable"><thead><tr><th scope="col">Valinta</th><th scope="col">Malli</th>` +
    `<th scope="col">Markkina</th><th scope="col">Paras</th><th scope="col">Veikkaus</th><th scope="col">EV</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></div>` +
    `<details class="books"><summary>Kaikki välittäjät</summary><ul>${books}</ul></details>`;
}

function oddsSection(g) {
  const first = oneXTwoBlock(g);
  if (!g.odds) return first + `<p class="note">Kertoimia ei ole saatavilla tälle ottelulle.</p>`;
  const refs = g.odds.markets.map((m) => m.ref);
  const ref = refs.includes("pinnacle") ? "Viite: Pinnacle" : refs.includes("median") ? "Viite: välittäjien mediaani" : "Ei viitettä";
  return first + g.odds.markets.map((m) => marketTable(m, g)).join("") +
    `<p class="note foot">Kertoimet haettu ${esc(fmtDateTime(g.odds.fetched_at))} · ${esc(ref)}</p>`;
}

/* ---------- context sections (insights, lineup, form, season, h2h) ---------- */
const SEV_LABEL = { 1: "Pieni", 2: "Keskisuuri", 3: "Suuri" };

function insightsSection(g) {
  if (g.insights == null) return null;
  if (!g.insights.length) return `<p class="note first">Ei nostoja</p>`;
  return `<ul class="insights">${g.insights.map((i) =>
    `<li><span class="sev sev-${esc(i.severity)}" role="img" aria-label="Merkitys: ${esc(SEV_LABEL[i.severity] || i.severity)}"></span>` +
    `<span class="itext">${i.team ? `<strong>${esc(i.team)}</strong> ` : ""}${esc(i.text)}</span></li>`).join("")}</ul>`;
}

const KIND_BADGE = { out: "bad", doubtful: "warn", returning: "ok", acquired: "info", traded_away: "muted" };

function lineupRow(c) {
  const impacts = [
    c.impact_attack != null ? `Hyökkäys ${signedPct(c.impact_attack)}` : "",
    c.impact_defense != null ? `Puolustus: vastustajalle ${signedPct(c.impact_defense)}` : "",
  ].filter(Boolean).join(" · ");
  return `<li class="lrow"><div class="lrow-h"><span class="abbr">${esc(c.team)}</span>` +
    `<span class="lname">${esc(c.player)}</span>${badge(kindLabel(c.kind), KIND_BADGE[c.kind] || "muted")}` +
    `${badge(sourceLabel(c.source), "muted")}</div>` +
    (c.detail ? `<p class="ldetail">${esc(c.detail)}</p>` : "") +
    (impacts ? `<p class="limpact">${esc(impacts)}</p>` : "") + `</li>`;
}

function lineupSection(g) {
  if (g.lineup_changes == null) return null;
  if (!g.lineup_changes.length) return `<p class="note first">Ei tiedossa olevia poissaoloja</p>`;
  return `<ul class="lineup">${g.lineup_changes.map(lineupRow).join("")}</ul>` +
    `<p class="note">Hyökkäys = muutos joukkueen omaan maaliodotukseen. Puolustus = muutos vastustajan maaliodotukseen.</p>`;
}

function lineupWithGoalies(g) {
  const goalies = g.goalies
    ? `<div class="goalies">${goalieCard(g.away, g.goalies.away)}${goalieCard(g.home, g.goalies.home)}</div>` : "";
  const lineup = lineupSection(g);
  if (!goalies && lineup === null) return null;
  return goalies + (lineup === null ? "" : `<h3 class="sub">Poissaolot ja kokoonpanomuutokset</h3>${lineup}`);
}

const FORM_TITLE = {
  V: "Voitto", H: "Tappio varsinaisella peliajalla", J: "Tappio jatkoajalla tai voittolaukauskilpailussa",
};

function formColumn(abbr, f, cls) {
  if (!f) return `<div class="fcol"><h3 class="fcol-h ${cls}">${esc(abbr)}</h3><p class="muted">Ei dataa</p></div>`;
  const chips = formSeq(f.seq).map((c) =>
    `<span class="chip ${esc(c.cls)}" title="${esc(FORM_TITLE[c.letter] || "")}">${esc(c.letter)}</span>`).join("");
  return `<div class="fcol"><h3 class="fcol-h ${cls}">${esc(abbr)}</h3>` +
    `<div class="chips" aria-label="Viimeisimmät ottelut, uusin ensin">${chips}</div>` +
    `<dl class="kv"><dt>Viim. 10 (V-H-J)</dt><dd>${esc(f.last10)}</dd>` +
    `<dt>Maalit</dt><dd>${esc(f.gf)}–${esc(f.ga)}</dd>` +
    `<dt>xG %</dt><dd>${esc(pct1(f.xgf_pct))}</dd>` +
    `<dt>Putki</dt><dd>${esc(f.streak ?? "—")}</dd></dl></div>`;
}

function formSection(g) {
  if (g.form == null) return null;
  return `<div class="fcols">${formColumn(g.away.abbr, g.form.away, "a")}${formColumn(g.home.abbr, g.form.home, "h")}</div>` +
    `<p class="note">V voitto · H tappio varsinaisella peliajalla · J tappio jatkoajalla tai voittolaukauskilpailussa. Uusin ottelu vasemmalla.</p>`;
}

const goals2 = (v) => (v == null ? dash : fmtNum(v, 2));
const SEASON_ROWS = [
  ["Pistepros.", "pts_pct", pct1], ["Maalit/ottelu", "gf_pg", goals2],
  ["Päästetyt/ottelu", "ga_pg", goals2], ["xG %", "xgf_pct", pct1],
  ["YV %", "pp", pct1], ["AV %", "pk", pct1],
];

function seasonTable(abbr, sc, cls) {
  const cell = (s, key, fmt) => `<td>${s ? esc(fmt(s[key])) : dash}</td>`;
  const rows = SEASON_ROWS.map(([label, key, fmt]) =>
    `<tr><th scope="row">${esc(label)}</th>${cell(sc?.now, key, fmt)}${cell(sc?.last, key, fmt)}</tr>`).join("");
  return `<h3 class="sub tname ${cls}">${esc(abbr)}</h3><div class="twrap"><table class="otable stable">` +
    `<thead><tr><th scope="col"></th><th scope="col">Nyt</th><th scope="col">Viime kausi</th></tr></thead><tbody>${rows}</tbody></table></div>` +
    `<p class="note">${sc?.now ? `Tällä kaudella ${esc(sc.now.gp)} ottelua` : "Ei pelattuja otteluita tällä kaudella"}` +
    `${sc?.last ? ` · viime kaudella ${esc(sc.last.gp)}` : ""}</p>`;
}

function seasonSection(g) {
  if (g.season_compare == null) return null;
  return seasonTable(g.away.abbr, g.season_compare.away, "a") + seasonTable(g.home.abbr, g.season_compare.home, "h");
}

// Pipeline strings are home-away ("4-2", "3.1-2.4"); the UI lists away–home like fmtScore.
const swapDash = (s) => String(s).split("-").reverse().join("–").replace(/\./g, ",");

function h2hSection(g) {
  if (g.h2h == null) return null;
  const rows = (g.h2h.games || []).map((m) =>
    `<tr><td>${esc(fmtDate(m.date))}</td><td>${esc(m.away)} ${esc(swapDash(m.score))} ${esc(m.home)}</td>` +
    `<td>${m.xg ? `xG ${esc(swapDash(m.xg))}` : dash}</td></tr>`).join("");
  return (g.h2h.summary ? `<p class="h2h-sum">${esc(g.h2h.summary)}</p>` : "") +
    (rows ? `<div class="twrap"><table class="otable"><tbody>${rows}</tbody></table></div>` : `<p class="note first">Ei aiempia kohtaamisia</p>`);
}

// A section whose render() returns null is not shown (later-phase fields stay invisible).
const SECTIONS = [
  { id: "header", title: null, render: headerSection },
  { id: "score", title: "xG ja tulos", render: scoreSection },
  { id: "factors", title: "Mistä ennuste koostuu", render: (g) => (g.factors?.length ? factorsSection(g) : null) },
  { id: "insights", title: "Nostot", render: insightsSection },
  { id: "periods", title: "Erät", render: (g) => (g.periods?.length ? periodsSection(g) : null) },
  { id: "players", title: "Pelaajat", render: playersSection },
  { id: "lineup", title: "Maalivahdit ja poissaolot", render: lineupWithGoalies },
  { id: "form", title: "Vire", render: formSection },
  { id: "season", title: "Kausivertailu", render: seasonSection },
  { id: "h2h", title: "Keskinäiset", render: h2hSection },
  { id: "odds", title: "Kertoimet", render: oddsSection },
];

function gameView(g) {
  const sections = SECTIONS.map((s) => {
    const html = s.render(g);
    if (html === null) return "";
    return `<section class="sec sec-${esc(s.id)}">${s.title ? `<h2>${esc(s.title)}</h2>` : ""}${html}</section>`;
  }).join("");
  return `<a class="back" href="#/">‹ Ottelut</a>${sections}`;
}


/* ---------- model tab (Malli) ---------- */
let perf = { status: "idle", data: null };   // idle | loading | ok | error
let perfWindow = "30d";

async function loadPerf() {
  if (perf.status !== "idle") return;
  perf = { status: "loading", data: null };
  try {
    const res = await fetch(PERF_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    perf = { status: "ok", data: await res.json() };
  } catch {
    perf = { status: "error", data: null };
  }
  if (parseRoute().name === "model") render();
}

const num3 = (x) => (x == null ? dash : fmtNum(x, 3));
const swapScore = (s) => String(s).split("-").reverse().join("–");

function metricTile(label, main, subs) {
  return `<div class="stat"><span class="k">${esc(label)}</span><span class="v">${esc(main)}</span>` +
    subs.map((t) => `<span class="k">${esc(t)}</span>`).join("") + `</div>`;
}

function calibrationChart(bins) {
  if (!bins?.length) return `<p class="note">Ei vielä riittävästi ratkenneita otteluita kalibrointikuvaan.</p>`;
  const S = 240, pad = 30, span = S - pad * 1.4;
  const x = (v) => (pad + v * span).toFixed(1);
  const y = (v) => (S - pad - v * span).toFixed(1);
  const maxN = Math.max(...bins.map((b) => b.n));
  let svg = `<svg class="calib" viewBox="0 0 ${S} ${S}" role="img" aria-label="Kalibrointi: ennustettu todennäköisyys vaaka-akselilla, toteutunut osuus pystyakselilla">`;
  for (const t of [0, 0.5, 1]) {
    svg += `<line class="grid" x1="${x(0)}" y1="${y(t)}" x2="${x(1)}" y2="${y(t)}"/>` +
      `<line class="grid" x1="${x(t)}" y1="${y(0)}" x2="${x(t)}" y2="${y(1)}"/>` +
      `<text class="axis" x="${x(t)}" y="${S - pad + 14}" text-anchor="middle">${t * 100}</text>` +
      `<text class="axis" x="${pad - 6}" y="${(Number(y(t)) + 4).toFixed(1)}" text-anchor="end">${t * 100}</text>`;
  }
  svg += `<line class="diag" x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}"/>`;
  svg += `<polyline class="cline" points="${bins.map((b) => `${x(b.p_mean)},${y(b.y_mean)}`).join(" ")}"/>`;
  for (const b of bins) {
    svg += `<circle class="cdot" cx="${x(b.p_mean)}" cy="${y(b.y_mean)}" r="${(3 + 4 * Math.sqrt(b.n / maxN)).toFixed(1)}"><title>${esc(`Ennuste ${fmtPct(b.p_mean)}, toteutunut ${fmtPct(b.y_mean)} (${b.n} ottelua)`)}</title></circle>`;
  }
  svg += `<text class="axis" x="${x(0.5)}" y="${S - 2}" text-anchor="middle">Ennuste (%)</text></svg>`;
  return svg + `<p class="note">Diagonaali = täydellinen kalibrointi. Pisteen koko kertoo otteluiden määrän.</p>`;
}

function pickRow(label, p) {
  return `<tr><th scope="row">${esc(label)}</th><td>${p ? esc(p.n) : dash}</td>` +
    `<td>${p && p.hit_rate != null ? esc(pct1(p.hit_rate)) : dash}</td><td>${p && p.roi != null ? esc(signedPct(p.roi)) : dash}</td></tr>`;
}

function recentList(recent) {
  if (!recent?.length) return `<p class="note first">Ei vielä ratkenneita otteluita.</p>`;
  return `<ul class="recent">${recent.slice(0, 15).map((r) =>
    `<li><span class="rdate">${esc(fmtDate(r.date))}</span>` +
    `<span class="rmain"><strong>${esc(r.away)} @ ${esc(r.home)}</strong>` +
    `<span class="psub">Ennuste: ${esc(r.home)} ${esc(fmtPct(r.p_home))} → ${esc(swapScore(r.score))}</span></span>` +
    `<span class="hit ${r.hit ? "yes" : "no"}" role="img" aria-label="${r.hit ? "Osui" : "Ei osunut"}">${r.hit ? "✓" : "✗"}</span></li>`).join("")}</ul>`;
}

function backtestBlock(b) {
  if (!b) return "";
  return `<section class="sec backtest"><h2>${esc(b.label)}</h2>` +
    `<p class="note first">Mallin ajo menneelle kaudelle takautuvasti (${esc(b.n)} ottelua) — ei oikeita ennusteita.</p>` +
    `<div class="stats">${metricTile("Brier", num3(b.brier), [`Perustaso ${num3(b.brier_base)}`])}` +
    `${metricTile("Log loss", num3(b.logloss), [`Perustaso ${num3(b.logloss_base)}`])}</div></section>`;
}

function modelView() {
  const head = `<h1 class="day-title">Malli</h1>`;
  if (perf.status === "idle" || perf.status === "loading") return `${head}<p class="loading">Ladataan…</p>`;
  if (perf.status === "error" || !perf.data) {
    return `${head}<section class="empty"><p>Suorituskykydataa ei saatu ladattua</p>` +
      `<button type="button" class="seg-btn" id="perf-retry">Yritä uudelleen</button></section>`;
  }
  const d = perf.data;
  const w = d.windows?.[perfWindow];
  const toggle = `<div class="seg" role="group" aria-label="Aikaväli">` +
    [["30d", "30 pv"], ["season", "Kausi"]].map(([id, label]) =>
      `<button type="button" class="seg-btn" data-pwindow="${id}" aria-pressed="${perfWindow === id}">${label}</button>`).join("") + `</div>`;
  let body;
  if (!w) {
    body = `<p class="note first">Ei dataa valitulle aikavälille.</p>`;
  } else {
    const tiles = [
      metricTile("Brier (pienempi on parempi)", num3(w.brier), [`Perustaso ${num3(w.brier_base)}`, `Markkina ${num3(w.brier_market)}`]),
      metricTile("Log loss", num3(w.logloss), [`Perustaso ${num3(w.logloss_base)}`, `Markkina ${num3(w.logloss_market)}`]),
      metricTile("Osumatarkkuus", pct1(w.accuracy), [`Markkina ${pct1(w.accuracy_market)}`]),
      metricTile("CLV, keskiarvo", w.clv_mean == null ? dash : signedPct(w.clv_mean), [`${w.clv_n ?? 0} vetoa`]),
      metricTile("Pelaajat, Brier", w.players ? num3(w.players.brier) : dash,
        w.players ? [`Perustaso ${num3(w.players.brier_base)}`, `${w.players.n} ennustetta`] : ["Ei dataa"]),
      metricTile("Maalimäärän MAE", w.total_mae == null ? dash : fmtNum(w.total_mae, 2), ["Kokonaismaalit"]),
      metricTile("Maalimäärä", w.totals ? `${pct1(w.totals.model_hit_rate)}` : dash,
        w.totals ? [`Yli/alle osumat: malli ${pct1(w.totals.model_hit_rate)} · markkina ${pct1(w.totals.market_hit_rate)}`,
          `${w.totals.n} ottelua, keskiraja ${w.totals.line_mean == null ? dash : fmtNum(w.totals.line_mean, 2)}`] : ["Ei dataa"]),
    ].join("");
    const pk = w.picks || {};
    body = (w.small_sample ? `<div class="banner inline" role="status">Otos pieni — luvut eivät vielä kerro paljoa</div>` : "") +
      `<p class="note first">${esc(w.n)} ratkennutta ottelua${w.n_market ? `, joista ${esc(w.n_market)} vertailtavissa markkinaan` : ""}.</p>` +
      `<div class="stats">${tiles}</div>` +
      `<h3 class="sub">Ylikerroinpoiminnat</h3><div class="twrap"><table class="otable"><thead><tr><th scope="col">Poiminnat</th><th scope="col">n</th><th scope="col">Osumat</th><th scope="col">ROI</th></tr></thead><tbody>` +
      `${pickRow("Kaikki", pk.all)}${pickRow("Ilman varoitusta", pk.no_flag)}${pickRow("Vain Veikkaus", pk.veikkaus)}</tbody></table></div>` +
      `<h3 class="sub">Kalibrointi</h3>${calibrationChart(w.calibration)}`;
  }
  return head + `<p class="note fetched">Mallin versio ${esc(d.model_version)} · päivitetty ${esc(fmtDateTime(d.generated_at))}</p>` + toggle +
    `<section class="sec sec-perf">${body}</section>` +
    `<section class="sec"><h2>Viimeisimmät ennusteet</h2>${recentList(d.recent)}</section>` + backtestBlock(d.backtest);
}

/* ---------- bets tab (Vedot) ---------- */
let results = { status: "idle", data: null };   // idle | loading | ok | error
let betsMsg = null;                              // { text, kind } after import/export

async function loadResults() {
  if (results.status !== "idle") return;
  results = { status: "loading", data: null };
  try {
    const res = await fetch(RESULTS_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    results = { status: "ok", data: await res.json() };
  } catch {
    results = { status: "error", data: null };
  }
  if (parseRoute().name === "bets") render();
}

const BET_MARKET_NAMES = { h2h: "Voittaja", "1x2": "1X2", totals: "Yli/alle", spreads: "Tasoitus" };
const BET_STATUS = {
  open: ["Avoin", "info"], waiting: ["Odottaa tulosta", "muted"], won: ["✓ Voitto", "ok"],
  lost: ["✗ Tappio", "bad"], push: ["Palautus", "muted"], void: ["Mitätön", "muted"],
};

function betRow(r, deletable) {
  const b = r.bet;
  const [txt, kind] = BET_STATUS[r.status] || [r.status, "muted"];
  const settled = !["open", "waiting"].includes(r.status);
  const profit = settled ? `<span class="bet-profit ${r.profit > 0 ? "pos" : r.profit < 0 ? "neg" : ""}">${esc(fmtEurSigned(r.profit))}</span>` : "";
  return `<li class="bet-item"><div class="bet-item-h"><span class="bet-item-label">${esc(b.label)}</span>${badge(txt, kind)}</div>` +
    `<div class="bet-item-sub"><span>${esc(fmtDateTime(b.start_utc))} · Panos ${esc(fmtEur(b.stake))} @ ${esc(fmtOdds(b.price))}</span>${profit}</div>` +
    (deletable ? `<button type="button" class="bet-btn danger" data-bet-del="${esc(b.id)}">Poista veto</button>` : "") + `</li>`;
}

function betsView() {
  const head = `<h1 class="day-title">Vedot</h1>`;
  const ev = evaluate(bets, results.status === "ok" ? results.data : null, new Date());
  const t = ev.totals;
  const pending = ev.rows.filter((r) => r.status === "open" || r.status === "waiting");
  const nOpen = pending.filter((r) => r.status === "open").length;
  const settled = ev.rows.filter((r) => !["open", "waiting"].includes(r.status))
    .sort((a, b) => Date.parse(b.bet.start_utc) - Date.parse(a.bet.start_utc));
  pending.sort((a, b) => Date.parse(a.bet.start_utc) - Date.parse(b.bet.start_utc));
  const tile = (k, v, sub) => `<div class="stat"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>${sub ? `<span class="k">${esc(sub)}</span>` : ""}</div>`;
  const tiles = `<div class="stats">` +
    tile("Panostettu", fmtEur(t.staked), `${t.n_settled} ratkennutta`) +
    tile("Tulos", fmtEurSigned(t.profit), "") +
    tile("ROI", t.roi == null ? "—" : signedPct(t.roi), "") +
    tile("Osumat", t.hit_rate == null ? "—" : pct1(t.hit_rate), `${t.hits} voittoa`) +
    tile("Kesken", String(pending.length), `avoimia ${nOpen} · odottaa tulosta ${pending.length - nOpen}`) +
    `</div>`;
  const resultsNote = results.status === "error"
    ? `<p class="note">Tuloksia ei saatu ladattua — vedot näkyvät tilassa "Odottaa tulosta". <button type="button" class="seg-btn" id="results-retry">Yritä uudelleen</button></p>`
    : results.status !== "ok" ? `<p class="note">Ladataan tuloksia…</p>` : "";
  const list = (rows, del) => rows.length ? `<ul class="bet-list">${rows.map((r) => betRow(r, del && r.status === "open")).join("")}</ul>` : "";
  const market = Object.entries(ev.byMarket).map(([m, s]) =>
    `<tr><th scope="row">${esc(BET_MARKET_NAMES[m] || m)}</th><td>${esc(s.n)}</td><td>${esc(fmtEurSigned(s.profit))}</td><td>${s.roi == null ? "—" : esc(signedPct(s.roi))}</td></tr>`).join("");
  const msg = betsMsg ? `<p class="bet-msg ${betsMsg.kind}" role="status">${esc(betsMsg.text)}</p>` : "";
  return head + resultsNote + tiles +
    `<section class="sec"><h2>Avoimet ja odottavat</h2>${pending.length ? list(pending, true) : `<p class="note first">Ei avoimia vetoja. Lyö veto Ylikertoimet-välilehdellä tai ottelun kertoimista.</p>`}</section>` +
    `<section class="sec"><h2>Ratkenneet</h2>${settled.length ? list(settled, false) : `<p class="note first">Ei vielä ratkenneita vetoja.</p>`}</section>` +
    (market ? `<section class="sec"><h2>Markkinoittain</h2><div class="twrap"><table class="otable"><thead><tr><th scope="col">Markkina</th><th scope="col">Vetoja</th><th scope="col">Tulos</th><th scope="col">ROI</th></tr></thead><tbody>${market}</tbody></table></div></section>` : "") +
    `<section class="sec"><h2>Varmuuskopio</h2><div class="bet-actions">` +
    `<button type="button" class="bet-btn" id="bets-export">Vie varmuuskopio</button>` +
    `<label class="bet-btn file">Tuo varmuuskopio<input type="file" id="bets-import" accept="application/json"></label></div>${msg}` +
    `<p class="note">Vedot tallentuvat vain tähän selaimeen — ota varmuuskopio.</p></section>`;
}

function downloadBackup() {
  const d = new Date();
  const ymd = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const url = URL.createObjectURL(new Blob([exportJson(bets)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `nhl-vedot-${ymd}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  betsMsg = { text: `Varmuuskopio ladattu (${bets.length} vetoa).`, kind: "ok" };
  render();
}

async function importBackup(file) {
  if (!file) return;
  let text;
  try { text = await file.text(); } catch { betsMsg = { text: "Tiedoston lukeminen epäonnistui.", kind: "bad" }; return render(); }
  const r = importJson(text, bets);
  if (r.error) betsMsg = { text: r.error, kind: "bad" };
  else if (r.added > 0 && !saveBets(storage(), r.bets)) betsMsg = { text: "Tallennus selaimeen ei onnistunut.", kind: "bad" };
  else {
    if (r.added > 0) bets = r.bets;
    betsMsg = { text: `Tuotu ${r.added} vetoa, ohitettu ${r.skipped}.`, kind: "ok" };
  }
  render();
}

/* ---------- status view ---------- */
const SOURCE_NAMES = { nhl: "NHL", injuries: "Loukkaantumiset (ESPN)", odds: "Kertoimet (The Odds API)" };
const SOURCE_STATUS = {
  ok: ["Kunnossa", "ok"], stale: ["Vanhentunut", "warn"],
  failed: ["Epäonnistui", "bad"], unavailable: ["Ei saatavilla", "muted"],
};

function statusView(latest) {
  const rows = latest.sources.map((s) => {
    const [txt, kind] = SOURCE_STATUS[s.status] || [s.status, "muted"];
    return `<li class="src"><div class="src-h"><span class="src-n">${esc(SOURCE_NAMES[s.id] || s.id)}</span>${badge(txt, kind)}</div>` +
      (s.fetched_at ? `<div class="src-t">Haettu ${esc(fmtDateTime(s.fetched_at))}</div>` : "") +
      (s.note ? `<div class="src-note">${esc(s.note)}</div>` : "") + `</li>`;
  }).join("");
  const d = latest.calibration?.draw;
  const drawTile = d
    ? `<div class="stat"><span class="k">${esc(`Tasapelit, kausi ${fmtSeason(d.season)} (${d.n} ottelua)`)}</span><span class="v s">${esc(drawGapLine(d))}</span>${d.warn ? badge("Poikkeama yli 2 %-yks.", "warn") : ""}</div>`
    : `<div class="stat"><span class="k">Tasapelit</span><span class="v s">Ei vielä riittävästi ratkenneita ennusteita</span></div>`;
  return `<a class="back" href="#/">‹ Ottelut</a><section class="sec"><h1 class="day-title">Tila</h1><ul class="srcs">${rows}</ul>` +
    `<div class="stats one">${drawTile}</div>` +
    `<div class="stats one"><div class="stat"><span class="k">Tarkistusraportti</span><span class="v s">${esc(latest.review.problems)} ongelmaa</span></div>` +
    `<div class="stat"><span class="k">Mallin versio</span><span class="v s">${esc(latest.model_version)}</span></div></div></section>`;
}

/* ---------- router ---------- */
function parseRoute() {
  const h = location.hash || "#/";
  const m = h.match(/^#\/ottelu\/(\d+)$/);
  if (m) return { name: "game", id: Number(m[1]) };
  if (h === "#/tila") return { name: "status" };
  if (h === "#/pelaajat") return { name: "players" };
  if (h === "#/ylikertoimet") return { name: "value" };
  if (h === "#/malli") return { name: "model" };
  if (h === "#/vedot") return { name: "bets" };
  return { name: "list" };
}

function errorView() {
  return `<section class="empty"><h1>Dataa ei saatu ladattua</h1><button class="btn" id="retry" type="button">Yritä uudelleen</button></section>`;
}

let lastKey = null;
function render() {
  const route = parseRoute();
  const key = location.hash;
  betTargets = [];
  let html;
  let title = "NHL-analyysi";
  if (state.schemaError !== null) {
    html = `<section class="empty"><h1>Sivu ja data ovat eri versiota (skeema ${esc(state.schemaError)}). Päivitä sivu.</h1></section>`;
  } else if (!state.latest) {
    html = state.error ? errorView() : `<p class="loading">Ladataan…</p>`;
  } else if (route.name === "game") {
    const g = state.latest.games.find((x) => x.id === route.id);
    if (!g) { location.hash = "#/"; return; }
    html = gameView(g);
    title = `${g.away.abbr} @ ${g.home.abbr} · NHL-analyysi`;
  } else if (route.name === "value") {
    html = valueView(state.latest);
    title = "Ylikertoimet · NHL-analyysi";
  } else if (route.name === "model") {
    html = modelView();
    title = "Malli · NHL-analyysi";
    loadPerf();
  } else if (route.name === "bets") {
    html = betsView();
    title = "Vedot · NHL-analyysi";
    loadResults();
  } else if (route.name === "players") {
    html = playersView(state.latest);
    title = "Pelaajat · NHL-analyysi";
  } else if (route.name === "status") {
    html = statusView(state.latest);
  } else {
    html = listView(state.latest);
  }
  renderChrome(route);
  $("view").innerHTML = html;
  document.title = title;
  if (key !== lastKey) window.scrollTo(0, 0);
  lastKey = key;
  if (route.name !== "model" && perf.status === "error") perf = { status: "idle", data: null };
  if (route.name !== "bets") betsMsg = null;
  if (route.name !== "bets" && results.status === "error") results = { status: "idle", data: null };
  const resRetry = $("results-retry");
  if (resRetry) {
    resRetry.addEventListener("click", () => {
      results = { status: "idle", data: null };
      render();
    });
  }
  const perfRetry = $("perf-retry");
  if (perfRetry) {
    perfRetry.addEventListener("click", () => {
      perf = { status: "idle", data: null };
      render();
    });
  }
  const retry = $("retry");
  if (retry) {
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      await load();
      render();
    });
  }
}

window.addEventListener("hashchange", render);
$("view").addEventListener("click", (ev) => {
  const tgt = ev.target instanceof Element ? ev.target : null;
  const open = tgt?.closest("[data-bet-open]");
  if (open) { ev.preventDefault(); openBetForm(Number(open.dataset.betOpen)); return; }
  if (tgt?.closest("[data-bet-cancel]")) { betForm = null; render(); return; }
  const del = tgt?.closest("[data-bet-del]");
  if (del) {
    if (confirm("Poistetaanko veto?")) {
      const next = removeBet(bets, del.dataset.betDel);
      if (saveBets(storage(), next)) bets = next;
      render();
    }
    return;
  }
  if (tgt?.closest("#bets-export")) { downloadBackup(); return; }
  const btn = ev.target instanceof Element ? ev.target.closest("[data-vmarket], [data-vtoggle], [data-pkind], [data-pscope], [data-pwindow]") : null;
  if (!btn) return;
  if (btn.dataset.pwindow) perfWindow = btn.dataset.pwindow;
  else if (btn.dataset.pkind) playerFilter = { ...playerFilter, kind: btn.dataset.pkind };
  else if (btn.dataset.pscope) playerFilter = { ...playerFilter, scope: btn.dataset.pscope };
  else if (btn.dataset.vmarket) valueFilter = { ...valueFilter, market: btn.dataset.vmarket };
  else valueFilter = { ...valueFilter, [btn.dataset.vtoggle]: !valueFilter[btn.dataset.vtoggle] };
  render();
});
$("view").addEventListener("submit", (ev) => {
  if (ev.target instanceof Element && ev.target.matches("[data-bet-form]")) { ev.preventDefault(); saveBetForm(); }
});
$("view").addEventListener("input", (ev) => {
  const t = ev.target;
  if (!betForm || !(t instanceof HTMLInputElement)) return;
  if (t.id === "bet-stake") betForm.stake = t.value;
  else if (t.id === "bet-price") betForm.price = t.value;
});
$("view").addEventListener("change", (ev) => {
  const t = ev.target;
  if (t instanceof HTMLInputElement && t.id === "bets-import") { const f = t.files?.[0]; t.value = ""; importBackup(f); return; }
  if (!(t instanceof Element) || t.id !== "bankroll") return;
  saveBankroll(t.value);
  bankroll = parseBankroll(t.value);
  render();
});
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && Date.now() - state.loadedAt > RELOAD_AFTER_MS) {
    await load();
    if (perf.status === "ok") perf = { status: "idle", data: null };   // refetch on next Malli render
    if (results.status === "ok") results = { status: "idle", data: null };
    render();
  }
});

render();
await load();
render();
