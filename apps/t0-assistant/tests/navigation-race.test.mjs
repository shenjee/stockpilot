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

test("enter-today shares one navigation sequence with performSecuritySelection", async () => {
  // Regression for #164: handleEnterDayChart.begin() then a nested
  // performSecuritySelection.begin() would invalidate the outer sequence so
  // isCurrent(outer) is always false after await, blocking Replay→Live.
  const navigationRequests = createLatestRequestTracker();
  const steps = [];

  async function enterTodayChart() {
    const requestSequence = navigationRequests.begin();
    steps.push("enter-begin");
    await Promise.resolve();
    if (!navigationRequests.isCurrent(requestSequence)) {
      steps.push("enter-stale-before-select");
      return;
    }
    const liveReady = await selectSecurityShared(requestSequence);
    if (!navigationRequests.isCurrent(requestSequence)) {
      steps.push("enter-stale-after-select");
      return;
    }
    if (liveReady) steps.push("switched-to-live");
  }

  async function selectSecurityShared(navigationSequence) {
    const selectionSequence = navigationSequence;
    await Promise.resolve();
    if (!navigationRequests.isCurrent(selectionSequence)) return false;
    steps.push("select-accepted");
    return true;
  }

  await enterTodayChart();
  assert.deepEqual(steps, [
    "enter-begin",
    "select-accepted",
    "switched-to-live",
  ]);
});

test("a nested begin inside enter-today would block the Live switch", async () => {
  const navigationRequests = createLatestRequestTracker();
  let switched = false;

  async function brokenEnterToday() {
    const requestSequence = navigationRequests.begin();
    await Promise.resolve();
    // Bug: nested begin invalidates requestSequence.
    navigationRequests.begin();
    const liveReady = true;
    if (!navigationRequests.isCurrent(requestSequence)) return;
    if (liveReady) switched = true;
  }

  await brokenEnterToday();
  assert.equal(switched, false);
});
