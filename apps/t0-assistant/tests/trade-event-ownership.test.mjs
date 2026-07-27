import test from "node:test";
import assert from "node:assert/strict";
import { isTradeScopedError } from "../renderer/src/trading/app-event-ownership.mjs";
import { matchTradeOperationFailed } from "../renderer/src/trading/trade-state.mjs";

// A trade operation_failed event carrying the frozen application_error shape.
function tradeOperationFailedEvent(operationId) {
  return {
    schema_version: "t0_app_v1",
    service_generation: 3,
    session_id: null,
    revision: 5,
    event_type: "operation_failed",
    operation_id: operationId,
    payload: {
      error_code: "trade_persist_failed",
      category: "persistence",
      severity: "error",
      retryable: true,
      affected_capability: "trades",
      message: "成交保存失败，请重试",
      request_id: "req-create-1",
      details: {},
    },
  };
}

test("isTradeScopedError is true for a trades-capability error", () => {
  const error = {
    error_code: "trade_persist_failed",
    message: "boom",
    retryable: true,
    affected_capability: "trades",
  };
  assert.equal(isTradeScopedError(error), true);
});

test("isTradeScopedError is false for live/preferences/service capabilities", () => {
  for (const capability of ["live", "preferences", "service", "symbol_selection"]) {
    assert.equal(
      isTradeScopedError({
        error_code: "x",
        message: "m",
        retryable: true,
        affected_capability: capability,
      }),
      false,
      `${capability} should not be trade-scoped`,
    );
  }
});

test("isTradeScopedError accepts an envelope wrapping the error in payload", () => {
  // App's handler passes applicationErrorFrom(envelope.payload), but the
  // predicate also tolerates a raw envelope (error in .payload) defensively.
  const envelope = tradeOperationFailedEvent("op-1");
  assert.equal(isTradeScopedError(envelope), true);
});

test("isTradeScopedError is false for null/non-object input", () => {
  assert.equal(isTradeScopedError(null), false);
  assert.equal(isTradeScopedError(undefined), false);
  assert.equal(isTradeScopedError("trades"), false);
});

test("a trade operation_failed is owned by the TradeDrawer, not the App generic path", () => {
  // Integration contract: one trade async failure must produce exactly one
  // error entry (the TradeDrawer's), with a retry that re-runs the original
  // create/update/delete. The App must NOT also surface it as a global
  // backgroundError (whose retry would call retryLive/retryService).
  const event = tradeOperationFailedEvent("op-create-1");
  const retryCalls = [];
  const pending = new Map([
    [
      "op-create-1",
      {
        command: "create",
        retry: () => {
          retryCalls.push("create");
          return Promise.resolve();
        },
      },
    ],
  ]);

  // (1) The App generic path skips trade-scoped failures.
  assert.equal(isTradeScopedError(event), true);

  // (2) The TradeDrawer matches the tracked operation and keeps the retry.
  const match = matchTradeOperationFailed(event, pending);
  assert.equal(match.operationId, "op-create-1");
  const op = pending.get(match.operationId);
  assert.equal(op.command, "create");

  // (3) The retry re-runs the original create (not retryLive/retryService).
  op.retry();
  assert.deepEqual(retryCalls, ["create"]);
});

test("a non-trade operation_failed is NOT claimed by the TradeDrawer ownership path", () => {
  // A live operation_failed must still flow through the App's generic path;
  // isTradeScopedError must be false so the App handles it.
  const liveEvent = {
    ...tradeOperationFailedEvent("op-live-1"),
    payload: {
      ...tradeOperationFailedEvent("op-live-1").payload,
      affected_capability: "live",
    },
  };
  assert.equal(isTradeScopedError(liveEvent), false);
  // And the TradeDrawer does not match it (untracked operation id).
  const pending = new Map([
    ["op-create-1", { command: "create", retry: () => Promise.resolve() }],
  ]);
  assert.equal(matchTradeOperationFailed(liveEvent, pending), null);
});
