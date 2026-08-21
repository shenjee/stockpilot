import test from "node:test";
import assert from "node:assert/strict";
import {
  applyHistoryListResponse,
  applyHistoryTradesChanged,
  historyInvalidatedByTradesChanged,
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
    schema_version: "t0_app_v2",
    service_generation: serviceGeneration,
    session_id: null,
    revision: tradeRevision,
    event_type: "trades_changed",
    payload: {
      symbol: "sh.600584",
      trade_date: "2026-07-24",
      trade_revision: tradeRevision,
      trades,
    },
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

test("historyInvalidatedByTradesChanged is true for real trades_changed", () => {
  assert.equal(historyInvalidatedByTradesChanged(tradesChangedEvent([], 1)), true);
});

test("historyInvalidatedByTradesChanged ignores simulated session-scoped events", () => {
  const simulated = {
    ...tradesChangedEvent([trade({ trade_scope: "simulated" })], 1),
    session_id: "replay-1",
  };
  assert.equal(historyInvalidatedByTradesChanged(simulated), false);
});

test("applyHistoryTradesChanged does not merge scoped payloads into history", () => {
  const existing = {
    trades: [trade({ trade_id: "history-kept" })],
    tradeRevision: 4,
    serviceGeneration: 3,
  };
  const next = applyHistoryTradesChanged(
    existing,
    tradesChangedEvent([trade({ trade_id: "scoped-only" })], 5),
  );
  assert.equal(next, existing);
  assert.deepEqual(
    next.trades.map((t) => t.trade_id),
    ["history-kept"],
  );
});

test("applyHistoryListResponse accepts a newer revision and sorts trades", () => {
  const state = applyHistoryListResponse(
    null,
    {
      trade_revision: 5,
      trades: [
        trade({ trade_id: "t1", executed_at: "2026-07-24 10:03:00" }),
        trade({ trade_id: "t2", symbol: "sz.000001", executed_at: "2026-07-25 14:10:00" }),
        trade({ trade_id: "t3", executed_at: "2026-07-24 09:35:00" }),
      ],
    },
    3,
  );
  assert.equal(state.tradeRevision, 5);
  assert.equal(state.serviceGeneration, 3);
  assert.deepEqual(
    state.trades.map((t) => t.trade_id),
    ["t2", "t1", "t3"],
  );
});

test("applyHistoryListResponse accepts an empty authoritative snapshot", () => {
  const state = applyHistoryListResponse(null, { trade_revision: 0, trades: [] }, 3);
  assert.deepEqual(state.trades, []);
  assert.equal(state.tradeRevision, 0);
});

test("applyHistoryListResponse discards older revisions within the same generation", () => {
  const first = applyHistoryListResponse(
    null,
    { trade_revision: 5, trades: [trade()] },
    3,
  );
  const stale = applyHistoryListResponse(
    first,
    { trade_revision: 5, trades: [trade({ trade_id: "stale" })] },
    3,
  );
  assert.equal(stale, first);
  assert.deepEqual(
    stale.trades.map((t) => t.trade_id),
    ["t-1"],
  );
});

test("applyHistoryListResponse resets the gate on a newer service generation", () => {
  const first = applyHistoryListResponse(
    null,
    { trade_revision: 9, trades: [trade()] },
    3,
  );
  const restarted = applyHistoryListResponse(
    first,
    { trade_revision: 1, trades: [trade({ trade_id: "new" })] },
    4,
  );
  assert.equal(restarted.serviceGeneration, 4);
  assert.equal(restarted.tradeRevision, 1);
  assert.deepEqual(
    restarted.trades.map((t) => t.trade_id),
    ["new"],
  );
});

test("applyHistoryListResponse ignores malformed data (no integer trade_revision)", () => {
  assert.equal(
    applyHistoryListResponse(null, { trade_revision: "oops", trades: [] }, 3),
    null,
  );
});
