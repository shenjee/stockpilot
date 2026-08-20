import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { selectActiveWorkbenchProjection } from "../renderer/src/charts/active-workbench-projection.mjs";
import { LiveProjectionController } from "../renderer/src/charts/live-projection-controller.mjs";
import { ReplaySessionController } from "../renderer/src/charts/replay-session-controller.mjs";
import { createChartProjection } from "../renderer/src/charts/chart-projection.mjs";
import {
  deriveReplayControls,
  replayFactsFromSnapshot,
} from "../renderer/src/replay-controls.mjs";
import { WorkbenchMode } from "../renderer/src/workbench-layout.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const [workbenchFixture, replayFixture] = await Promise.all([
  readFile(
    resolve(testDir, "../contracts/fixtures/workbench-flow-v2.json"),
    "utf8",
  ).then(JSON.parse),
  readFile(
    resolve(testDir, "../contracts/fixtures/replay-speed-v2.json"),
    "utf8",
  ).then(JSON.parse),
]);

const liveBaseline = () =>
  createChartProjection(
    workbenchFixture.initial_snapshot_event.payload,
    workbenchFixture.initial_snapshot_event,
  );

const [liveMarketUpdate, liveIndicatorUpdate] =
  workbenchFixture.incremental_events;

function cloneReplaySnapshot(overrides = {}) {
  const snapshot = structuredClone(replayFixture.snapshot);
  if (overrides.session) {
    snapshot.session = { ...snapshot.session, ...overrides.session };
  }
  if (overrides.replay) {
    snapshot.replay = { ...snapshot.replay, ...overrides.replay };
  }
  return snapshot;
}

function activeFrom(mode, live, replay) {
  return selectActiveWorkbenchProjection({
    mode,
    liveProjection: live.projection,
    replayProjection: replay.projection,
    loadingFallbackProjection: replay.loadingFallbackProjection,
  });
}

test("LiveProjectionController applies events and notifies subscribers", () => {
  const live = new LiveProjectionController(liveBaseline());
  let notifications = 0;
  live.subscribe(() => {
    notifications += 1;
  });

  const after = live.applyEvent(liveMarketUpdate);
  assert.equal(after.revision, 2);
  assert.equal(live.projection.revision, 2);
  assert.equal(notifications, 1);

  const ignored = live.applyEvent({
    ...liveMarketUpdate,
    revision: 2,
  });
  assert.equal(ignored, live.projection);
  assert.equal(notifications, 1);
});

test("LiveProjectionController tracks rebaseline request keys", () => {
  const live = new LiveProjectionController(liveBaseline());
  assert.equal(live.beginRebaselineRequest("3:live-fixture-1:1"), true);
  assert.equal(live.beginRebaselineRequest("3:live-fixture-1:1"), false);
  live.clearRebaselineRequest();
  assert.equal(live.rebaselineRequestKey, null);
});

test("ReplaySessionController freezes fallback at enterMode; Live advances separately", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);

  live.applyEvent(liveMarketUpdate);
  const foreground = live.projection;
  replay.enterMode(foreground);
  live.applyEvent(liveIndicatorUpdate);

  assert.equal(replay.loadingFallbackProjection, foreground);
  assert.equal(
    activeFrom(WorkbenchMode.REPLAY, live, replay),
    foreground,
  );
  assert.equal(live.projection.revision, 3);
  assert.equal(activeFrom(WorkbenchMode.REPLAY, live, replay).revision, 2);
});

test("ReplaySessionController acceptSnapshot replaces fallback atomically", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  replay.beginSession("replay-1", "op-load-1");
  assert.equal(replay.loading, true);

  const snapshot = cloneReplaySnapshot();
  assert.equal(
    replay.acceptSnapshot(snapshot, {
      service_generation: live.projection.serviceGeneration,
      session_id: "replay-1",
      revision: snapshot.session.revision,
      operation_id: "op-load-1",
    }),
    true,
  );

  const active = activeFrom(WorkbenchMode.REPLAY, live, replay);
  assert.equal(active, replay.projection);
  const facts = replayFactsFromSnapshot(active.snapshot);
  assert.equal(facts?.sessionId, "replay-1");
  assert.equal(deriveReplayControls(facts).active, true);
  assert.equal(replay.loading, false);
  assert.equal(replay.loadOperationId, null);
});

test("mismatched Replay snapshots cannot replace fallback", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  replay.beginSession("replay-1");
  const fallback = replay.loadingFallbackProjection;

  assert.equal(
    replay.acceptSnapshot(cloneReplaySnapshot({ session: { session_id: "x" } }), {
      service_generation: live.projection.serviceGeneration,
      session_id: "x",
      revision: 8,
    }),
    false,
  );
  assert.equal(activeFrom(WorkbenchMode.REPLAY, live, replay), fallback);
});

test("load-operation mismatch cannot replace fallback while loading", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  replay.beginSession("replay-1", "expected-load-op");
  const fallback = replay.loadingFallbackProjection;

  assert.equal(
    replay.acceptSnapshot(cloneReplaySnapshot(), {
      service_generation: live.projection.serviceGeneration,
      session_id: "replay-1",
      revision: 8,
      operation_id: "other-op",
    }),
    false,
  );
  assert.equal(replay.projection, null);
  assert.equal(replay.loading, true);
  assert.equal(replay.loadOperationId, "expected-load-op");
  assert.equal(activeFrom(WorkbenchMode.REPLAY, live, replay), fallback);

  assert.equal(
    replay.acceptSnapshot(cloneReplaySnapshot(), {
      service_generation: live.projection.serviceGeneration,
      session_id: "replay-1",
      revision: 8,
    }),
    false,
  );
  assert.equal(replay.projection, null);
  assert.equal(replay.loading, true);
});

test("new Replay session converts prior success into loading fallback", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  replay.beginSession("replay-1");
  assert.equal(
    replay.acceptSnapshot(cloneReplaySnapshot(), {
      service_generation: live.projection.serviceGeneration,
      session_id: "replay-1",
      revision: 8,
    }),
    true,
  );
  const prior = replay.projection;
  replay.beginSession("replay-2", "op-2");
  assert.equal(replay.projection, null);
  assert.equal(replay.loadingFallbackProjection, prior);
  assert.equal(replay.hasAuthoritativeProjection, false);
});

test("exitMode returns latest Live through the selector once", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  live.applyEvent(liveMarketUpdate);
  replay.beginSession("replay-1");
  const ended = replay.exitMode();
  assert.equal(ended, "replay-1");
  assert.equal(
    activeFrom(WorkbenchMode.LIVE, live, replay),
    live.projection,
  );
  assert.equal(replay.loadingFallbackProjection, null);
});

test("clearForGenerationChange drops fallback so it cannot stay visible", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  assert.ok(replay.loadingFallbackProjection);
  replay.clearForGenerationChange();
  assert.equal(replay.loadingFallbackProjection, null);
  assert.equal(activeFrom(WorkbenchMode.REPLAY, live, replay), null);
});

test("applySessionStatus patches authoritative Replay facts in place", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  replay.beginSession("replay-1");
  const snapshot = cloneReplaySnapshot();
  replay.acceptSnapshot(snapshot, {
    service_generation: live.projection.serviceGeneration,
    session_id: "replay-1",
    revision: 8,
  });
  assert.equal(
    replay.applySessionStatus({
      state: "playing",
      playback_speed: 10,
      revision: 9,
    }),
    true,
  );
  const facts = replayFactsFromSnapshot(replay.projection.snapshot);
  assert.equal(facts?.state, "playing");
  assert.equal(facts?.playbackSpeed, 10);
  assert.equal(replay.projection.revision, 9);
});

test("applySessionStatus rejects stale or equal revision", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  replay.beginSession("replay-1");
  const snapshot = cloneReplaySnapshot();
  replay.acceptSnapshot(snapshot, {
    service_generation: live.projection.serviceGeneration,
    session_id: "replay-1",
    revision: 8,
  });
  assert.equal(
    replay.applySessionStatus({
      state: "paused",
      playback_speed: 1,
      revision: 7,
    }),
    false,
  );
  assert.equal(replay.projection.revision, 8);
  assert.equal(replay.projection.snapshot.session.revision, 8);
  assert.equal(
    replay.applySessionStatus({
      state: "paused",
      playback_speed: 1,
      revision: 8,
    }),
    false,
  );
  assert.equal(replay.projection.revision, 8);
});

test("applySessionStatus rejects missing or non-integer revision", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  replay.beginSession("replay-1");
  replay.acceptSnapshot(cloneReplaySnapshot(), {
    service_generation: live.projection.serviceGeneration,
    session_id: "replay-1",
    revision: 8,
  });
  assert.equal(
    replay.applySessionStatus({ state: "playing", playback_speed: 2 }),
    false,
  );
  assert.equal(
    replay.applySessionStatus({
      state: "playing",
      playback_speed: 2,
      revision: null,
    }),
    false,
  );
  assert.equal(
    replay.applySessionStatus({
      state: "playing",
      playback_speed: 2,
      revision: 9.5,
    }),
    false,
  );
  assert.equal(
    replay.applySessionStatus({
      state: "playing",
      playback_speed: 2,
      revision: Number.NaN,
    }),
    false,
  );
  assert.equal(replay.projection.revision, 8);
  assert.equal(replay.projection.snapshot.session.state, "paused");
});

test("Replay load settles only after acceptSnapshot succeeds", () => {
  const live = new LiveProjectionController(liveBaseline());
  const replay = new ReplaySessionController();
  replay.setServiceGeneration(live.projection.serviceGeneration);
  replay.enterMode(live.projection);
  replay.beginSession("replay-1", "load-op-1");
  assert.equal(replay.loading, true);
  assert.equal(replay.projection, null);
  assert.equal(replay.matchesLoadOperation("load-op-1"), true);
  assert.equal(replay.loading, true);
  const snapshot = cloneReplaySnapshot();
  assert.equal(
    replay.acceptSnapshot(snapshot, {
      service_generation: live.projection.serviceGeneration,
      session_id: "replay-1",
      revision: 8,
      operation_id: "load-op-1",
    }),
    true,
  );
  assert.equal(replay.loading, false);
  assert.equal(replay.loadOperationId, null);
  assert.ok(replay.projection);
  assert.equal(
    activeFrom(WorkbenchMode.REPLAY, live, replay),
    replay.projection,
  );
});
