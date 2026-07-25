import assert from "node:assert/strict";
import test from "node:test";

import { createSerialTaskQueue } from "../renderer/src/serial-task-queue.mjs";

test("preference writes execute in enqueue order even while an older write is pending", async () => {
  const started = [];
  const releases = [];
  const queue = createSerialTaskQueue(
    (value) =>
      new Promise((resolve) => {
        started.push(value);
        releases.push(() => resolve(value));
      }),
  );

  const first = queue.enqueue("older");
  const second = queue.enqueue("newer");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, ["older"]);

  releases.shift()();
  await first;
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, ["older", "newer"]);
  releases.shift()();
  assert.equal(await second, "newer");
});

test("a failed write does not block the next queued preference copy", async () => {
  const queue = createSerialTaskQueue(async (value) => {
    if (value === "failed") throw new Error("disk busy");
    return value;
  });

  await assert.rejects(queue.enqueue("failed"), /disk busy/);
  assert.equal(await queue.enqueue("latest"), "latest");
});
