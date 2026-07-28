import test from "node:test";
import assert from "node:assert/strict";
import { isTradeScopedError } from "../renderer/src/trading/app-event-ownership.mjs";
import { TradeOperationController } from "../renderer/src/trading/trade-operation-controller.mjs";

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
  const envelope = tradeOperationFailedEvent("op-1");
  assert.equal(isTradeScopedError(envelope), true);
});

test("isTradeScopedError is false for null/non-object input", () => {
  assert.equal(isTradeScopedError(null), false);
  assert.equal(isTradeScopedError(undefined), false);
  assert.equal(isTradeScopedError("trades"), false);
});

test("a trade operation_failed is owned by the App-level controller, not the generic App path", () => {
  // Integration contract: one trade async failure must produce exactly one
  // error entry (the App-level persistent banner via the controller), with a
  // retry that re-runs the original create/update/delete. The App must NOT
  // route it through the generic backgroundError path (whose retry would call
  // retryLive/retryService). The controller is always mounted, so the failure
  // is surfaced even if the TradeDrawer unmounted (e.g. in Replay).
  const event = tradeOperationFailedEvent("op-create-1");
  const retryCalls = [];
  const controller = new TradeOperationController();
  controller.track("op-create-1", {
    command: "create",
    retry: () => {
      retryCalls.push("create");
      return Promise.resolve();
    },
  });

  // (1) The App generic path skips trade-scoped failures.
  assert.equal(isTradeScopedError(event), true);

  // (2) The App routes the failure to the controller, which claims it.
  const failureId = controller.fail(
    "op-create-1",
    event.payload.message,
    event.payload,
  );
  assert.equal(typeof failureId, "string");
  assert.equal(controller.failure !== null, true);
  assert.equal(controller.failure.command, "create");

  // (3) The retry re-runs the original create (not retryLive/retryService).
  controller.failure.retry();
  assert.deepEqual(retryCalls, ["create"]);
});

test("a non-trade operation_failed is NOT claimed by the trade ownership path", () => {
  // A live operation_failed must still flow through the App's generic path;
  // isTradeScopedError must be false so the App handles it, and the trade
  // controller does not claim an untracked op.
  const liveEvent = {
    ...tradeOperationFailedEvent("op-live-1"),
    payload: {
      ...tradeOperationFailedEvent("op-live-1").payload,
      affected_capability: "live",
    },
  };
  assert.equal(isTradeScopedError(liveEvent), false);
  const controller = new TradeOperationController();
  controller.track("op-create-1", {
    command: "create",
    retry: () => Promise.resolve(),
  });
  assert.equal(
    controller.fail("op-live-1", "x", liveEvent.payload),
    null,
  );
  assert.equal(controller.failure, null);
});
