import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createChartProjection } from "../renderer/src/charts/chart-projection.mjs";
import { LiveProjectionController } from "../renderer/src/charts/live-projection-controller.mjs";
import { waitForLiveProjectionReady } from "../renderer/src/charts/wait-live-projection-ready.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const baseSnapshot = JSON.parse(
  readFileSync(
    resolve(testDir, "../contracts/fixtures/chart-groups-v1.json"),
    "utf8",
  ),
);

function emptyProjection(generation = 1) {
  return createChartProjection(baseSnapshot, {
    service_generation: generation,
    session_id: null,
    revision: 0,
  });
}

function readySnapshot(symbol, sessionId) {
  return {
    ...baseSnapshot,
    session: {
      ...(baseSnapshot.session ?? {}),
      session_id: sessionId,
      session_type: "live",
      symbol,
      state: "ready",
      revision: 1,
    },
  };
}

test("waitForLiveProjectionReady resolves when a later snapshot becomes ready", async () => {
  const live = new LiveProjectionController(emptyProjection(2));
  live.beginSession(emptyProjection(2).snapshot, 2, "live-1");

  const pending = waitForLiveProjectionReady({
    getProjection: () => live.projection,
    subscribe: (listener) => live.subscribe(listener),
    symbol: "sh.600584",
    sessionId: "live-1",
    serviceGeneration: 2,
    isCurrent: () => true,
    timeoutMs: 1000,
  });

  await Promise.resolve();
  live.applySnapshot(readySnapshot("sh.600584", "live-1"), {
    service_generation: 2,
    session_id: "live-1",
    revision: 1,
  });

  assert.equal(await pending, true);
});

test("waitForLiveProjectionReady fails when navigation is superseded", async () => {
  const live = new LiveProjectionController(emptyProjection(2));
  let current = true;
  const pending = waitForLiveProjectionReady({
    getProjection: () => live.projection,
    subscribe: (listener) => live.subscribe(listener),
    symbol: "sh.600584",
    sessionId: "live-1",
    serviceGeneration: 2,
    isCurrent: () => current,
    timeoutMs: 1000,
  });

  await Promise.resolve();
  current = false;
  live.applySnapshot(readySnapshot("sh.600584", "live-1"), {
    service_generation: 2,
    session_id: "live-1",
    revision: 1,
  });

  assert.equal(await pending, false);
});

test("waitForLiveProjectionReady returns true immediately when already ready", async () => {
  const live = new LiveProjectionController(emptyProjection(2));
  live.applySnapshot(readySnapshot("sh.600584", "live-1"), {
    service_generation: 2,
    session_id: "live-1",
    revision: 1,
  });

  const ready = await waitForLiveProjectionReady({
    getProjection: () => live.projection,
    subscribe: (listener) => live.subscribe(listener),
    symbol: "sh.600584",
    sessionId: "live-1",
    serviceGeneration: 2,
    isCurrent: () => true,
    timeoutMs: 100,
  });
  assert.equal(ready, true);
});
