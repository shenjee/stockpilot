import test from "node:test";
import assert from "node:assert/strict";
import {
  applyTradesChanged,
  filterTradesByReplayCursor,
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
    payload: {
      symbol: scope.symbol,
      trade_date: scope.tradeDate,
      trade_revision: revision,
      trades,
    },
    ...overrides,
  };
}

function state(revision, trades = [], generation = 1, loadedScope) {
  return {
    trades,
    tradeRevision: revision,
    serviceGeneration: generation,
    loadedScope:
      loadedScope !== undefined
        ? loadedScope
        : trades.length > 0
          ? { ...scope }
          : null,
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

test("applyTradesChanged uses authoritative scoped payload trades without filtering", () => {
  // Payload is already scoped; even a mismatched record in trades is kept.
  const current = state(0);
  const event = tradesEvent(1, [
    trade("same"),
    trade("other-symbol", { symbol: "sz.000001" }),
  ]);
  const next = applyTradesChanged(current, event, scope);
  assert.equal(next.trades.length, 2);
  assert.deepEqual(
    next.trades.map((t) => t.trade_id),
    ["same", "other-symbol"],
  );
});

test("applyTradesChanged keeps existing trades when scope does not match", () => {
  const current = state(1, [trade("keep-me")]);
  const otherScope = tradesEvent(2, [trade("other-day")], {
    payload: {
      symbol: "sh.600584",
      trade_date: "2026-07-25",
      trade_revision: 2,
      trades: [trade("other-day", { executed_at: "2026-07-25 10:00:00" })],
    },
  });
  const next = applyTradesChanged(current, otherScope, scope);
  assert.equal(next.tradeRevision, 2);
  assert.equal(next.trades.length, 1);
  assert.equal(next.trades[0].trade_id, "keep-me");
});

test("applyTradesChanged revision jump alone must not clear trades", () => {
  const current = state(1, [trade("day-trade")]);
  const otherSymbol = tradesEvent(9, [], {
    payload: {
      symbol: "sz.000001",
      trade_date: scope.tradeDate,
      trade_revision: 9,
      trades: [],
    },
  });
  const next = applyTradesChanged(current, otherSymbol, scope);
  assert.equal(next.tradeRevision, 9);
  assert.deepEqual(
    next.trades.map((t) => t.trade_id),
    ["day-trade"],
  );
});

test("applyTradesChanged accepts same-revision fact when loading a new scope", () => {
  // list_trades does not bump trade_revision. After a scope switch, a
  // non-current-scope event may advance the gate to N; the current-scope
  // list_trades result can also carry N and must not be discarded.
  const afterSwitch = state(-1, [], 1, null);
  const otherScope = tradesEvent(7, [], {
    payload: {
      symbol: "sz.000001",
      trade_date: scope.tradeDate,
      trade_revision: 7,
      trades: [],
    },
  });
  const gated = applyTradesChanged(afterSwitch, otherScope, scope);
  assert.equal(gated.tradeRevision, 7);
  assert.equal(gated.loadedScope, null);
  assert.deepEqual(gated.trades, []);

  const currentScope = tradesEvent(7, [trade("t1")]);
  const next = applyTradesChanged(gated, currentScope, scope);
  assert.equal(next.tradeRevision, 7);
  assert.deepEqual(next.loadedScope, scope);
  assert.equal(next.trades.length, 1);
  assert.equal(next.trades[0].trade_id, "t1");
});

test("applyTradesChanged still rejects duplicate same-revision for an already-loaded scope", () => {
  const current = state(7, [trade("t1")]);
  const next = applyTradesChanged(current, tradesEvent(7, [trade("t2")]), scope);
  assert.equal(next, current);
  assert.equal(next.trades[0].trade_id, "t1");
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

test("applyTradesChanged accepts a newer generation's low revision over an old high revision", () => {
  const current = state(20, [trade("old")], 1);
  const event = tradesEvent(1, [trade("new")], { service_generation: 2 });
  const next = applyTradesChanged(current, event, scope);
  assert.notEqual(next, current);
  assert.equal(next.serviceGeneration, 2);
  assert.equal(next.tradeRevision, 1);
  assert.equal(next.trades[0].trade_id, "new");
});

test("applyTradesChanged ignores an older generation's high revision", () => {
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

test("filterTradesByReplayCursor keeps trades at or before current_time", () => {
  const trades = [
    trade("a", { executed_at: "2026-07-24 10:00:00" }),
    trade("b", { executed_at: "2026-07-24 10:05:00" }),
    trade("c", { executed_at: "2026-07-24 10:10:00" }),
  ];
  assert.deepEqual(
    filterTradesByReplayCursor(trades, "2026-07-24 10:05:00").map((t) => t.trade_id),
    ["a", "b"],
  );
  assert.deepEqual(
    filterTradesByReplayCursor(trades, "2026-07-24 09:59:59").map((t) => t.trade_id),
    [],
  );
  assert.equal(filterTradesByReplayCursor(trades, null).length, 3);
});

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
