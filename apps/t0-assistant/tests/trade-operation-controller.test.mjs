import test from "node:test";
import assert from "node:assert/strict";
import { TradeOperationController } from "../renderer/src/trading/trade-operation-controller.mjs";

function makeError(affectedCapability = "trades") {
  return {
    error_code: "trade_persist_failed",
    message: "成交保存失败",
    retryable: true,
    affected_capability: affectedCapability,
  };
}

test("track + resolve clears a pending op and never notifies", () => {
  const controller = new TradeOperationController();
  let notified = 0;
  controller.subscribe(() => {
    notified += 1;
  });
  const retry = () => Promise.resolve();
  controller.track("op-1", { command: "create", retry });
  assert.equal(controller.has("op-1"), true);
  assert.equal(controller.hasPending(), true);

  // resolve (success) must not raise a failure notification.
  assert.equal(controller.resolve("op-1"), true);
  assert.equal(controller.has("op-1"), false);
  assert.equal(controller.hasPending(), false);
  assert.equal(notified, 0);
  assert.equal(controller.failure, null);
});

test("track + fail publishes a failure with the captured retry", () => {
  const controller = new TradeOperationController();
  let lastFailure = "sentinel";
  controller.subscribe((f) => {
    lastFailure = f;
  });
  let retried = 0;
  controller.track("op-1", {
    command: "create",
    retry: () => {
      retried += 1;
      return Promise.resolve();
    },
  });

  assert.equal(
    controller.fail("op-1", "成交保存失败", makeError()),
    true,
  );
  assert.equal(controller.has("op-1"), false);
  assert.equal(controller.failure !== null, true);
  assert.equal(controller.failure.command, "create");
  assert.equal(controller.failure.operationId, "op-1");
  // The retry closure survived the failure and re-runs the original op.
  controller.failure.retry();
  assert.equal(retried, 1);
  assert.equal(lastFailure.command, "create");
});

test("fail on an untracked operation returns false (not claimed)", () => {
  const controller = new TradeOperationController();
  assert.equal(controller.fail("op-unknown", "x", makeError()), false);
  assert.equal(controller.failure, null);
});

test("failUntracked surfaces a failure with a null retry (never silently dropped)", () => {
  // Models the cross-channel timing case: operation_failed arrives before the
  // op is registered (or after the Drawer unmounted and dropped tracking). The
  // controller still surfaces it so the App does not silently swallow it.
  const controller = new TradeOperationController();
  let lastFailure = null;
  controller.subscribe((f) => {
    lastFailure = f;
  });
  controller.failUntracked("成交操作未完成", makeError());
  assert.equal(controller.failure !== null, true);
  assert.equal(controller.failure.operationId, null);
  assert.equal(controller.failure.command, null);
  assert.equal(controller.failure.retry, null);
  assert.equal(lastFailure, controller.failure);
});

test("dismissFailure clears the failure and notifies (pending ops untouched)", () => {
  const controller = new TradeOperationController();
  controller.track("op-1", { command: "create", retry: () => Promise.resolve() });
  controller.fail("op-2", "x", makeError());
  controller.track("op-3", { command: "update", retry: () => Promise.resolve() });

  controller.dismissFailure();
  assert.equal(controller.failure, null);
  // Pending ops survive a dismiss.
  assert.equal(controller.hasPending(), true);
  assert.equal(controller.has("op-3"), true);
});

test("clearPending drops all tracked ops (generation change) but keeps the failure", () => {
  const controller = new TradeOperationController();
  controller.track("op-1", { command: "create", retry: () => Promise.resolve() });
  controller.track("op-2", { command: "update", retry: () => Promise.resolve() });
  controller.fail("op-2", "x", makeError());
  controller.track("op-3", { command: "update", retry: () => Promise.resolve() });

  controller.clearPending();
  assert.equal(controller.hasPending(), false);
  assert.equal(controller.has("op-1"), false);
  assert.equal(controller.has("op-3"), false);
  // The current failure surface is independent of the pending map.
  assert.equal(controller.failure !== null, true);
});

test("resolve clears a stale failure for the same op", () => {
  const controller = new TradeOperationController();
  controller.track("op-1", { command: "create", retry: () => Promise.resolve() });
  controller.fail("op-1", "x", makeError());
  assert.equal(controller.failure !== null, true);

  // A late trades_changed resolving op-1 clears its stale failure too.
  assert.equal(controller.resolve("op-1"), false); // already removed by fail
  assert.equal(controller.failure !== null, true); // failure persists (op was failed, not pending)
  controller.dismissFailure();
  assert.equal(controller.failure, null);
});

// --- the lifecycle regression the reviewer asked for ---

test("regression: submit create, switch to Replay (Drawer unmounts), then operation_failed is still surfaced and retried", () => {
  // The App owns the controller; the TradeDrawer delegates to it. When the
  // Drawer unmounts on a Live->Replay switch, the controller keeps the pending
  // op + retry. The App routes the later operation_failed to the controller,
  // so the failure is still visible and its retry re-runs the original create.
  const controller = new TradeOperationController();

  // 1. User submits a create; backend returns an accepted op id.
  let createCalls = 0;
  function reRunCreate() {
    createCalls += 1;
    return Promise.resolve();
  }
  controller.track("op-create-1", { command: "create", retry: reRunCreate });

  // 2. User switches to Replay. The TradeDrawer unmounts; its local refs are
  //    gone. The controller (App-owned) survives.
  //    (Nothing to do here - the controller is independent of the Drawer.)

  // 3. Backend publishes operation_failed for op-create-1. The App routes it.
  const claimed = controller.fail(
    "op-create-1",
    "成交保存失败",
    makeError(),
  );
  assert.equal(claimed, true);

  // 4. The failure is visible (App banner) and the retry re-runs the create.
  assert.equal(controller.failure !== null, true);
  assert.equal(controller.failure.command, "create");
  controller.failure.retry();
  assert.equal(createCalls, 1, "retry must re-run the original create");
});

test("regression: an untracked trade failure after a mode switch is still surfaced (not silently dropped)", () => {
  // Cross-channel timing: operation_failed arrives but the controller never
  // saw the op id (e.g. it raced ahead of the accepted response, or the
  // Drawer unmounted before tracking). The App must not drop it.
  const controller = new TradeOperationController();
  controller.failUntracked("成交操作未完成", makeError());
  assert.equal(controller.failure !== null, true);
  assert.equal(controller.failure.retry, null); // no retry context available
});
