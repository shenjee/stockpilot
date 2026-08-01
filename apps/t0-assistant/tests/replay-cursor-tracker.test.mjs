import assert from "node:assert/strict";
import test from "node:test";

import { createReplayCursorTracker } from "../renderer/src/replay-cursor-tracker.mjs";

test("early workbench_snapshot settles when HTTP adopt arrives later", () => {
  const tracker = createReplayCursorTracker();
  assert.equal(tracker.noteOutcome("op-step-1", "completed"), "cached");
  assert.equal(tracker.activeOperationId, null);

  const adopted = tracker.adopt("op-step-1");
  assert.deepEqual(adopted, { status: "already_settled", early: "completed" });
  assert.equal(tracker.activeOperationId, null);
});

test("early operation_failed settles when HTTP adopt arrives later", () => {
  const tracker = createReplayCursorTracker();
  assert.equal(tracker.noteOutcome("op-seek-1", "failed"), "cached");

  const adopted = tracker.adopt("op-seek-1");
  assert.deepEqual(adopted, { status: "already_settled", early: "failed" });
  assert.equal(tracker.activeOperationId, null);
});

test("normal order tracks then settles on matching outcome", () => {
  const tracker = createReplayCursorTracker();
  assert.deepEqual(tracker.adopt("op-step-2"), {
    status: "tracking",
    early: null,
  });
  assert.equal(tracker.activeOperationId, "op-step-2");
  assert.equal(tracker.noteOutcome("op-step-2", "completed"), "settled");
  assert.equal(tracker.activeOperationId, null);
});

test("unrelated early outcomes do not settle a different operation", () => {
  const tracker = createReplayCursorTracker();
  tracker.noteOutcome("op-old", "completed");
  assert.deepEqual(tracker.adopt("op-new"), {
    status: "tracking",
    early: null,
  });
  assert.equal(tracker.activeOperationId, "op-new");
  assert.equal(tracker.noteOutcome("op-old", "failed"), "cached");
  assert.equal(tracker.activeOperationId, "op-new");
});

test("missing operation_id clears tracking without leaving a pending id", () => {
  const tracker = createReplayCursorTracker();
  assert.deepEqual(tracker.adopt(null), {
    status: "no_operation",
    early: null,
  });
  assert.equal(tracker.activeOperationId, null);
});
