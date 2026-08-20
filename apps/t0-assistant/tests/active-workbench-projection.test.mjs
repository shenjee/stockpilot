/**
 * #155 PR1 — ActiveWorkbenchProjection selector + ownership characterization.
 *
 * These tests pin the read-only selector and the Live / Replay / fallback
 * ownership protocol before controllers are extracted (PR2). The in-file
 * harness is not production code; it only drives the sequences App will later
 * express via LiveProjectionController and ReplaySessionController.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  captureLoadingFallbackProjection,
  hasAuthoritativeReplayProjection,
  selectActiveWorkbenchProjection,
} from "../renderer/src/charts/active-workbench-projection.mjs";
import {
  ChartGroupKind,
  createChartGroupModel,
} from "../renderer/src/charts/chart-model.mjs";
import {
  applyLiveChartEvent,
  applyWorkbenchSnapshot,
  createChartProjection,
} from "../renderer/src/charts/chart-projection.mjs";
import {
  deriveReplayControls,
  replayFactsFromSnapshot,
} from "../renderer/src/replay-controls.mjs";
import { WorkbenchMode } from "../renderer/src/workbench-layout.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const [workbenchFixture, replayFixture, chartGroupsFixture] = await Promise.all([
  readFile(
    resolve(testDir, "../contracts/fixtures/workbench-flow-v2.json"),
    "utf8",
  ).then(JSON.parse),
  readFile(
    resolve(testDir, "../contracts/fixtures/replay-speed-v2.json"),
    "utf8",
  ).then(JSON.parse),
  readFile(
    resolve(testDir, "../contracts/fixtures/chart-groups-v1.json"),
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

function replayProjectionFromSnapshot(snapshot, identity = {}) {
  return createChartProjection(snapshot, {
    service_generation:
      identity.service_generation ?? liveBaseline().serviceGeneration,
    session_id: identity.session_id ?? snapshot.session?.session_id,
    revision: identity.revision ?? snapshot.session?.revision,
  });
}

/**
 * Characterization harness for active-projection ownership (#155).
 * Mirrors the decided controller boundaries without React.
 */
function createActiveProjectionHarness(initialLive) {
  let mode = WorkbenchMode.LIVE;
  let live = initialLive;
  let replay = null;
  let fallback = null;
  let activeReplaySessionId = null;
  let serviceGeneration = initialLive.serviceGeneration;

  function active() {
    return selectActiveWorkbenchProjection({
      mode,
      liveProjection: live,
      replayProjection: replay,
      loadingFallbackProjection: fallback,
    });
  }

  return {
    active,
    sources: () => ({ mode, live, replay, fallback, activeReplaySessionId }),
    enterReplayMode() {
      // Capture the foreground projection at mode entry — not beginReplay.
      const foreground =
        mode === WorkbenchMode.REPLAY ? (replay ?? fallback) : live;
      fallback = captureLoadingFallbackProjection(foreground);
      mode = WorkbenchMode.REPLAY;
    },
    beginReplaySession(sessionId) {
      if (mode !== WorkbenchMode.REPLAY) {
        throw new Error("beginReplaySession requires Replay mode");
      }
      if (replay != null) {
        // Prior successful Replay becomes the next loading fallback.
        fallback = captureLoadingFallbackProjection(replay);
        replay = null;
      }
      activeReplaySessionId = sessionId;
    },
    applyLiveEvent(event) {
      live = applyLiveChartEvent(live, event);
    },
    acceptReplaySnapshot(snapshot, identity = {}) {
      if (mode !== WorkbenchMode.REPLAY) return false;
      const sessionId = identity.session_id ?? snapshot.session?.session_id;
      const generation =
        identity.service_generation ?? serviceGeneration;
      const revision = identity.revision ?? snapshot.session?.revision;
      if (
        generation !== serviceGeneration ||
        sessionId !== activeReplaySessionId ||
        !Number.isInteger(revision)
      ) {
        return false;
      }
      const facts = replayFactsFromSnapshot(snapshot);
      if (!facts || facts.sessionId !== activeReplaySessionId) {
        return false;
      }
      if (replay == null) {
        replay = createChartProjection(snapshot, {
          service_generation: generation,
          session_id: sessionId,
          revision,
        });
        return true;
      }
      const next = applyWorkbenchSnapshot(replay, snapshot, {
        service_generation: generation,
        session_id: sessionId,
        revision,
      });
      if (next === replay) {
        return false;
      }
      replay = next;
      return true;
    },
    exitReplay() {
      mode = WorkbenchMode.LIVE;
      replay = null;
      fallback = null;
      activeReplaySessionId = null;
    },
    bumpServiceGeneration(nextGeneration) {
      serviceGeneration = nextGeneration;
      // Old-generation fallback must not remain displayable.
      if (mode === WorkbenchMode.REPLAY) {
        fallback = null;
        replay = null;
        activeReplaySessionId = null;
      }
      live = {
        ...live,
        serviceGeneration: nextGeneration,
        sessionId: live.sessionId,
        revision: null,
        rebaselineRequired: false,
      };
    },
  };
}

test("selector returns live projection in Live mode", () => {
  const live = liveBaseline();
  const replay = replayProjectionFromSnapshot(cloneReplaySnapshot());
  const fallback = liveBaseline();

  const active = selectActiveWorkbenchProjection({
    mode: WorkbenchMode.LIVE,
    liveProjection: live,
    replayProjection: replay,
    loadingFallbackProjection: fallback,
  });

  assert.equal(active, live);
});

test("selector prefers authoritative Replay over fallback", () => {
  const live = liveBaseline();
  const replay = replayProjectionFromSnapshot(cloneReplaySnapshot());
  const fallback = liveBaseline();

  const active = selectActiveWorkbenchProjection({
    mode: WorkbenchMode.REPLAY,
    liveProjection: live,
    replayProjection: replay,
    loadingFallbackProjection: fallback,
  });

  assert.equal(active, replay);
  assert.equal(hasAuthoritativeReplayProjection(replay), true);
});

test("selector uses loading fallback when Replay projection is absent", () => {
  const live = liveBaseline();
  const fallback = captureLoadingFallbackProjection(live);

  const active = selectActiveWorkbenchProjection({
    mode: WorkbenchMode.REPLAY,
    liveProjection: live,
    replayProjection: null,
    loadingFallbackProjection: fallback,
  });

  assert.equal(active, fallback);
  assert.equal(hasAuthoritativeReplayProjection(null), false);
});

test("captureLoadingFallbackProjection keeps object identity without cloning", () => {
  const live = liveBaseline();
  const fallback = captureLoadingFallbackProjection(live);
  assert.equal(fallback, live);
  assert.equal(captureLoadingFallbackProjection(null), null);
  assert.equal(captureLoadingFallbackProjection(undefined), null);
});

test("enter Replay freezes foreground; Live increments do not move chart or fallback", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  const beforeBars =
    harness.active().snapshot.market.bars_1m.map((bar) => bar.timestamp);

  harness.enterReplayMode();
  const fallback = harness.sources().fallback;
  const activeAtEntry = harness.active();
  assert.equal(activeAtEntry, fallback);
  assert.equal(fallback, harness.sources().live);

  harness.applyLiveEvent(liveMarketUpdate);
  harness.applyLiveEvent(liveIndicatorUpdate);

  assert.equal(harness.active(), fallback);
  assert.notEqual(harness.sources().live, fallback);
  assert.deepEqual(
    harness.active().snapshot.market.bars_1m.map((bar) => bar.timestamp),
    beforeBars,
  );
  assert.deepEqual(
    harness.sources().live.snapshot.market.bars_1m.map((bar) => bar.timestamp),
    ["2026-07-22 09:31:00", "2026-07-22 09:32:00"],
  );
  assert.equal(replayFactsFromSnapshot(harness.active().snapshot), null);
  assert.deepEqual(deriveReplayControls(null).active, false);
});

test("enter Replay captures foreground at mode switch, not a later Live head", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  harness.applyLiveEvent(liveMarketUpdate);
  const foregroundAtSwitch = harness.active();

  harness.enterReplayMode();
  harness.applyLiveEvent(liveIndicatorUpdate);

  assert.equal(harness.sources().fallback, foregroundAtSwitch);
  assert.equal(harness.active(), foregroundAtSwitch);
  assert.equal(harness.sources().live.revision, 3);
  assert.equal(harness.active().revision, 2);
});

test("matched Replay snapshot atomically replaces fallback for controls and chart", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  harness.enterReplayMode();
  harness.beginReplaySession("replay-1");

  const snapshot = cloneReplaySnapshot();
  const accepted = harness.acceptReplaySnapshot(snapshot, {
    service_generation: liveBaseline().serviceGeneration,
    session_id: "replay-1",
    revision: snapshot.session.revision,
  });
  assert.equal(accepted, true);

  const active = harness.active();
  assert.equal(active, harness.sources().replay);
  assert.notEqual(active, harness.sources().fallback);

  const facts = replayFactsFromSnapshot(active.snapshot);
  assert.equal(facts?.sessionId, "replay-1");
  assert.equal(facts?.currentTime, snapshot.replay.current_time);
  assert.equal(deriveReplayControls(facts).active, true);
  assert.equal(active.snapshot, harness.sources().replay.snapshot);
});

test("generation or session mismatch cannot replace loading fallback", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  harness.enterReplayMode();
  harness.beginReplaySession("replay-1");
  const fallback = harness.sources().fallback;

  const wrongSession = cloneReplaySnapshot({
    session: { session_id: "replay-other" },
  });
  assert.equal(
    harness.acceptReplaySnapshot(wrongSession, {
      service_generation: liveBaseline().serviceGeneration,
      session_id: "replay-other",
      revision: 8,
    }),
    false,
  );
  assert.equal(harness.active(), fallback);

  const wrongGeneration = cloneReplaySnapshot();
  assert.equal(
    harness.acceptReplaySnapshot(wrongGeneration, {
      service_generation: liveBaseline().serviceGeneration + 1,
      session_id: "replay-1",
      revision: 8,
    }),
    false,
  );
  assert.equal(harness.active(), fallback);
  assert.equal(harness.sources().replay, null);
});

test("stale revision cannot advance an established Replay projection", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  harness.enterReplayMode();
  harness.beginReplaySession("replay-1");

  const first = cloneReplaySnapshot({
    session: { revision: 8 },
    replay: { current_time: "2026-07-01 10:23:00" },
  });
  assert.equal(
    harness.acceptReplaySnapshot(first, {
      service_generation: liveBaseline().serviceGeneration,
      session_id: "replay-1",
      revision: 8,
    }),
    true,
  );
  const ready = harness.sources().replay;

  const stale = cloneReplaySnapshot({
    session: { revision: 7 },
    replay: { current_time: "2026-07-01 10:20:00" },
  });
  assert.equal(
    harness.acceptReplaySnapshot(stale, {
      service_generation: liveBaseline().serviceGeneration,
      session_id: "replay-1",
      revision: 7,
    }),
    false,
  );
  assert.equal(harness.active(), ready);
  assert.equal(
    harness.active().snapshot.replay.current_time,
    "2026-07-01 10:23:00",
  );
});

test("new Replay session converts prior success into loading fallback", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  harness.enterReplayMode();
  harness.beginReplaySession("replay-1");
  const first = cloneReplaySnapshot({ session: { session_id: "replay-1" } });
  assert.equal(
    harness.acceptReplaySnapshot(first, {
      service_generation: liveBaseline().serviceGeneration,
      session_id: "replay-1",
      revision: 8,
    }),
    true,
  );
  const priorSuccess = harness.sources().replay;

  harness.beginReplaySession("replay-2");
  assert.equal(harness.sources().replay, null);
  assert.equal(harness.sources().fallback, priorSuccess);
  assert.equal(harness.active(), priorSuccess);
  // Loading: chart keeps prior success as fallback, but controls have no
  // authoritative Replay facts until the new session's first snapshot.
  assert.equal(hasAuthoritativeReplayProjection(harness.sources().replay), false);
  assert.equal(deriveReplayControls(null).active, false);

  const second = cloneReplaySnapshot({
    session: { session_id: "replay-2", revision: 1 },
    replay: { current_time: "2026-07-01 09:31:00" },
  });
  assert.equal(
    harness.acceptReplaySnapshot(second, {
      service_generation: liveBaseline().serviceGeneration,
      session_id: "replay-2",
      revision: 1,
    }),
    true,
  );
  assert.equal(harness.active(), harness.sources().replay);
  assert.equal(
    replayFactsFromSnapshot(harness.active().snapshot)?.sessionId,
    "replay-2",
  );
});

test("exit Replay selects the latest background Live projection once", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  harness.enterReplayMode();
  harness.applyLiveEvent(liveMarketUpdate);
  harness.applyLiveEvent(liveIndicatorUpdate);
  const latestLive = harness.sources().live;

  harness.exitReplay();
  assert.equal(harness.active(), latestLive);
  assert.equal(harness.sources().fallback, null);
  assert.equal(harness.sources().replay, null);
  assert.equal(harness.active().revision, 3);
});

test("service generation change clears Replay fallback so it cannot stay visible", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  harness.enterReplayMode();
  const fallback = harness.sources().fallback;
  assert.equal(harness.active(), fallback);

  harness.bumpServiceGeneration(liveBaseline().serviceGeneration + 1);
  assert.equal(harness.sources().fallback, null);
  assert.equal(harness.sources().replay, null);
  assert.equal(harness.active(), null);
});

test("Live revision gates stay within one generation/session; old session cannot write back", () => {
  let live = liveBaseline();
  live = applyLiveChartEvent(live, liveMarketUpdate);
  assert.equal(live.revision, 2);

  const staleRevision = {
    ...liveMarketUpdate,
    revision: 2,
  };
  assert.equal(applyLiveChartEvent(live, staleRevision), live);

  const wrongSession = {
    ...liveIndicatorUpdate,
    session_id: "other-live-session",
    revision: 3,
  };
  assert.equal(applyLiveChartEvent(live, wrongSession), live);

  const nextSession = createChartProjection(live.snapshot, {
    service_generation: live.serviceGeneration,
    session_id: "live-session-2",
    revision: 1,
  });
  const oldSessionEvent = {
    ...liveIndicatorUpdate,
    session_id: "live-fixture-1",
    revision: 3,
  };
  assert.equal(applyLiveChartEvent(nextSession, oldSessionEvent), nextSession);

  // Monotonicity is per session: a new session may restart at revision 1.
  assert.equal(nextSession.revision, 1);
  assert.ok(live.revision > nextSession.revision);
});

test("active Replay projection feeds createChartGroupModel without Replay trimming", () => {
  const harness = createActiveProjectionHarness(liveBaseline());
  harness.enterReplayMode();
  harness.beginReplaySession("replay-chart");

  // Use chart-groups fixture body with Replay session/replay facts so controls
  // and the chart model share one projection identity.
  const snapshot = {
    ...structuredClone(chartGroupsFixture),
    session: {
      session_id: "replay-chart",
      session_type: "replay",
      symbol: "sh.600000",
      trade_date: "2026-07-22",
      state: "paused",
      revision: 4,
    },
    replay: {
      granularity: "five_minute",
      current_time: "2026-07-22 10:10:00",
      next_bar_time: null,
      start_time: "2026-07-22 09:35:00",
      end_time: "2026-07-22 10:10:00",
      playing: false,
      playback_speed: 1,
      step_seconds: 300,
    },
  };

  assert.equal(
    harness.acceptReplaySnapshot(snapshot, {
      service_generation: liveBaseline().serviceGeneration,
      session_id: "replay-chart",
      revision: 4,
    }),
    true,
  );

  const active = harness.active();
  assert.equal(active, harness.sources().replay);
  const facts = replayFactsFromSnapshot(active.snapshot);
  assert.equal(facts?.sessionId, "replay-chart");

  const model = createChartGroupModel(
    active.snapshot,
    ChartGroupKind.FIVE_MINUTE,
  );
  assert.equal(model.timestamps.at(-1), "2026-07-22 10:10:00");
  assert.equal(
    active.snapshot.market.bars_5m.at(-1).timestamp,
    model.timestamps.at(-1),
  );
});
