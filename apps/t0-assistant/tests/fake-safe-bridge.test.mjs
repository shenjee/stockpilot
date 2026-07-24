import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { SAFE_BRIDGE_METHODS } from "../electron/safe-bridge.mjs";
import { createFakeSafeBridge } from "./fake-safe-bridge.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  await readFile(resolve(testDir, "../contracts/fixtures/workbench-flow-v1.json"), "utf8"),
);
const replayFixture = JSON.parse(
  await readFile(resolve(testDir, "../contracts/fixtures/replay-speed-v1.json"), "utf8"),
);

test("Fake Safe Bridge exposes only the domain allowlist", () => {
  const { bridge } = createFakeSafeBridge(fixture);
  assert.deepEqual(Object.keys(bridge), SAFE_BRIDGE_METHODS);
  const serialized = Object.keys(bridge).join(" ");
  for (const forbidden of ["invoke", "request", "fetch", "http", "ws", "port", "token", "credential"]) {
    assert.equal(serialized.includes(forbidden), false);
  }
  assert.equal(Object.isFrozen(bridge), true);
});

test("Fake Safe Bridge serves the shared full snapshot and records snake_case commands", async () => {
  const { bridge, controller } = createFakeSafeBridge(fixture);
  const response = await bridge.getLiveSnapshot({
    schema_version: "t0_app_v1",
    request_id: "node-snapshot-1",
    command: "get_live_snapshot",
    session_id: fixture.session_id,
    payload: {},
  });
  assert.equal(response.data.session.session_id, fixture.session_id);
  assert.equal(response.data.session.revision, 1);
  assert.deepEqual(controller.calls[0], {
    command: "get_live_snapshot",
    request: {
      schema_version: "t0_app_v1",
      request_id: "node-snapshot-1",
      command: "get_live_snapshot",
      session_id: fixture.session_id,
      payload: {},
    },
  });
});

test("Fake Safe Bridge deterministically delivers increments, out-of-order events, and errors", () => {
  const { bridge, controller } = createFakeSafeBridge(fixture);
  const received = [];
  const unsubscribe = bridge.onAppEvent((event) => received.push(event));

  controller.emitInitialSnapshot();
  controller.emitOutOfOrder();
  controller.emitError();
  unsubscribe();
  controller.emitIncrementals();

  assert.deepEqual(received.map((event) => event.revision), [1, 2, 4, 3, 5]);
  assert.equal(received.at(-1).event_type, "operation_failed");
  assert.equal(received.at(-1).operation_id, received.at(-1).payload.operation_id);
});

test("Fake Safe Bridge subscriptions are isolated and removable", () => {
  const { bridge, controller } = createFakeSafeBridge(fixture);
  const appEvents = [];
  const replayEvents = [];
  const stopApp = bridge.onAppEvent((event) => appEvents.push(event));
  bridge.onReplayEvent((event) => replayEvents.push(event));

  controller.emitInitialSnapshot();
  controller.emitReplayEvent({ schema_version: "t0_replay_v1", revision: 1 });
  stopApp();
  controller.emitInitialSnapshot();

  assert.equal(appEvents.length, 1);
  assert.equal(replayEvents.length, 1);
});

test("Fake Safe Bridge returns Replay v1 identities and a complete Replay snapshot", async () => {
  const { bridge } = createFakeSafeBridge(fixture, { replayFixture });
  const request = {
    schema_version: "t0_replay_v1",
    request_id: "replay-command-1",
    session_id: "replay-1",
  };
  const results = await Promise.all([
    bridge.selectSymbol({ ...request, symbol: "600000" }),
    bridge.beginReplay({ ...request, symbol: "sh.600000", trade_date: "2026-07-01" }),
    bridge.setReplayPlayback({ ...request, playing: false }),
    bridge.setReplaySpeed({ ...request, playback_speed: 5 }),
    bridge.stepReplay(request),
    bridge.seekReplay({ ...request, target_time: "2026-07-01 10:23:00" }),
    bridge.endReplay(request),
  ]);
  const snapshot = await bridge.getReplaySnapshot(request);

  assert.ok(results.every((result) => result.schema_version === "t0_replay_v1"));
  assert.equal(results[0].security.symbol, "sh.600000");
  assert.equal(results[1].operation_id, "operation-begin_replay");
  assert.equal(results[4].operation_id, "operation-step_replay");
  assert.equal(results[5].operation_id, "operation-seek_replay");
  assert.deepEqual(snapshot, replayFixture.snapshot);
});
