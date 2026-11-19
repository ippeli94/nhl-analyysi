import { test } from "node:test";
import assert from "node:assert/strict";
import {
  loadBets, saveBets, addBet, removeBet, settle, evaluate, exportJson, importJson,
} from "../bets.js";

const mem = (init) => {
  const m = new Map(init ? Object.entries(init) : []);
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), m };
};
const bet = (o = {}) => ({
  id: "b1", placed_at: "2026-11-19T10:00:00Z", game_id: 1, start_utc: "2026-11-20T00:00:00Z",
  home: "TOR", away: "BOS", market: "h2h", line: null, outcome: "home", book: "pinnacle",
  price: 2.0, stake: 10, p_model: 0.55, label: "TOR voittaa", ...o,
});
const res = (o = {}) => ({
  id: 1, date: "2026-11-20", home: "TOR", away: "BOS", home_goals: 4, away_goals: 3,
  reg_home_goals: 3, reg_away_goals: 3, decided: "OT", ...o,
});
const NOW = new Date("2026-11-21T12:00:00Z");

test("h2h settles by final winner (OT/SO winner counts)", () => {
  assert.deepEqual(settle(bet(), res()), { status: "won", profit: 10 });
  assert.deepEqual(settle(bet({ outcome: "away" }), res()), { status: "lost", profit: -10 });
  const so = res({ home_goals: 2, away_goals: 3, reg_home_goals: 2, reg_away_goals: 2, decided: "SO" });
  assert.equal(settle(bet({ outcome: "away" }), so).status, "won");
});

test("1x2 uses regulation goals; draw wins on OT game", () => {
  const b = bet({ market: "1x2", outcome: "draw", price: 4 });
  assert.deepEqual(settle(b, res()), { status: "won", profit: 30 });
  assert.equal(settle({ ...b, outcome: "home" }, res()).status, "lost");
  const reg = res({ reg_home_goals: 4, reg_away_goals: 1, decided: "REG" });
  assert.equal(settle({ ...b, outcome: "home" }, reg).status, "won");
  assert.equal(settle({ ...b, outcome: "away" }, reg).status, "lost");
});

test("totals vs final goals incl. SO winner goal; exact line pushes", () => {
  const o = bet({ market: "totals", line: 6.5, outcome: "over" });
  assert.equal(settle(o, res()).status, "won"); // 7 goals
  assert.equal(settle({ ...o, outcome: "under" }, res()).status, "lost");
  // SO game 2-3 final = 5 goals (reg 2-2 = 4)
  const so = res({ home_goals: 2, away_goals: 3, reg_home_goals: 2, reg_away_goals: 2, decided: "SO" });
  assert.equal(settle({ ...o, line: 4.5 }, so).status, "won");
  assert.equal(settle({ ...o, line: 4.5, outcome: "under" }, so).status, "lost");
  assert.deepEqual(settle({ ...o, line: 7 }, res()), { status: "push", profit: 0 });
  assert.deepEqual(settle({ ...o, line: 7, outcome: "under" }, res()), { status: "push", profit: 0 });
});

test("spreads: line is HOME handicap, both signs, push", () => {
  const s = (line, outcome, r = res()) => settle(bet({ market: "spreads", line, outcome }), r).status;
  // margin +1 (4-3)
  assert.equal(s(-1.5, "home"), "lost");
  assert.equal(s(-1.5, "away"), "won");
  assert.equal(s(1.5, "home"), "won");
  assert.equal(s(1.5, "away"), "lost");
  assert.equal(s(-1, "home"), "push");
  assert.equal(s(-1, "away"), "push");
  assert.equal(s(-2.5, "home", res({ home_goals: 5, away_goals: 1 })), "won");
});

test("Betfair 5 % commission on winnings only", () => {
  const w = settle(bet({ book: "betfair_ex_eu", price: 3, stake: 10 }), res());
  assert.ok(Math.abs(w.profit - 19) < 1e-9);
  assert.equal(settle(bet({ book: "betfair_ex_eu", outcome: "away" }), res()).profit, -10);
  assert.equal(settle(bet({ book: "betfair_ex_eu", market: "totals", line: 7, outcome: "over" }), res()).profit, 0);
});

test("unsettleable combinations are void with stake returned", () => {
  for (const b of [
    bet({ market: "spreads", line: null, outcome: "home" }),
    bet({ market: "totals", line: null, outcome: "over" }),
    bet({ market: "h2h", outcome: "draw" }),
    bet({ market: "1x2", outcome: "over" }),
    bet({ market: "spreads", line: 1.5, outcome: "draw" }),
    bet({ market: "xyz" }),
  ]) assert.deepEqual(settle(b, res()), { status: "void", profit: 0 }, JSON.stringify(b));
  assert.equal(settle(bet({ market: "1x2" }), res({ reg_home_goals: null })).status, "void");
});

test("evaluate: open / waiting / settled", () => {
  const bets = [
    bet({ id: "a", game_id: 1 }),
    bet({ id: "b", game_id: 2, start_utc: "2026-11-21T00:00:00Z" }), // started, no result
    bet({ id: "c", game_id: 3, start_utc: "2026-11-22T00:00:00Z" }), // future
  ];
  const e = evaluate(bets, { games: [res()] }, NOW);
  assert.deepEqual(e.rows.map((r) => r.status), ["won", "waiting", "open"]);
});

test("evaluate: results null or postponed game stays waiting, never settled wrongly", () => {
  const e = evaluate([bet()], null, NOW);
  assert.equal(e.rows[0].status, "waiting");
  assert.equal(e.rows[0].profit, 0);
  const e2 = evaluate([bet({ game_id: 99 })], { games: [res()] }, NOW);
  assert.equal(e2.rows[0].status, "waiting");
  const e3 = evaluate([bet()], { games: "bad" }, NOW);
  assert.equal(e3.rows[0].status, "waiting");
  assert.equal(e3.totals.n_settled, 0);
});

test("evaluate: unsettleable bet with result present is void", () => {
  const e = evaluate([bet({ market: "spreads", line: null })], { games: [res()] }, NOW);
  assert.equal(e.rows[0].status, "void");
  assert.equal(e.totals.n_settled, 0);
  assert.equal(e.totals.staked, 0);
});

test("evaluate: ROI math, push in staked, void excluded, byMarket", () => {
  const bets = [
    bet({ id: "1", stake: 10, price: 2 }),                                   // won +10
    bet({ id: "2", stake: 20, outcome: "away" }),                            // lost -20
    bet({ id: "3", stake: 10, market: "totals", line: 7, outcome: "over" }), // push 0
    bet({ id: "4", stake: 50, market: "spreads", line: null }),              // void
    bet({ id: "5", stake: 5, game_id: 9 }),                                  // waiting
  ];
  const { totals: t, byMarket } = evaluate(bets, { games: [res()] }, NOW);
  assert.equal(t.n, 5);
  assert.equal(t.n_settled, 3);
  assert.equal(t.staked, 40);
  assert.equal(t.profit, -10);
  assert.equal(t.roi, -10 / 40);
  assert.equal(t.hits, 1);
  assert.equal(t.hit_rate, 0.5);
  assert.equal(byMarket.h2h.n_settled, 2);
  assert.equal(byMarket.h2h.profit, -10);
  assert.equal(byMarket.totals.profit, 0);
  assert.equal(byMarket.totals.hit_rate, null);
  assert.equal(evaluate([], null, NOW).totals.roi, null);
});

test("addBet validates and assigns new id", () => {
  const out = addBet([], bet({ id: undefined }));
  assert.equal(out.length, 1);
  assert.ok(out[0].id);
  assert.throws(() => addBet([], bet({ stake: 0 })));
  assert.throws(() => addBet([], bet({ stake: -1 })));
  assert.throws(() => addBet([], bet({ price: 1 })));
  assert.throws(() => addBet([], bet({ price: NaN })));
  assert.throws(() => addBet([], bet({ market: "foo" })));
  assert.throws(() => addBet([], bet({ game_id: 1.5 })));
  const two = addBet(out, bet({ id: undefined }));
  assert.notEqual(two[0].id, two[1].id);
  assert.equal(out.length, 1); // input not mutated
});

test("removeBet", () => {
  const bs = [bet({ id: "a" }), bet({ id: "b" })];
  assert.deepEqual(removeBet(bs, "a").map((b) => b.id), ["b"]);
  assert.equal(removeBet(bs, "zzz").length, 2);
});

test("storage: save/load roundtrip and key", () => {
  const st = mem();
  assert.equal(saveBets(st, [bet()]), true);
  assert.deepEqual(JSON.parse(st.m.get("nhl.bets.v1")), { v: 1, bets: [bet()] });
  assert.deepEqual(loadBets(st), [bet()]);
});

test("storage: corrupt, foreign, throwing or missing storage is safe", () => {
  assert.deepEqual(loadBets(null), []);
  assert.equal(saveBets(null, [bet()]), false);
  assert.deepEqual(loadBets(mem()), []);
  assert.deepEqual(loadBets(mem({ "nhl.bets.v1": "{not json" })), []);
  assert.deepEqual(loadBets(mem({ "nhl.bets.v1": '{"v":2,"bets":[]}' })), []);
  assert.deepEqual(loadBets(mem({ "nhl.bets.v1": "[1,2]" })), []);
  const boom = { getItem() { throw new Error("x"); }, setItem() { throw new Error("x"); } };
  assert.deepEqual(loadBets(boom), []);
  assert.equal(saveBets(boom, [bet()]), false);
});

test("loadBets drops invalid entries only", () => {
  const good = bet();
  const bad = [
    null, 5, { ...good, game_id: "1" }, { ...good, market: "x" }, { ...good, outcome: "x" },
    { ...good, price: 1 }, { ...good, stake: 0 }, { ...good, stake: Infinity },
    { ...good, line: "1.5" }, { ...good, id: "" },
  ];
  const st = mem({ "nhl.bets.v1": JSON.stringify({ v: 1, bets: [good, ...bad] }) });
  assert.deepEqual(loadBets(st), [good]);
});

test("export/import roundtrip merges by id", () => {
  const a = bet({ id: "a" }), b = bet({ id: "b" });
  const text = exportJson([a, b]);
  const r = importJson(text, [a]);
  assert.equal(r.error, undefined);
  assert.equal(r.added, 1);
  assert.equal(r.skipped, 1);
  assert.deepEqual(r.bets.map((x) => x.id), ["a", "b"]);
});

test("import rejects garbage and leaves bets unchanged", () => {
  const cur = [bet()];
  for (const t of ["not json", "[]", "{}", '{"v":1}', '{"v":1,"bets":"x"}', '{"v":9,"bets":[]}', "null", ""]) {
    const r = importJson(t, cur);
    assert.ok(typeof r.error === "string" && r.error.length > 0, t);
    assert.equal(r.bets, cur);
    assert.equal(r.added, 0);
  }
});

test("import skips invalid entries inside a valid file", () => {
  const text = JSON.stringify({ v: 1, bets: [bet({ id: "n" }), { junk: 1 }, bet({ id: "m", stake: -3 })] });
  const r = importJson(text, []);
  assert.equal(r.added, 1);
  assert.equal(r.skipped, 2);
  assert.equal(r.bets[0].id, "n");
});

test("prototype-named book gets no commission and no NaN", () => {
  for (const book of ["constructor", "__proto__", "toString"]) {
    const r = settle(bet({ book }), res());
    assert.deepEqual(r, { status: "won", profit: 10 }, book);
  }
  const e = evaluate([bet({ book: "constructor" })], { games: [res()] }, NOW);
  assert.equal(e.totals.profit, 10);
});

test("validation: book must be string, caps on stake and price", () => {
  assert.throws(() => addBet([], bet({ book: 5 })));
  assert.throws(() => addBet([], bet({ stake: 1e7 + 1 })));
  assert.throws(() => addBet([], bet({ price: 1001 })));
  assert.equal(addBet([], bet({ stake: 1e7, price: 1000 })).length, 1);
  assert.throws(() => addBet([], bet({ p_model: "x" })));
  assert.throws(() => addBet([], bet({ label: 3 })));
});

test("load/import keep whitelisted fields only; loadBets dedupes ids", () => {
  const dirty = { ...bet(), evil: "<script>", __x: 1 };
  const st = mem({ "nhl.bets.v1": JSON.stringify({ v: 1, bets: [dirty, bet({ label: "dup" })] }) });
  assert.deepEqual(loadBets(st), [bet()]);
  const r = importJson(JSON.stringify({ v: 1, bets: [dirty] }), []);
  assert.deepEqual(r.bets, [bet()]);
});
