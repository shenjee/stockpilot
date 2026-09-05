/**
 * App 层 React 集成测试：Session 切换后新 ChartGroup 首次 committed mount
 * 必须收到 initialViewport=null（Issue #170 / PR #169 render-phase 清空）。
 *
 * 只记录 useEffect 里的 committed mount，不记录会被 React 丢弃的 render-phase 输出。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { buildSafeBridge } from "../electron/safe-bridge.mjs";
import {
  mountsFor,
  reportManualViewport,
  resetCommittedChartMounts,
  type ChartKind,
} from "./helpers/mock-chart-group";

vi.mock("../renderer/src/charts/ChartGroup", () =>
  import("./helpers/mock-chart-group"),
);

const testDir = dirname(fileURLToPath(import.meta.url));
const workbenchFixture = JSON.parse(
  readFileSync(
    resolve(testDir, "../contracts/fixtures/workbench-flow-v2.json"),
    "utf8",
  ),
);

const SECURITY = Object.freeze({
  symbol: "sh.600000",
  code: "600000",
  market: "sh",
  name: "浦发银行",
  instrument_type: "stock",
});

const PREFERENCES = Object.freeze({
  last_symbol: SECURITY.symbol,
  layout: { show_intraday: true },
  layers: {
    ma5: false,
    ma10: false,
    ma20: false,
    ma30: false,
    ma60: false,
    strokes: true,
    pivot_zones: true,
  },
});

const LIVE_MANUAL = Object.freeze({
  fiveMinute: { range: { from: 10, to: 40 }, followState: "manual" as const },
  thirtyMinute: { range: { from: 4, to: 18 }, followState: "manual" as const },
});
const REPLAY_A_MANUAL = Object.freeze({
  fiveMinute: { range: { from: 20, to: 50 }, followState: "manual" as const },
  thirtyMinute: { range: { from: 6, to: 22 }, followState: "manual" as const },
});
const LIVE_AFTER_REPLAY_MANUAL = Object.freeze({
  fiveMinute: { range: { from: 14, to: 44 }, followState: "manual" as const },
});
const GENERATION_B_SESSION = "live-after-generation";
const SAME_SESSION_MANUAL = Object.freeze({
  thirtyMinute: { range: { from: 2, to: 12 }, followState: "manual" as const },
  intraday: { range: { from: 0, to: 80 }, followState: "manual" as const },
});

function clone(value) {
  return structuredClone(value);
}

function withThirtyMinuteBars(snapshot) {
  const next = clone(snapshot);
  const bar = {
    timestamp: "2026-07-22 10:00:00",
    open: 10.0,
    high: 10.12,
    low: 9.98,
    close: 10.08,
    volume: 51000.0,
    amount: 513200.0,
    closed: true,
  };
  next.market.bars_30m = [bar];
  const point = { timestamp: bar.timestamp, value: null };
  next.indicators.thirty_minute = {
    ma: {
      ma5: [point],
      ma10: [point],
      ma20: [point],
      ma30: [point],
      ma60: [point],
    },
    boll: {
      period: 20,
      stddev: 2.0,
      upper: [point],
      middle: [point],
      lower: [point],
    },
    volume: {
      values: [{ timestamp: bar.timestamp, value: 51000.0 }],
      ma5: [point],
      ma10: [point],
    },
    macd: {
      fast_period: 12,
      slow_period: 26,
      signal_period: 9,
      dif: [point],
      dea: [point],
      histogram: [point],
    },
  };
  return next;
}

function liveSnapshot() {
  return withThirtyMinuteBars(workbenchFixture.initial_snapshot_event.payload);
}

function replaySnapshot(sessionId, tradeDate = "2026-07-22") {
  const snapshot = withThirtyMinuteBars(liveSnapshot());
  snapshot.session = {
    session_id: sessionId,
    session_type: "replay",
    symbol: SECURITY.symbol,
    trade_date: tradeDate,
    state: "paused",
    revision: 1,
  };
  snapshot.replay = {
    granularity: "one_minute",
    current_time: "2026-07-22 10:23:00",
    next_bar_time: "2026-07-22 10:24:00",
    start_time: "2026-07-22 09:30:00",
    end_time: "2026-07-22 15:00:00",
    playing: false,
    playback_speed: 1,
    step_seconds: 60,
  };
  return snapshot;
}

function appResponse(request, data) {
  return {
    schema_version: "t0_app_v2",
    request_id: request?.request_id ?? "app-viewport-test",
    accepted: true,
    operation_id: null,
    data,
    error: null,
  };
}

function createAppBridge() {
  const listeners = new Map();
  let livePayload = liveSnapshot();
  let replaySeq = 0;
  const replaySessions = [];
  const serviceStatus = {
    state: "connected",
    service_generation: workbenchFixture.service_generation,
    message: "App viewport test bridge",
  };

  function emit(channel, payload) {
    for (const listener of listeners.get(channel) ?? []) listener(clone(payload));
  }

  const bridge = buildSafeBridge({
    invoke: async (command, request) => {
      if (command === "get_service_status") return clone(serviceStatus);
      if (command === "get_live_snapshot") {
        return appResponse(request, clone(livePayload));
      }
      if (command === "get_preferences") {
        return appResponse(request, {
          preferences: PREFERENCES,
          restored_security: SECURITY,
          startup_restore: {
            status: "restored",
            session_id: workbenchFixture.session_id,
          },
        });
      }
      if (command === "save_preferences" || command === "save_last_symbol") {
        return appResponse(request, { preferences: PREFERENCES });
      }
      if (command === "search_securities") {
        return appResponse(request, { securities: [SECURITY] });
      }
      if (command === "resolve_security_identity" || command === "select_security") {
        return appResponse(request, {
          security: SECURITY,
          session_id: livePayload.session.session_id,
        });
      }
      if (command === "list_trades") {
        return { ...appResponse(request, null), operation_id: "list-trades-1" };
      }
      if (command === "list_fee_plans") {
        return appResponse(request, { fee_plans: [] });
      }
      if (command === "begin_replay") {
        replaySeq += 1;
        const sessionId = `replay-session-${replaySeq}`;
        const operationId = `operation-begin_replay-${replaySeq}`;
        replaySessions.push({ sessionId, operationId });
        return {
          schema_version: "t0_replay_v2",
          request_id: request.request_id,
          service_generation: serviceStatus.service_generation,
          session_id: sessionId,
          operation_id: operationId,
        };
      }
      if (command === "end_replay") {
        return {
          schema_version: "t0_replay_v2",
          request_id: request.request_id,
          service_generation: serviceStatus.service_generation,
          session_id: request.session_id,
        };
      }
      return appResponse(request, null);
    },
    subscribe: (channel, listener) => {
      const channelListeners = listeners.get(channel) ?? new Set();
      channelListeners.add(listener);
      listeners.set(channel, channelListeners);
      return () => channelListeners.delete(listener);
    },
  });

  return {
    bridge,
    latestReplay() {
      return replaySessions.at(-1) ?? null;
    },
    emitReplaySnapshot() {
      const latest = replaySessions.at(-1);
      if (!latest) throw new Error("begin_replay has not created a session");
      emit("replay_event", {
        schema_version: "t0_replay_v2",
        service_generation: serviceStatus.service_generation,
        session_id: latest.sessionId,
        revision: 1,
        operation_id: latest.operationId,
        event_type: "workbench_snapshot",
        payload: replaySnapshot(latest.sessionId),
      });
    },
    emitGenerationBump() {
      serviceStatus.service_generation += 1;
      emit("service_status", clone(serviceStatus));
    },
    emitLiveWorkbenchSnapshot(sessionId = GENERATION_B_SESSION) {
      livePayload = liveSnapshot();
      livePayload.session = {
        ...livePayload.session,
        session_id: sessionId,
        revision: 1,
      };
      emit("app_event", {
        schema_version: "t0_app_v2",
        service_generation: serviceStatus.service_generation,
        session_id: sessionId,
        revision: 1,
        event_type: "workbench_snapshot",
        payload: clone(livePayload),
      });
    },
  };
}

function panChart(kind: ChartKind, snapshot: (typeof LIVE_MANUAL)["fiveMinute"]) {
  act(() => {
    reportManualViewport(kind, snapshot);
  });
}

function lastMount(kind: ChartKind) {
  const mounts = mountsFor(kind);
  expect(mounts.length, `expected a committed ${kind} mount`).toBeGreaterThan(0);
  return mounts[mounts.length - 1];
}

async function waitForChart(kind: ChartKind) {
  await waitFor(() => {
    expect(screen.getByTestId(`mock-chart-${kind}`)).toBeTruthy();
  });
}

async function bootLiveWorkbench() {
  const harness = createAppBridge();
  window.stockpilot = harness.bridge;
  const view = render(await loadApp());
  await waitForChart("five_minute");
  await waitForChart("one_minute");
  return { harness, view };
}

async function loadApp() {
  const mod = await import("../renderer/src/App");
  const App = mod.App;
  return createElement(App);
}

async function showThirtyMinute() {
  fireEvent.click(screen.getByTestId("secondary-thirty-minute"));
  await waitForChart("thirty_minute");
}

async function showIntraday() {
  fireEvent.click(screen.getByTestId("secondary-intraday"));
  await waitForChart("one_minute");
}

async function enterReplayAndCommitSnapshot(harness) {
  fireEvent.click(screen.getByTestId("mode-replay"));
  const date = screen.getByLabelText("回放日期") as HTMLInputElement;
  fireEvent.change(date, { target: { value: "2026-07-22" } });
  fireEvent.click(screen.getByRole("button", { name: "开始回放" }));
  await waitFor(() => {
    expect(harness.latestReplay()).not.toBeNull();
  });
  harness.emitReplaySnapshot();
  await waitFor(() => {
    expect(screen.getByTestId("replay-controls").className).toContain(
      "replay-active",
    );
  });
}

async function exitReplayToLive() {
  fireEvent.click(screen.getByTestId("mode-live"));
  await waitFor(() => {
    expect(screen.getByTestId("mode-live").getAttribute("aria-pressed")).toBe(
      "true",
    );
  });
  await waitForChart("five_minute");
}

describe("App session-switch initialViewport commits", () => {
  beforeEach(() => {
    resetCommittedChartMounts();
    document.body.innerHTML = "";
  });

  afterEach(() => {
    cleanup();
    resetCommittedChartMounts();
    // @ts-expect-error test teardown
    delete window.stockpilot;
  });

  it("keeps same-session 30m and 分时 viewports when switching secondary charts", async () => {
    await bootLiveWorkbench();
    await showThirtyMinute();
    panChart("thirty_minute", SAME_SESSION_MANUAL.thirtyMinute);

    await showIntraday();
    panChart("one_minute", SAME_SESSION_MANUAL.intraday);

    const thirtyBefore = mountsFor("thirty_minute").length;
    await showThirtyMinute();
    expect(mountsFor("thirty_minute").length).toBe(thirtyBefore + 1);
    expect(lastMount("thirty_minute").initialViewport).toEqual(
      SAME_SESSION_MANUAL.thirtyMinute,
    );

    const layoutSwitcher = screen.getByTestId("layout-switcher");
    fireEvent.click(layoutSwitcher);
    fireEvent.click(layoutSwitcher);
    await waitForChart("thirty_minute");
    expect(lastMount("thirty_minute").initialViewport).toEqual(
      SAME_SESSION_MANUAL.thirtyMinute,
    );

    const intraBefore = mountsFor("one_minute").length;
    await showIntraday();
    expect(mountsFor("one_minute").length).toBe(intraBefore + 1);
    expect(lastMount("one_minute").initialViewport).toEqual(
      SAME_SESSION_MANUAL.intraday,
    );
  });

  it("Live → Replay commits new 5m/30m mounts with null viewport after the first authoritative snapshot", async () => {
    const { harness } = await bootLiveWorkbench();
    await showThirtyMinute();
    panChart("five_minute", LIVE_MANUAL.fiveMinute);
    panChart("thirty_minute", LIVE_MANUAL.thirtyMinute);

    fireEvent.click(screen.getByTestId("mode-replay"));
    await waitFor(() => {
      expect(screen.getByTestId("replay-controls")).toBeTruthy();
      expect(screen.getByTestId("replay-controls").className).toContain(
        "replay-setup",
      );
    });
    const fiveBeforeSnapshot = mountsFor("five_minute").length;
    const thirtyBeforeSnapshot = mountsFor("thirty_minute").length;

    const date = screen.getByLabelText("回放日期") as HTMLInputElement;
    fireEvent.change(date, { target: { value: "2026-07-22" } });
    fireEvent.click(screen.getByRole("button", { name: "开始回放" }));
    await waitFor(() => expect(harness.latestReplay()).not.toBeNull());
    expect(mountsFor("five_minute").length).toBe(fiveBeforeSnapshot);

    harness.emitReplaySnapshot();
    await waitFor(() => {
      expect(mountsFor("five_minute").length).toBeGreaterThan(fiveBeforeSnapshot);
      expect(mountsFor("thirty_minute").length).toBeGreaterThan(
        thirtyBeforeSnapshot,
      );
    });
    expect(lastMount("five_minute").initialViewport).toBeNull();
    expect(lastMount("thirty_minute").initialViewport).toBeNull();
  });

  it("Replay A → Live → Replay B remounts hidden 30m with null, not Session A range", async () => {
    const { harness } = await bootLiveWorkbench();
    await showThirtyMinute();
    panChart("five_minute", LIVE_MANUAL.fiveMinute);
    panChart("thirty_minute", LIVE_MANUAL.thirtyMinute);

    await enterReplayAndCommitSnapshot(harness);
    await waitForChart("thirty_minute");
    panChart("five_minute", REPLAY_A_MANUAL.fiveMinute);
    panChart("thirty_minute", REPLAY_A_MANUAL.thirtyMinute);
    await showIntraday();

    await exitReplayToLive();
    await waitForChart("five_minute");
    expect(lastMount("five_minute").initialViewport).toBeNull();
    panChart("five_minute", LIVE_AFTER_REPLAY_MANUAL.fiveMinute);

    await enterReplayAndCommitSnapshot(harness);
    expect(screen.queryByTestId("mock-chart-thirty_minute")).toBeNull();

    const thirtyBeforeShow = mountsFor("thirty_minute").length;
    await showThirtyMinute();
    expect(mountsFor("thirty_minute").length).toBe(thirtyBeforeShow + 1);
    expect(lastMount("thirty_minute").initialViewport).toBeNull();
    expect(lastMount("five_minute").initialViewport).toBeNull();
  });

  it("Replay → Live commits new 5m/30m mounts with null viewport", async () => {
    const { harness } = await bootLiveWorkbench();
    await showThirtyMinute();
    panChart("five_minute", LIVE_MANUAL.fiveMinute);
    panChart("thirty_minute", LIVE_MANUAL.thirtyMinute);

    await enterReplayAndCommitSnapshot(harness);
    await waitForChart("thirty_minute");
    panChart("five_minute", REPLAY_A_MANUAL.fiveMinute);
    panChart("thirty_minute", REPLAY_A_MANUAL.thirtyMinute);

    const fiveBeforeExit = mountsFor("five_minute").length;
    const thirtyBeforeExit = mountsFor("thirty_minute").length;
    await exitReplayToLive();
    await waitFor(() => {
      expect(mountsFor("five_minute").length).toBeGreaterThan(fiveBeforeExit);
      expect(mountsFor("thirty_minute").length).toBeGreaterThan(thirtyBeforeExit);
    });
    expect(lastMount("five_minute").initialViewport).toBeNull();
    expect(lastMount("thirty_minute").initialViewport).toBeNull();
  });

  it("generation restart A → null → B does not inherit Session A viewport", async () => {
    const { harness } = await bootLiveWorkbench();
    await showThirtyMinute();
    panChart("five_minute", LIVE_MANUAL.fiveMinute);
    panChart("thirty_minute", LIVE_MANUAL.thirtyMinute);

    const fiveBeforeBump = mountsFor("five_minute").length;
    act(() => {
      harness.emitGenerationBump();
    });
    await waitFor(() => {
      expect(mountsFor("five_minute").length).toBeGreaterThan(fiveBeforeBump);
    });

    const fiveBeforeB = mountsFor("five_minute").length;
    const thirtyBeforeB = mountsFor("thirty_minute").length;
    act(() => {
      harness.emitLiveWorkbenchSnapshot(GENERATION_B_SESSION);
    });
    await waitFor(() => {
      expect(mountsFor("five_minute").length).toBeGreaterThan(fiveBeforeB);
      expect(mountsFor("thirty_minute").length).toBeGreaterThan(thirtyBeforeB);
    });
    expect(lastMount("five_minute").initialViewport).toBeNull();
    expect(lastMount("thirty_minute").initialViewport).toBeNull();
  });
});
