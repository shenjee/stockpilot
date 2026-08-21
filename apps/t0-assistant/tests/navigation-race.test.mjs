import assert from "node:assert/strict";
import test from "node:test";

import { createLatestRequestTracker } from "../renderer/src/workbench-presenter.mjs";

test("a later security selection invalidates an in-flight historical request", async () => {
  // This is the race guard used by App.tsx: performSecuritySelection and
  // handleEnterDayChart share one navigation tracker.  If a historical request
  // is in flight when the user selects another security, the stale historical
  // response must be dropped even though it passes its own local sequence gate.
  const navigationRequests = createLatestRequestTracker();
  const applied = [];

  async function loadHistoricalSnapshot() {
    const requestSequence = navigationRequests.begin();
    await new Promise((resolve) => setTimeout(resolve, 50));
    if (!navigationRequests.isCurrent(requestSequence)) return;
    applied.push("historical-applied");
  }

  async function selectSecurity() {
    const selectionSequence = navigationRequests.begin();
    await Promise.resolve();
    if (!navigationRequests.isCurrent(selectionSequence)) return;
    applied.push("selection-applied");
  }

  const historicalPromise = loadHistoricalSnapshot();
  const selectionPromise = selectSecurity();
  await Promise.all([historicalPromise, selectionPromise]);

  assert.deepEqual(applied, ["selection-applied"]);
});

test("consecutive security selections invalidate all but the latest", async () => {
  const navigationRequests = createLatestRequestTracker();
  const applied = [];

  async function selectSecurity(id) {
    const selectionSequence = navigationRequests.begin();
    await new Promise((resolve) => setTimeout(resolve, 20));
    if (!navigationRequests.isCurrent(selectionSequence)) return;
    applied.push(id);
  }

  await Promise.all([
    selectSecurity("first"),
    selectSecurity("second"),
    selectSecurity("third"),
  ]);

  assert.deepEqual(applied, ["third"]);
});
