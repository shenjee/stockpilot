/**
 * Issue #202: 历史交易日图形取数失败的用户文案固定为
 * 「【历史行情】数据获取失败，请重试」，不再拼接后端英文 message。
 *
 * 覆盖 handleEnterDayChart 的两条失败路径：
 * - getHistoricalSnapshot 返回错误 envelope（applicationErrorFrom 命中）
 * - bridge invoke 抛异常（catch 路径）
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { buildSafeBridge } from "../electron/safe-bridge.mjs";
import { resetCommittedChartMounts } from "./helpers/mock-chart-group";

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

const HISTORICAL_FAILURE_MESSAGE = "【历史行情】数据获取失败，请重试";

// A historical trade whose executed_at date differs from the machine's real
// today, so handleEnterDayChart takes the historical-snapshot path.
const TRADE_RECORD = Object.freeze({
  trade_id: "t1",
  bucket_start: "2026-07-22 09:31:00",
  trade_scope: "real",
  symbol: SECURITY.symbol,
  side: "buy",
  executed_at: "2026-07-22 10:00:00",
  price: 10.0,
  quantity: 100,
  fee: null,
  note: "",
  fee_plan_id: null,
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

function appResponse(request, data) {
  return {
    schema_version: "t0_app_v2",
    request_id: request?.request_id ?? "app-history-test",
    accepted: true,
    operation_id: null,
    data,
    error: null,
  };
}

function historicalErrorResponse(request) {
  return {
    schema_version: "t0_app_v2",
    request_id: request?.request_id ?? "app-history-test",
    accepted: false,
    operation_id: null,
    data: null,
    error: {
      error_code: "service_unavailable",
      message: "historical snapshot failed: provider request failed",
      retryable: true,
      affected_capability: "historical_chart",
    },
  };
}

function createAppBridge({ historicalSnapshot }) {
  const listeners = new Map();
  const livePayload = liveSnapshot();
  const serviceStatus = {
    state: "connected",
    service_generation: workbenchFixture.service_generation,
    message: "App historical notice test bridge",
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
      if (command === "list_trade_history") {
        return appResponse(request, {
          trade_revision: 1,
          trades: [clone(TRADE_RECORD)],
        });
      }
      if (command === "list_fee_plans") {
        return appResponse(request, { fee_plans: [] });
      }
      if (command === "get_historical_snapshot") {
        return historicalSnapshot(request);
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

  return { bridge, emit };
}

async function loadApp() {
  const mod = await import("../renderer/src/App");
  const App = mod.App;
  return createElement(App);
}

async function waitForChart(kind) {
  await waitFor(() => {
    expect(screen.getByTestId(`mock-chart-${kind}`)).toBeTruthy();
  });
}

async function bootLiveWorkbench(harness) {
  window.stockpilot = harness.bridge;
  render(await loadApp());
  await waitForChart("five_minute");
  await waitForChart("one_minute");
}

async function enterHistoricalDayChart() {
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "历史交易记录" })).toBeTruthy();
  });
  fireEvent.click(screen.getByRole("button", { name: "历史交易记录" }));
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "进入当天图形" })).toBeTruthy();
  });
  fireEvent.click(screen.getByRole("button", { name: "进入当天图形" }));
}

describe("historical day-chart fetch failure notice", () => {
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

  it("shows the fixed historical copy when the snapshot response carries an error", async () => {
    const harness = createAppBridge({
      historicalSnapshot: (request) => historicalErrorResponse(request),
    });
    await bootLiveWorkbench(harness);

    await enterHistoricalDayChart();

    await waitFor(() => {
      expect(screen.getByText(HISTORICAL_FAILURE_MESSAGE)).toBeTruthy();
    });
    expect(
      screen.queryByText(/historical snapshot failed|provider request failed/),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "知道了" })).toBeTruthy();
  });

  it("shows the fixed historical copy when the bridge invoke throws", async () => {
    const harness = createAppBridge({
      historicalSnapshot: () => {
        throw new Error("bridge invoke crashed: provider request failed");
      },
    });
    await bootLiveWorkbench(harness);

    await enterHistoricalDayChart();

    await waitFor(() => {
      expect(screen.getByText(HISTORICAL_FAILURE_MESSAGE)).toBeTruthy();
    });
    expect(
      screen.queryByText(/bridge invoke crashed|provider request failed/),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "知道了" })).toBeTruthy();
  });
});
