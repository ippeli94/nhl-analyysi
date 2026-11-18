import {
  SUPPORTED_SCHEMA, fmtTime, fmtDate, fmtDateTime, fmtPct, fmtNum, fmtScore,
  fmtSignedPct, ageText, freshness, groupByDate,
  fmtOdds, fmtPct1, fmtPp, fmtEv, bookName, fairOdds, minValuePrice, pickLabel, marketTitle, collectPicks,
} from "./format.js";

const DATA_URL = "data/latest.json";
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
const TABS = [
  { id: "games", label: "Ottelut", href: "#/", icon: ICON_BALL },
  { id: "value", label: "Ylikertoimet", href: "#/ylikertoimet", icon: ICON_STAR },
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
    const tab = route.name === "value" ? "value" : (route.name === "list" || route.name === "game") ? "games" : null;
    const active = t.id === tab;
    return `<a href="${esc(t.href)}" class="${active ? "active" : ""}"${active ? ' aria-current="page"' : ""}>${t.icon}<span>${esc(t.label)}</span></a>`;
  }).join("");
}

/* ---------- list view ---------- */
function gameCard(g) {
  const flags = [
    g.state === "started" ? badge("Käynnissä — ennuste jäädytetty", "live") : "",
    confidenceBadge(g),
    g.state !== "started" && (g.odds?.picks || []).some((p) => !p.flag) ? badge("Ylikertoimia", "ok") : "",
  ].join("");
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

function pickCard({ game, pick }) {
  const v = pick.veikkaus;
  const flagged = !!pick.flag;
  return `<a class="card pick${flagged ? " flagged" : ""}" href="#/ottelu/${esc(game.id)}">` +
    `<div class="pick-row"><span class="pick-label">${esc(pickLabel(pick, game))} · ${esc(bookName(pick.book))} ${esc(fmtOdds(pick.price))}</span>` +
      `<span class="pick-ev">EV ${esc(fmtEv(pick.ev))}</span></div>` +
    `<div class="pick-sub">Malli ${esc(fmtPct1(pick.p_model))} · Markkina ${esc(fmtPct1(pick.p_market))} · edge ${esc(fmtPp(pick.edge))}</div>` +
    `<div class="pick-sub">${v ? `Veikkaus ${esc(fmtOdds(v.price))} (EV ${esc(fmtEv(v.ev))})` : "Veikkaus: ei kerrointa"}</div>` +
    `<div class="pick-sub">${esc(game.away.abbr)} @ ${esc(game.home.abbr)} · ${esc(fmtDate(game.nhl_date))} ${esc(fmtTime(game.start_utc))}</div>` +
    (flagged
      ? `<div class="pick-flag">${badge("Todennäköisemmin mallin virhe", "warn")}` +
        `<p class="note">Suuri ero markkinaan — todennäköisemmin mallin virhe tai puuttuva tieto kuin löytö.</p></div>`
      : "") +
    `</a>`;
}

function valueControls() {
  const seg = VALUE_MARKETS.map(([id, label]) =>
    `<button type="button" class="seg-btn" data-vmarket="${esc(id)}" aria-pressed="${valueFilter.market === id}">${esc(label)}</button>`).join("");
  const tog = (key, label) =>
    `<button type="button" class="toggle" data-vtoggle="${esc(key)}" aria-pressed="${valueFilter[key]}">${esc(label)}</button>`;
  return `<div class="seg" role="group" aria-label="Markkina">${seg}</div>` +
    `<div class="toggles">${tog("veikkausOnly", "Vain Veikkaus")}${tog("showFlagged", "Näytä varoitusleimalliset")}</div>`;
}

function valueView(latest) {
  const src = oddsSource(latest);
  const head = `<h1 class="day-title">Ylikertoimet</h1>`;
  if (!src) return `${head}<section class="empty"><p>Kertoimia ei ole vielä haettu.</p></section>`;
  const fetched = src.fetched_at ? `<p class="note fetched">Kertoimet haettu ${esc(fmtDateTime(src.fetched_at))}</p>` : "";
  const picks = collectPicks(latest, valueFilter);
  const body = picks.length
    ? `<div class="cards">${picks.map(pickCard).join("")}</div>`
    : `<section class="empty"><p>Ei ylikertoimia juuri nyt.</p></section>`;
  return head + fetched + valueControls() + body +
    `<p class="note foot">Markkina = Pinnaclen marginaaliton todennäköisyys, tai välittäjien mediaani jos Pinnacle puuttuu. ` +
    `Ylikerroin: EV vähintään 3 % ja ero markkinaan vähintään 2 prosenttiyksikköä.</p>`;
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
    return `<tr class="${o.value ? "is-value" : ""}"><th scope="row">${esc(label)}` +
      (o.flag ? `<br>${badge("Todennäköisemmin mallin virhe", "warn")}` : "") + `</th>` +
      `<td>${o.p_model != null ? esc(fmtPct1(o.p_model)) : dash}</td>` +
      `<td>${o.p_market != null ? esc(fmtPct1(o.p_market)) : dash}</td>` +
      `<td>${o.best ? `${esc(fmtOdds(o.best.price))} ${esc(bookName(o.best.book))}` : dash}</td>` +
      `<td>${o.veikkaus ? esc(fmtOdds(o.veikkaus.price)) : dash}</td>` +
      `<td>${o.ev != null ? esc(fmtEv(o.ev)) : dash}</td></tr>`;
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

// A section whose render() returns null is not shown (later-phase fields stay invisible).
const SECTIONS = [
  { id: "header", title: null, render: headerSection },
  { id: "score", title: "xG ja tulos", render: scoreSection },
  { id: "factors", title: "Mistä ennuste koostuu", render: (g) => (g.factors?.length ? factorsSection(g) : null) },
  { id: "periods", title: "Erät", render: (g) => (g.periods?.length ? periodsSection(g) : null) },
  {
    id: "goalies", title: "Maalivahdit",
    render: (g) => (g.goalies
      ? `<div class="goalies">${goalieCard(g.away, g.goalies.away)}${goalieCard(g.home, g.goalies.home)}</div>` : null),
  },
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
  return `<a class="back" href="#/">‹ Ottelut</a><section class="sec"><h1 class="day-title">Tila</h1><ul class="srcs">${rows}</ul>` +
    `<div class="stats one"><div class="stat"><span class="k">Tarkistusraportti</span><span class="v s">${esc(latest.review.problems)} ongelmaa</span></div>` +
    `<div class="stat"><span class="k">Mallin versio</span><span class="v s">${esc(latest.model_version)}</span></div></div></section>`;
}

/* ---------- router ---------- */
function parseRoute() {
  const h = location.hash || "#/";
  const m = h.match(/^#\/ottelu\/(\d+)$/);
  if (m) return { name: "game", id: Number(m[1]) };
  if (h === "#/tila") return { name: "status" };
  if (h === "#/ylikertoimet") return { name: "value" };
  return { name: "list" };
}

function errorView() {
  return `<section class="empty"><h1>Dataa ei saatu ladattua</h1><button class="btn" id="retry" type="button">Yritä uudelleen</button></section>`;
}

let lastKey = null;
function render() {
  const route = parseRoute();
  const key = location.hash;
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
  const btn = ev.target instanceof Element ? ev.target.closest("[data-vmarket], [data-vtoggle]") : null;
  if (!btn) return;
  if (btn.dataset.vmarket) valueFilter = { ...valueFilter, market: btn.dataset.vmarket };
  else valueFilter = { ...valueFilter, [btn.dataset.vtoggle]: !valueFilter[btn.dataset.vtoggle] };
  render();
});
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && Date.now() - state.loadedAt > RELOAD_AFTER_MS) {
    await load();
    render();
  }
});

render();
await load();
render();
