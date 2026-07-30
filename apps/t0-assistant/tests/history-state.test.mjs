import test from "node:test";
import assert from "node:assert/strict";
import {
  applyHistoryTradesChanged,
  sortHistoryTrades,
} from "../renderer/src/trading/history-state.mjs";

function trade(overrides = {}) {
  return {
    trade_id: "t-1",
    bucket_start: "2026-07-24 10:00:00",
    trade_scope: "real",
    symbol: "sh.600584",
    side: "buy",
    executed_at: "2026-07-24 10:03:00",
    price: 38.25,
    quantity: 200,
    fee: 5.01,
    note: "",
    fee_plan_id: "shenwan-hongyuan",
    ...overrides,
  };
}

function tradesChangedEvent(trades, tradeRevision, serviceGeneration = 3) {
  return {
    schema_version: "t0_app_v1",
    service_generation: serviceGeneration,
    session_id: null,
    revision: tradeRevision,
    event_type: "trades_changed",
    payload: { trade_revision: tradeRevision, trades },
  };
}

test("sortHistoryTrades orders by executed_at descending then trade_id ascending", () => {
  const sorted = sortHistoryTrades([
    trade({ trade_id: "a", executed_at: "2026-07-24 10:00:00" }),
    trade({ trade_id: "c", executed_at: "2026-07-25 14:10:00" }),
    trade({ trade_id: "b", executed_at: "2026-07-24 10:00:00" }),
  ]);
  assert.deepEqual(
    sorted.map((t) => t.trade_id),
    ["c", "a", "b"],
    "most recent first; same time -> trade_id ascending",
  );
});

test("applyHistoryTradesChanged keeps the full snapshot across symbols and dates", () => {
  const event = tradesChangedEvent(
    [
      trade({ trade_id: "t1", symbol: "sh.600584", executed_at: "2026-07-24 10:03:00" }),
      trade({ trade_id: "t2", symbol: "sz.000001", executed_at: "2026-07-25 14:10:00" }),
      trade({ trade_id: "t3", symbol: "sh.600584", executed_at: "2026-07-24 09:35:00" }),
    ],
    5,
  );
  const state = applyHistoryTradesChanged(null, event);
  assert.equal(state.tradeRevision, 5);
  assert.equal(state.serviceGeneration, 3);
  // No scope filter: every trade survives, sorted most-recent-first.
  assert.deepEqual(
    state.trades.map((t) => t.trade_id),
    ["t2", "t1", "t3"],
  );
});

test("applyHistoryTradesChanged accepts an empty authoritative snapshot", () => {
  const state = applyHistoryTradesChanged(null, tradesChangedEvent([], 0));
  assert.deepEqual(state.trades, []);
  assert.equal(state.tradeRevision, 0);
});

test("applyHistoryTradesChanged ignores stale revisions within the same generation", () => {
  const first = applyHistoryTradesChanged(null, tradesChangedEvent([trade()], 5));
  const stale = applyHistoryTradesChanged(first, tradesChangedEvent([trade({ trade_id: "stale" })], 5));
  assert.equal(stale, first);
  assert.deepEqual(stale.trades.map((t) => t.trade_id), ["t-1"]);
});

test("applyHistoryTradesChanged resets the gate on a newer service generation", () => {
  const first = applyHistoryTradesChanged(null, tradesChangedEvent([trade()], 9, 3));
  // A fresh generation's low revision is accepted (not rejected by the stale high revision).
  const restarted = applyHistoryTradesChanged(
    first,
    tradesChangedEvent([trade({ trade_id: "new" })], 1, 4),
  );
  assert.equal(restarted.serviceGeneration, 4);
  assert.equal(restarted.tradeRevision, 1);
  assert.deepEqual(restarted.trades.map((t) => t.trade_id), ["new"]);
});

test("applyHistoryTradesChanged ignores simulated (session-scoped) trades_changed", () => {
  const simulated = {
    ...tradesChangedEvent([trade({ trade_scope: "simulated" })], 1),
    session_id: "replay-1",
  };
  const state = applyHistoryTradesChanged(null, simulated);
  assert.equal(state, null);
});

test("applyHistoryTradesChanged ignores malformed events (no integer trade_revision)", () => {
  const malformed = {
    ...tradesChangedEvent([], 0),
    payload: { trade_revision: "oops", trades: [] },
  };
  assert.equal(applyHistoryTradesChanged(null, malformed), null);
});
