import test from "node:test";
import assert from "node:assert/strict";
import {
  applyTradesChanged,
  isRealTradesChangedEvent,
  matchTradeOperationFailed,
  pendingOpResolvedByTradesChanged,
} from "../renderer/src/trading/trade-state.mjs";

const scope = { symbol: "sh.600584", tradeDate: "2026-07-24" };

function trade(id, overrides = {}) {
  return {
    trade_id: id,
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

function tradesEvent(revision, trades, overrides = {}) {
  return {
    event_type: "trades_changed",
    session_id: null,
    service_generation: 1,
    revision,
    payload: { trade_revision: revision, trades },
    ...overrides,
  };
}

function state(revision, trades = [], generation = 1) {
  return { trades, tradeRevision: revision, serviceGeneration: generation };
}

test("isRealTradesChangedEvent accepts real (session_id null) events", () => {
  assert.equal(isRealTradesChangedEvent(tradesEvent(1, [])), true);
});

test("isRealTradesChangedEvent rejects simulated (session_id non-null) events", () => {
  const simulated = {
    event_type: "trades_changed",
    session_id: "replay-1",
    payload: { trade_revision: 1, trades: [] },
  };
  assert.equal(isRealTradesChangedEvent(simulated), false);
});

test("isRealTradesChangedEvent rejects non-trades events", () => {
  assert.equal(
    isRealTradesChangedEvent({ event_type: "preferences_changed", session_id: null }),
    false,
  );
  assert.equal(isRealTradesChangedEvent(null), false);
});

test("applyTradesChanged replaces the list with a newer same-generation revision", () => {
  const current = state(0);
  const next = applyTradesChanged(current, tradesEvent(1, [trade("t1")]), scope);
  assert.equal(next.tradeRevision, 1);
  assert.equal(next.serviceGeneration, 1);
  assert.equal(next.trades.length, 1);
  assert.equal(next.trades[0].trade_id, "t1");
});

test("applyTradesChanged ignores a stale (lower-or-equal) same-generation revision", () => {
  const current = state(2, [trade("t1")]);
  const next = applyTradesChanged(current, tradesEvent(2, [trade("t2")]), scope);
  assert.equal(next, current); // unchanged, same reference
  assert.equal(next.trades[0].trade_id, "t1");
});

test("applyTradesChanged filters the event trades to the current symbol and date", () => {
  const current = state(0);
  const event = tradesEvent(1, [
    trade("same"),
    trade("other-symbol", { symbol: "sz.000001" }),
    trade("other-date", { executed_at: "2026-07-25 10:03:00" }),
  ]);
  const next = applyTradesChanged(current, event, scope);
  assert.equal(next.trades.length, 1);
  assert.equal(next.trades[0].trade_id, "same");
});

test("applyTradesChanged ignores a malformed event (no revision)", () => {
  const current = state(1, [trade("t1")]);
  const malformed = {
    event_type: "trades_changed",
    session_id: null,
    payload: { trades: [trade("t2")] },
  };
  const next = applyTradesChanged(current, malformed, scope);
  assert.equal(next, current);
});

test("applyTradesChanged on a null current state initializes from the event", () => {
  const next = applyTradesChanged(null, tradesEvent(1, [trade("t1")]), scope);
  assert.equal(next.tradeRevision, 1);
  assert.equal(next.trades.length, 1);
});

// --- service_generation regression (review P1#3) ---

test("applyTradesChanged accepts a newer generation's low revision over an old high revision", () => {
  // Old generation climbed to revision 20; Python restarts to generation 2.
  const current = state(20, [trade("old")], 1);
  const event = tradesEvent(1, [trade("new")], { service_generation: 2 });
  const next = applyTradesChanged(current, event, scope);
  assert.notEqual(next, current);
  assert.equal(next.serviceGeneration, 2);
  assert.equal(next.tradeRevision, 1);
  assert.equal(next.trades[0].trade_id, "new");
});

test("applyTradesChanged ignores an older generation's high revision", () => {
  // Now tracking generation 2; a late generation-1 event (revision 21) is stale.
  const current = state(1, [trade("new")], 2);
  const event = tradesEvent(21, [trade("stale")], { service_generation: 1 });
  const next = applyTradesChanged(current, event, scope);
  assert.equal(next, current);
  assert.equal(next.trades[0].trade_id, "new");
});

test("applyTradesChanged accepts the same generation with a higher revision", () => {
  const current = state(5, [trade("t1")], 2);
  const next = applyTradesChanged(
    current,
    tradesEvent(6, [trade("t2")], { service_generation: 2 }),
    scope,
  );
  assert.notEqual(next, current);
  assert.equal(next.tradeRevision, 6);
  assert.equal(next.trades[0].trade_id, "t2");
});

// --- pending op resolution (review P1#2) ---

test("pendingOpResolvedByTradesChanged returns the id when the event carries a tracked operation_id", () => {
  const pending = new Map([["op-1", { command: "create" }]]);
  const event = tradesEvent(1, [], { operation_id: "op-1" });
  assert.equal(pendingOpResolvedByTradesChanged(event, pending), "op-1");
});

test("pendingOpResolvedByTradesChanged returns null when the operation_id is untracked", () => {
  const pending = new Map([["op-1", { command: "create" }]]);
  const event = tradesEvent(1, [], { operation_id: "op-other" });
  assert.equal(pendingOpResolvedByTradesChanged(event, pending), null);
});

test("pendingOpResolvedByTradesChanged returns null when the event has no operation_id", () => {
  const pending = new Map([["op-1", { command: "create" }]]);
  // No operation_id on the event -> cannot correlate -> do NOT clear anything.
  assert.equal(pendingOpResolvedByTradesChanged(tradesEvent(1, []), pending), null);
});

test("pendingOpResolvedByTradesChanged returns null for a non-trades_changed event", () => {
  const pending = new Map([["op-1", { command: "create" }]]);
  assert.equal(
    pendingOpResolvedByTradesChanged(
      { event_type: "operation_failed", operation_id: "op-1" },
      pending,
    ),
    null,
  );
});

test("matchTradeOperationFailed works with a Map of pending operations", () => {
  const pending = new Map([
    ["op-1", { command: "create", retry: () => Promise.resolve() }],
  ]);
  const event = {
    event_type: "operation_failed",
    operation_id: "op-1",
    payload: { error_code: "trade_failed", message: "boom", retryable: true },
  };
  const match = matchTradeOperationFailed(event, pending);
  assert.deepEqual(match, { operationId: "op-1", error: event.payload });
});

test("matchTradeOperationFailed ignores untracked operation ids", () => {
  const pending = new Map([["op-1", { command: "create" }]]);
  const event = {
    event_type: "operation_failed",
    operation_id: "op-other",
    payload: { message: "boom" },
  };
  assert.equal(matchTradeOperationFailed(event, pending), null);
});

test("matchTradeOperationFailed ignores non-operation_failed events", () => {
  const pending = new Map([["op-1", { command: "create" }]]);
  assert.equal(matchTradeOperationFailed(tradesEvent(1, []), pending), null);
});
