import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fmtTime, fmtDate, fmtPct, fmtNum, fmtScore, fmtSignedPct, ageText,
  freshness, groupByDate, fmtDateTime, SUPPORTED_SCHEMA,
  fmtOdds, fmtPct1, fmtPp, fmtEv, bookName, fairOdds, minValuePrice, pickLabel, marketTitle, collectPicks,
} from "../format.js";

const NOW = new Date("2026-11-16T12:00:00Z");
const ago = (ms) => new Date(NOW.getTime() - ms).toISOString();
const MIN = 60_000, H = 3_600_000;
const latest = (over = {}) => ({
  generated_at: ago(30 * MIN),
  sources: [
    { id: "nhl", status: "ok" },
    { id: "injuries", status: "ok" },
  ],
  review: { problems: 0, worst: null },
  ...over,
});

test("schema constant", () => assert.equal(SUPPORTED_SCHEMA, 1));
test("fmtTime winter", () => assert.equal(fmtTime("2026-11-16T00:00:00Z"), "02.00"));
test("fmtTime summer DST", () => assert.equal(fmtTime("2026-04-12T23:00:00Z"), "02.00"));
test("fmtDate", () => assert.equal(fmtDate("2026-11-15"), "su 15.11."));
test("fmtPct", () => assert.equal(fmtPct(0.642), "64 %"));
test("fmtNum uses comma", () => assert.equal(fmtNum(2.61), "2,6"));
test("fmtNum digits", () => assert.equal(fmtNum(2.614, 2), "2,61"));
test("fmtSignedPct", () => {
  assert.equal(fmtSignedPct(0.976), "−2,4 %");
  assert.equal(fmtSignedPct(1.01), "+1,0 %");
});
test("fmtScore prints away-home to match the left-right team order", () =>
  assert.equal(fmtScore({ home: 3, away: 2 }), "2–3"));
test("ageText", () => {
  assert.equal(ageText(ago(12 * MIN), NOW), "12 min sitten");
  assert.equal(ageText(ago(3 * H), NOW), "3 h sitten");
  assert.equal(ageText(ago(50 * H), NOW), "2 pv sitten");
});
test("freshness ok", () => assert.equal(freshness(latest(), NOW), "ok"));
test("freshness stale after 14 h", () =>
  assert.equal(freshness(latest({ generated_at: ago(15 * H) }), NOW), "stale"));
test("freshness not stale at 13 h", () =>
  assert.equal(freshness(latest({ generated_at: ago(13 * H) }), NOW), "ok"));
test("freshness stale when nhl source failed", () =>
  assert.equal(freshness(latest({ sources: [{ id: "nhl", status: "failed" }] }), NOW), "stale"));
test("freshness ok when injuries unavailable", () =>
  assert.equal(
    freshness(latest({ sources: [{ id: "nhl", status: "ok" }, { id: "injuries", status: "unavailable" }] }), NOW),
    "ok"));
test("freshness warn when a non-core source failed or is stale", () => {
  for (const status of ["failed", "stale"]) {
    assert.equal(
      freshness(latest({ sources: [{ id: "nhl", status: "ok" }, { id: "injuries", status }] }), NOW),
      "warn");
  }
});
test("freshness warn when review worst is error", () =>
  assert.equal(freshness(latest({ review: { problems: 2, worst: "error" } }), NOW), "warn"));
test("groupByDate sorts and groups", () => {
  const g = groupByDate([
    { id: 1, nhl_date: "2026-11-16" },
    { id: 2, nhl_date: "2026-11-15" },
    { id: 3, nhl_date: "2026-11-16" },
  ]);
  assert.deepEqual(g.map((x) => x.nhl_date), ["2026-11-15", "2026-11-16"]);
  assert.deepEqual(g[1].games.map((x) => x.id), [1, 3]);
});
test("fmtDateTime", () => assert.equal(fmtDateTime("2026-11-16T00:00:00Z"), "16.11. 02.00"));
test("freshness stale when generated_at is unparseable", () =>
  assert.equal(freshness(latest({ generated_at: "not a date" }), NOW), "stale"));

test("fmtOdds", () => assert.equal(fmtOdds(1.66), "1,66"));
test("fmtPct1", () => assert.equal(fmtPct1(0.642), "64,2 %"));
test("fmtPp", () => { assert.equal(fmtPp(0.027), "+2,7"); assert.equal(fmtPp(-0.013), "−1,3"); });
test("fmtEv", () => { assert.equal(fmtEv(0.066), "+6,6 %"); assert.equal(fmtEv(-0.0049), "−0,5 %"); });
test("bookName", () => { assert.equal(bookName("veikkaus_fi"), "Veikkaus"); assert.equal(bookName("x"), "x"); });
test("fair and min value price", () => { assert.equal(fairOdds(0.25), 4); assert.equal(minValuePrice(0.25), 4.12); });

const G = { id: 1, state: "upcoming", home: { abbr: "VGK" }, away: { abbr: "LAK" } };
test("pickLabel all markets", () => {
  assert.equal(pickLabel({ market: "h2h", outcome: "home" }, G), "VGK voittaa");
  assert.equal(pickLabel({ market: "1x2", outcome: "draw" }, G), "Tasapeli varsinaisella peliajalla");
  assert.equal(pickLabel({ market: "1x2", outcome: "away" }, G), "LAK voittaa varsinaisella peliajalla");
  assert.equal(pickLabel({ market: "totals", outcome: "over", line: 6.5 }, G), "Yli 6,5 maalia");
  assert.equal(pickLabel({ market: "totals", outcome: "under", line: 5.5 }, G), "Alle 5,5 maalia");
  assert.equal(pickLabel({ market: "spreads", outcome: "home", line: -1.5 }, G), "VGK −1,5");
  assert.equal(pickLabel({ market: "spreads", outcome: "away", line: -1.5 }, G), "LAK +1,5");
});
test("marketTitle", () => {
  assert.equal(marketTitle({ key: "h2h" }, G), "Voittaja (sis. jatkoaika)");
  assert.equal(marketTitle({ key: "1x2" }, G), "1X2 (varsinainen peliaika)");
  assert.equal(marketTitle({ key: "totals", line: 6.5 }, G), "Yli/alle 6,5");
  assert.equal(marketTitle({ key: "spreads", line: -1.5 }, G), "Tasoitus VGK −1,5");
});

const pk = (market, ev, extra = {}) => ({
  market, outcome: "home", line: null, ev, flag: null, veikkaus: null, ...extra,
});
const picksLatest = () => ({
  games: [
    { ...G, id: 1, odds: { picks: [pk("h2h", 0.05), pk("totals", 0.09, { flag: "suspicious" }),
      pk("spreads", 0.07, { veikkaus: { price: 2, ev: 0.04, value: true } })] } },
    { ...G, id: 2, odds: { picks: [pk("h2h", 0.08, { veikkaus: { price: 2, ev: 0.06, value: true } })] } },
    { ...G, id: 3, state: "started", odds: { picks: [pk("h2h", 0.5)] } },
    { ...G, id: 4, odds: null },
  ],
});
test("collectPicks sorts and hides flagged by default", () => {
  const evs = collectPicks(picksLatest()).map((x) => x.pick.ev);
  assert.deepEqual(evs, [0.08, 0.07, 0.05]);
  const all = collectPicks(picksLatest(), { showFlagged: true }).map((x) => x.pick.ev);
  assert.deepEqual(all, [0.08, 0.07, 0.05, 0.09]);
});
test("collectPicks market filter", () =>
  assert.deepEqual(collectPicks(picksLatest(), { market: "spreads" }).map((x) => x.pick.ev), [0.07]));
test("collectPicks veikkausOnly", () => {
  const r = collectPicks(picksLatest(), { veikkausOnly: true });
  assert.deepEqual(r.map((x) => x.pick.veikkaus.ev), [0.06, 0.04]);
});
test("collectPicks skips started games", () =>
  assert.ok(!collectPicks(picksLatest(), { showFlagged: true }).some((x) => x.game.id === 3)));
