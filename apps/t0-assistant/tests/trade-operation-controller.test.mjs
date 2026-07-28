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

function failureIds(controller) {
  return controller.failures.map((f) => f.operationId);
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
  assert.deepEqual(controller.failures, []);
});

test("track + fail publishes a failure with the captured retry", () => {
  const controller = new TradeOperationController();
  let lastFailures = [];
  controller.subscribe((f) => {
    lastFailures = f;
  });
  let retried = 0;
  controller.track("op-1", {
    command: "create",
    retry: () => {
      retried += 1;
      return Promise.resolve();
    },
  });

  assert.equal(controller.fail("op-1", "成交保存失败", makeError()), true);
  assert.equal(controller.has("op-1"), false);
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].command, "create");
  assert.equal(controller.failures[0].operationId, "op-1");
  // The retry closure survived the failure and re-runs the original op.
  controller.failures[0].retry();
  assert.equal(retried, 1);
  assert.equal(lastFailures[0].command, "create");
});

test("fail on an untracked operation returns false (not claimed)", () => {
  const controller = new TradeOperationController();
  assert.equal(controller.fail("op-unknown", "x", makeError()), false);
  assert.deepEqual(controller.failures, []);
});

test("failUntracked caches the failure keyed by operation id (null retry until merge)", () => {
  const controller = new TradeOperationController();
  let lastFailures = [];
  controller.subscribe((f) => {
    lastFailures = f;
  });
  controller.failUntracked("op-1", "成交保存失败", makeError());
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].operationId, "op-1");
  assert.equal(controller.failures[0].command, null);
  assert.equal(controller.failures[0].retry, null);
  assert.equal(lastFailures.length, 1);
});

test("failUntracked without an operation id still surfaces (never dropped)", () => {
  const controller = new TradeOperationController();
  controller.failUntracked(null, "成交操作未完成", makeError());
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].operationId, null);
  assert.equal(controller.failures[0].retry, null);
});

test("dismissFailure(id) clears one failure; pending ops untouched", () => {
  const controller = new TradeOperationController();
  controller.track("op-1", { command: "create", retry: () => Promise.resolve() });
  controller.track("op-2", { command: "update", retry: () => Promise.resolve() });
  controller.fail("op-2", "x", makeError());
  controller.track("op-3", { command: "update", retry: () => Promise.resolve() });
  controller.fail("op-3", "y", makeError());

  controller.dismissFailure("op-2");
  assert.deepEqual(failureIds(controller), ["op-3"]);
  // op-1 is still pending; op-2 and op-3 failed and left pending.
  assert.equal(controller.has("op-1"), true);
  assert.equal(controller.has("op-3"), false);
});

test("dismissAllFailures clears all failures", () => {
  const controller = new TradeOperationController();
  controller.track("op-1", { command: "create", retry: () => Promise.resolve() });
  controller.fail("op-1", "x", makeError());
  controller.failUntracked("op-2", "y", makeError());
  assert.equal(controller.failures.length, 2);

  controller.dismissAllFailures();
  assert.deepEqual(controller.failures, []);
});

test("clearPending drops all tracked ops (generation change) but keeps failures", () => {
  const controller = new TradeOperationController();
  controller.track("op-1", { command: "create", retry: () => Promise.resolve() });
  controller.track("op-2", { command: "update", retry: () => Promise.resolve() });
  controller.fail("op-2", "x", makeError());
  controller.track("op-3", { command: "update", retry: () => Promise.resolve() });

  controller.clearPending();
  assert.equal(controller.hasPending(), false);
  assert.equal(controller.has("op-1"), false);
  assert.equal(controller.has("op-3"), false);
  // The failure surface is independent of the pending map.
  assert.equal(controller.failures.length, 1);
});

test("resolve clears a cached failure for the same id (success supersedes stale failure)", () => {
  const controller = new TradeOperationController();
  controller.failUntracked("op-1", "x", makeError());
  assert.equal(controller.failures.length, 1);

  // A late trades_changed resolving op-1 clears its stale failure.
  assert.equal(controller.resolve("op-1"), true);
  assert.deepEqual(controller.failures, []);
});

// --- the two regressions the reviewer asked for ---

test("regression: early operation_failed(op-1) -> track(op-1, retry) yields an actionable retry with no lingering pending", () => {
  // Cross-channel timing: operation_failed arrives before the accepted
  // response is processed. failUntracked caches it (null retry); the later
  // track() MERGES the retry in and does NOT re-add the op to pending.
  const controller = new TradeOperationController();

  // 1. operation_failed(op-1) arrives first.
  controller.failUntracked("op-1", "成交保存失败", makeError());
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].retry, null);
  assert.equal(controller.hasPending(), false);

  // 2. The accepted response lands; the Drawer tracks op-1 with its retry.
  let retried = 0;
  controller.track("op-1", {
    command: "create",
    retry: () => {
      retried += 1;
      return Promise.resolve();
    },
  });

  // 3. The failure now has an actionable retry, and op-1 is NOT lingering in
  //    pending (it already failed).
  assert.equal(controller.failures.length, 1);
  assert.equal(typeof controller.failures[0].retry, "function");
  assert.equal(controller.failures[0].command, "create");
  assert.equal(controller.hasPending(), false, "failed op must not linger as pending");
  assert.equal(controller.has("op-1"), false);

  // 4. The retry runs the original create.
  controller.failures[0].retry();
  assert.equal(retried, 1);
});

test("regression: two pending operations fail consecutively - both failures visible and independently retryable", () => {
  // The UI does not forbid concurrent trade writes, so two ops may be pending.
  // A single-slot failure surface would overwrite the first; the Map keeps both.
  const controller = new TradeOperationController();

  let aRetried = 0;
  let bRetried = 0;
  controller.track("op-a", {
    command: "create",
    retry: () => {
      aRetried += 1;
      return Promise.resolve();
    },
  });
  controller.track("op-b", {
    command: "update",
    retry: () => {
      bRetried += 1;
      return Promise.resolve();
    },
  });

  // op-a fails first, then op-b. Neither overwrites the other.
  assert.equal(controller.fail("op-a", "A 失败", makeError()), true);
  assert.equal(controller.fail("op-b", "B 失败", makeError()), true);

  assert.equal(controller.failures.length, 2);
  assert.deepEqual(failureIds(controller), ["op-a", "op-b"]);

  // Each failure keeps its own retry and re-runs its own op.
  const aFailure = controller.failures.find((f) => f.operationId === "op-a");
  const bFailure = controller.failures.find((f) => f.operationId === "op-b");
  assert.equal(aFailure.command, "create");
  assert.equal(bFailure.command, "update");
  aFailure.retry();
  bFailure.retry();
  assert.equal(aRetried, 1);
  assert.equal(bRetried, 1);

  // Dismissing one leaves the other intact.
  controller.dismissFailure("op-a");
  assert.deepEqual(failureIds(controller), ["op-b"]);
});

test("regression: early fail + later track, then a second concurrent failure, all retained", () => {
  // Combines both fixes: op-1 fails early and is merged on track; op-2 fails
  // concurrently. Both must be visible and retryable.
  const controller = new TradeOperationController();
  controller.failUntracked("op-1", "A 失败", makeError());

  let oneRetried = 0;
  controller.track("op-1", {
    command: "create",
    retry: () => {
      oneRetried += 1;
      return Promise.resolve();
    },
  });

  let twoRetried = 0;
  controller.track("op-2", {
    command: "delete",
    retry: () => {
      twoRetried += 1;
      return Promise.resolve();
    },
  });
  assert.equal(controller.fail("op-2", "B 失败", makeError()), true);

  assert.equal(controller.failures.length, 2);
  assert.deepEqual(failureIds(controller), ["op-1", "op-2"]);
  controller.failures.find((f) => f.operationId === "op-1").retry();
  controller.failures.find((f) => f.operationId === "op-2").retry();
  assert.equal(oneRetried, 1);
  assert.equal(twoRetried, 1);
});

test("subscribe receives the failures array (not a single failure)", () => {
  const controller = new TradeOperationController();
  let received = null;
  controller.subscribe((f) => {
    received = f;
  });
  controller.failUntracked("op-1", "x", makeError());
  assert.ok(Array.isArray(received));
  assert.equal(received.length, 1);
});
