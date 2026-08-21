import assert from "node:assert/strict";
import test from "node:test";

import { createLatestRequestTracker } from "../renderer/src/workbench-presenter.mjs";
import { enterTodayChart } from "../renderer/src/trading/enter-today-chart.mjs";

test("enterTodayChart shares one navigation sequence with performSecuritySelection", async () => {
  const navigationRequests = createLatestRequestTracker();
  const calls = [];
  let mode = "replay";

  const result = await enterTodayChart({
    symbol: "sh.600584",
    beginNavigation: () => navigationRequests.begin(),
    isCurrent: (sequence) => navigationRequests.isCurrent(sequence),
    resolveSecurity: async (symbol) => {
      calls.push(["resolve", symbol]);
      return { symbol, instrument_type: "stock" };
    },
    performSecuritySelection: async (identity, restoring, options) => {
      calls.push([
        "select",
        identity.symbol,
        restoring,
        options.navigationSequence,
      ]);
      // Nested begin would invalidate the shared sequence — production must not.
      assert.equal(
        navigationRequests.isCurrent(options.navigationSequence),
        true,
      );
      return true;
    },
    isReplayMode: () => mode === "replay",
    selectLiveMode: () => {
      calls.push(["selectLive"]);
      mode = "live";
    },
  });

  assert.deepEqual(result, { ok: true });
  assert.equal(mode, "live");
  assert.deepEqual(calls, [
    ["resolve", "sh.600584"],
    ["select", "sh.600584", false, 1],
    ["selectLive"],
  ]);
});

test("enterTodayChart keeps Replay when Live projection never becomes ready", async () => {
  const navigationRequests = createLatestRequestTracker();
  let mode = "replay";
  let switched = false;

  const result = await enterTodayChart({
    symbol: "sh.600584",
    beginNavigation: () => navigationRequests.begin(),
    isCurrent: (sequence) => navigationRequests.isCurrent(sequence),
    resolveSecurity: async () => ({
      symbol: "sh.600584",
      instrument_type: "stock",
    }),
    performSecuritySelection: async (_identity, _restoring, options) => {
      // Simulate cold start: selection accepted but projection not ready yet,
      // then a later navigation supersedes before ready arrives.
      assert.equal(navigationRequests.isCurrent(options.navigationSequence), true);
      navigationRequests.begin();
      return false;
    },
    isReplayMode: () => mode === "replay",
    selectLiveMode: () => {
      switched = true;
      mode = "live";
    },
  });

  assert.deepEqual(result, { ok: false, reason: "stale" });
  assert.equal(switched, false);
  assert.equal(mode, "replay");
});

test("enterTodayChart keeps Replay when liveReady is false", async () => {
  const navigationRequests = createLatestRequestTracker();
  let mode = "replay";

  const result = await enterTodayChart({
    symbol: "sh.600584",
    beginNavigation: () => navigationRequests.begin(),
    isCurrent: (sequence) => navigationRequests.isCurrent(sequence),
    resolveSecurity: async () => ({
      symbol: "sh.600584",
      instrument_type: "stock",
    }),
    performSecuritySelection: async () => false,
    isReplayMode: () => mode === "replay",
    selectLiveMode: () => {
      mode = "live";
    },
  });

  assert.deepEqual(result, { ok: false, reason: "live_not_ready" });
  assert.equal(mode, "replay");
});

test("enterTodayChart waits for delayed liveReady before leaving Replay", async () => {
  const navigationRequests = createLatestRequestTracker();
  let mode = "replay";

  const result = await enterTodayChart({
    symbol: "sh.600584",
    beginNavigation: () => navigationRequests.begin(),
    isCurrent: (sequence) => navigationRequests.isCurrent(sequence),
    resolveSecurity: async () => ({
      symbol: "sh.600584",
      instrument_type: "stock",
    }),
    performSecuritySelection: async (_identity, _restoring, options) => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return navigationRequests.isCurrent(options.navigationSequence);
    },
    isReplayMode: () => mode === "replay",
    selectLiveMode: () => {
      mode = "live";
    },
  });

  assert.deepEqual(result, { ok: true });
  assert.equal(mode, "live");
});

test("delayed resolve superseded by a later navigation returns stale, not identity", async () => {
  const navigationRequests = createLatestRequestTracker();
  let mode = "replay";
  let releaseResolve;
  const resolveGate = new Promise((resolve) => {
    releaseResolve = resolve;
  });

  const first = enterTodayChart({
    symbol: "sh.600584",
    beginNavigation: () => navigationRequests.begin(),
    isCurrent: (sequence) => navigationRequests.isCurrent(sequence),
    resolveSecurity: async () => {
      await resolveGate;
      return { symbol: "sh.600584", instrument_type: "stock" };
    },
    performSecuritySelection: async () => true,
    isReplayMode: () => mode === "replay",
    selectLiveMode: () => {
      mode = "live";
    },
  });

  // A later navigation begins while the first resolveSecurity is still pending.
  const second = await enterTodayChart({
    symbol: "sz.000001",
    beginNavigation: () => navigationRequests.begin(),
    isCurrent: (sequence) => navigationRequests.isCurrent(sequence),
    resolveSecurity: async (symbol) => ({
      symbol,
      instrument_type: "stock",
    }),
    performSecuritySelection: async () => true,
    isReplayMode: () => mode === "replay",
    selectLiveMode: () => {
      mode = "live";
    },
  });
  assert.deepEqual(second, { ok: true });

  releaseResolve();
  const firstResult = await first;
  assert.deepEqual(firstResult, { ok: false, reason: "stale" });
  assert.equal(mode, "live");
});

test("missing identity still returns identity failure", async () => {
  const navigationRequests = createLatestRequestTracker();
  const result = await enterTodayChart({
    symbol: "sh.600584",
    beginNavigation: () => navigationRequests.begin(),
    isCurrent: (sequence) => navigationRequests.isCurrent(sequence),
    resolveSecurity: async () => null,
    performSecuritySelection: async () => true,
    isReplayMode: () => true,
    selectLiveMode: () => {},
  });
  assert.deepEqual(result, { ok: false, reason: "identity" });
});
