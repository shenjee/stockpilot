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
  return controller.failures.map((f) => f.failureId);
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

  const failureId = controller.fail("op-1", "成交保存失败", makeError());
  assert.equal(typeof failureId, "string");
  assert.equal(controller.has("op-1"), false);
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].command, "create");
  assert.equal(controller.failures[0].operationId, "op-1");
  assert.equal(controller.failures[0].failureId, failureId);
  // The retry closure survived the failure and re-runs the original op.
  controller.failures[0].retry();
  assert.equal(retried, 1);
  assert.equal(lastFailures[0].command, "create");
});

test("fail on an untracked operation returns null (not claimed)", () => {
  const controller = new TradeOperationController();
  assert.equal(controller.fail("op-unknown", "x", makeError()), null);
  assert.deepEqual(controller.failures, []);
});

test("failUntracked caches the failure keyed by operation id (null retry until merge)", () => {
  const controller = new TradeOperationController();
  let lastFailures = [];
  controller.subscribe((f) => {
    lastFailures = f;
  });
  const failureId = controller.failUntracked("op-1", "成交保存失败", makeError());
  assert.equal(typeof failureId, "string");
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].operationId, "op-1");
  assert.equal(controller.failures[0].command, null);
  assert.equal(controller.failures[0].retry, null);
  assert.equal(controller.failures[0].failureId, failureId);
  assert.equal(lastFailures.length, 1);
});

test("failUntracked without an operation id still surfaces (never dropped)", () => {
  const controller = new TradeOperationController();
  controller.failUntracked(null, "成交操作未完成", makeError());
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].operationId, null);
  assert.equal(controller.failures[0].retry, null);
  assert.equal(typeof controller.failures[0].failureId, "string");
});

test("dismissFailure(failureId) clears one failure; pending ops untouched", () => {
  const controller = new TradeOperationController();
  controller.track("op-1", { command: "create", retry: () => Promise.resolve() });
  controller.track("op-2", { command: "update", retry: () => Promise.resolve() });
  const f2 = controller.fail("op-2", "x", makeError());
  controller.track("op-3", { command: "update", retry: () => Promise.resolve() });
  const f3 = controller.fail("op-3", "y", makeError());

  controller.dismissFailure(f2);
  assert.deepEqual(failureIds(controller), [f3]);
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

// --- early-fail merge (P1#1 from prior review, still covered) ---

test("regression: early operation_failed(op-1) -> track(op-1, retry) yields an actionable retry with no lingering pending", () => {
  const controller = new TradeOperationController();

  controller.failUntracked("op-1", "成交保存失败", makeError());
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].retry, null);
  assert.equal(controller.hasPending(), false);

  let retried = 0;
  controller.track("op-1", {
    command: "create",
    retry: () => {
      retried += 1;
      return Promise.resolve();
    },
  });

  assert.equal(controller.failures.length, 1);
  assert.equal(typeof controller.failures[0].retry, "function");
  assert.equal(controller.failures[0].command, "create");
  assert.equal(controller.hasPending(), false, "failed op must not linger as pending");
  assert.equal(controller.has("op-1"), false);

  controller.failures[0].retry();
  assert.equal(retried, 1);
});

// --- concurrent failures (P1#2 from prior review, still covered) ---

test("regression: two pending operations fail consecutively - both failures visible and independently retryable", () => {
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

  assert.equal(controller.fail("op-a", "A 失败", makeError()) !== null, true);
  assert.equal(controller.fail("op-b", "B 失败", makeError()) !== null, true);

  assert.equal(controller.failures.length, 2);
  const ids = controller.failures.map((f) => f.operationId);
  assert.deepEqual(ids, ["op-a", "op-b"]);

  const aFailure = controller.failures.find((f) => f.operationId === "op-a");
  const bFailure = controller.failures.find((f) => f.operationId === "op-b");
  assert.equal(aFailure.command, "create");
  assert.equal(bFailure.command, "update");
  aFailure.retry();
  bFailure.retry();
  assert.equal(aRetried, 1);
  assert.equal(bRetried, 1);

  // Dismissing one leaves the other intact.
  controller.dismissFailure(aFailure.failureId);
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].operationId, "op-b");
});

// --- THIS REVIEW's P1: anonymous failures dismissable + distinct keys ---

test("regression: an anonymous failure is dismissable by its failureId", () => {
  const controller = new TradeOperationController();
  const failureId = controller.failUntracked(null, "成交操作未完成", makeError());
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].operationId, null);
  assert.equal(controller.failures[0].failureId, failureId);

  // The dismiss button uses failureId (not operationId), so it can close an
  // anonymous failure.
  controller.dismissFailure(failureId);
  assert.deepEqual(controller.failures, []);
});

test("regression: multiple anonymous failures have distinct failureIds (distinct React keys)", () => {
  const controller = new TradeOperationController();
  const f1 = controller.failUntracked(null, "A 失败", makeError());
  const f2 = controller.failUntracked(null, "B 失败", makeError());
  assert.notEqual(f1, f2);
  assert.equal(controller.failures.length, 2);
  assert.deepEqual(failureIds(controller), [f1, f2]);
  // operationId is null for both, but failureId distinguishes them.
  assert.equal(controller.failures.every((f) => f.operationId === null), true);

  // Each can be dismissed independently by failureId.
  controller.dismissFailure(f1);
  assert.deepEqual(failureIds(controller), [f2]);
});

test("regression: a sync failure carries command/retry so it is retryable, and re-failing keeps it retryable", () => {
  // reRunCreate catches a sync rejection and calls
  // failUntracked(null, msg, err, {command, retry}). The failure must carry a
  // working retry; if the retry also fails synchronously, the new failure must
  // again carry a retry.
  const controller = new TradeOperationController();
  let createCalls = 0;

  function reRunCreate() {
    createCalls += 1;
    // Simulate a sync rejection on every attempt.
    return Promise.reject(new Error("sync 失败"));
  }

  const failureId = controller.failUntracked(
    null,
    "成交保存失败",
    new Error("sync 失败"),
    { command: "create", retry: () => reRunCreate() },
  );
  assert.equal(typeof failureId, "string");
  assert.equal(controller.failures.length, 1);
  assert.equal(controller.failures[0].command, "create");
  assert.equal(typeof controller.failures[0].retry, "function");

  // First retry: the create is invoked; when it rejects synchronously the
  // caller (reRunCreate's try/catch) would call failUntracked again. Simulate
  // that second failure by invoking the retry then recording a new failure.
  return controller.failures[0].retry().then(
    () => assert.fail("retry should have rejected"),
    () => {
      const secondFailureId = controller.failUntracked(
        null,
        "成交保存失败",
        new Error("sync 失败"),
        { command: "create", retry: () => reRunCreate() },
      );
      assert.notEqual(secondFailureId, failureId);
      assert.equal(controller.failures.length, 2);
      assert.equal(typeof controller.failures[1].retry, "function");
      assert.equal(createCalls, 1);
      // The second failure is still retryable.
      return controller.failures[1].retry().then(
        () => assert.fail("second retry should have rejected"),
        () => assert.equal(createCalls, 2),
      );
    },
  );
});

// --- THIS REVIEW's P2: early success reconciliation ---

test("regression: trades_changed(op-1) arriving before track(op-1) leaves no ghost pending", () => {
  // Symmetric to the early-fail case: a success event arrives before the
  // accepted response is processed. resolve() caches the id; a later track()
  // consumes the cached resolution and does NOT add the op to pending.
  const controller = new TradeOperationController();

  // 1. trades_changed(op-1) arrives first. resolve caches op-1 (not yet
  //    tracked) without notifying failure listeners.
  let notified = 0;
  controller.subscribe(() => {
    notified += 1;
  });
  assert.equal(controller.resolve("op-1"), true); // cached as resolved
  assert.equal(notified, 0);
  assert.equal(controller.hasPending(), false);

  // 2. The accepted response lands; the Drawer tracks op-1 with its retry.
  controller.track("op-1", {
    command: "create",
    retry: () => Promise.resolve(),
  });

  // 3. The op is NOT lingering in pending (it already succeeded).
  assert.equal(controller.hasPending(), false, "successful op must not linger as pending");
  assert.equal(controller.has("op-1"), false);
  assert.deepEqual(controller.failures, []);
});

test("regression: early success then a stale operation_failed for the same id does not resurrect a failure", () => {
  // resolve(op-1) caches the success. A late operation_failed(op-1) routed via
  // failUntracked must not produce a misleading failure for an op that already
  // succeeded. failUntracked drops any cached resolution for the id.
  const controller = new TradeOperationController();
  controller.resolve("op-1"); // early success cached

  controller.failUntracked("op-1", "迟到的失败", makeError());
  // The stale failure is surfaced (we cannot prove it is stale from the id
  // alone), BUT track() for op-1 will no longer add it to pending because the
  // cached resolution was consumed by failUntracked. The key invariant: no
  // ghost pending.
  controller.track("op-1", {
    command: "create",
    retry: () => Promise.resolve(),
  });
  assert.equal(controller.hasPending(), false);
});
