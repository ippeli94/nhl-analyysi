import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fmtTime, fmtDate, fmtPct, fmtNum, fmtScore, fmtSignedPct, ageText,
  freshness, groupByDate, fmtDateTime, SUPPORTED_SCHEMA,
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
test("fmtScore", () => assert.equal(fmtScore({ home: 3, away: 2 }), "3–2"));
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
test("freshness warn when injuries unavailable", () =>
  assert.equal(
    freshness(latest({ sources: [{ id: "nhl", status: "ok" }, { id: "injuries", status: "unavailable" }] }), NOW),
    "warn"));
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
