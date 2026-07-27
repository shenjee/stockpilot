import test from "node:test";
import assert from "node:assert/strict";
import {
  applyTradesChanged,
  isRealTradesChangedEvent,
  matchTradeOperationFailed,
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

function tradesEvent(revision, trades) {
  return {
    event_type: "trades_changed",
    session_id: null,
    service_generation: 1,
    revision,
    payload: { trade_revision: revision, trades },
  };
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

test("applyTradesChanged replaces the list with a newer revision", () => {
  const current = { trades: [], tradeRevision: 0 };
  const next = applyTradesChanged(current, tradesEvent(1, [trade("t1")]), scope);
  assert.equal(next.tradeRevision, 1);
  assert.equal(next.trades.length, 1);
  assert.equal(next.trades[0].trade_id, "t1");
});

test("applyTradesChanged ignores a stale (lower-or-equal) revision", () => {
  const current = { trades: [trade("t1")], tradeRevision: 2 };
  const next = applyTradesChanged(current, tradesEvent(2, [trade("t2")]), scope);
  assert.equal(next, current); // unchanged, same reference
  assert.equal(next.trades[0].trade_id, "t1");
});

test("applyTradesChanged filters the event trades to the current symbol and date", () => {
  const current = { trades: [], tradeRevision: 0 };
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
  const current = { trades: [trade("t1")], tradeRevision: 1 };
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

test("matchTradeOperationFailed matches a tracked operation id", () => {
  const pending = new Set(["op-1"]);
  const event = {
    event_type: "operation_failed",
    operation_id: "op-1",
    payload: { error_code: "trade_failed", message: "boom", retryable: true },
  };
  const match = matchTradeOperationFailed(event, pending);
  assert.deepEqual(match, {
    operationId: "op-1",
    error: event.payload,
  });
});

test("matchTradeOperationFailed ignores untracked operation ids", () => {
  const pending = new Set(["op-1"]);
  const event = {
    event_type: "operation_failed",
    operation_id: "op-other",
    payload: { message: "boom" },
  };
  assert.equal(matchTradeOperationFailed(event, pending), null);
});

test("matchTradeOperationFailed ignores non-operation_failed events", () => {
  const pending = new Set(["op-1"]);
  assert.equal(
    matchTradeOperationFailed(tradesEvent(1, []), pending),
    null,
  );
});
