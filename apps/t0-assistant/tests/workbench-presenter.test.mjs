import assert from "node:assert/strict";
import test from "node:test";

import {
  applicationErrorFrom,
  canHydratePreferences,
  createLatestRequestTracker,
  isCompleteWorkbenchSnapshot,
  latestDailyBars,
  liveOperationFailurePresentation,
  operationMatchesEnvelope,
  quoteRows,
  securitiesFromSearchResponse,
  standardSecurityFromResponse,
} from "../renderer/src/workbench-presenter.mjs";

test("security responses must contain a frozen standard identity", () => {
  const security = {
    symbol: "sh.510300",
    code: "510300",
    market: "sh",
    name: "沪深300ETF",
    security_type: "etf",
  };
  assert.deepEqual(standardSecurityFromResponse({ security }), security);
  assert.equal(
    standardSecurityFromResponse({
      security: { ...security, symbol: "510300" },
    }),
    null,
  );
});

test("operation failures only match the originating generation and session", () => {
  const operation = { serviceGeneration: 4, sessionId: "live-2" };
  assert.equal(
    operationMatchesEnvelope(operation, {
      service_generation: 4,
      session_id: "live-2",
    }),
    true,
  );
  assert.equal(
    operationMatchesEnvelope(operation, {
      service_generation: 3,
      session_id: "live-2",
    }),
    false,
  );
  assert.equal(
    operationMatchesEnvelope(operation, {
      service_generation: 4,
      session_id: "live-retired",
    }),
    false,
  );
});

test("preferences hydrate only after the event channel is connected", () => {
  assert.equal(
    canHydratePreferences({ state: "starting" }, false),
    false,
  );
  assert.equal(canHydratePreferences({ state: "ready" }, false), false);
  assert.equal(canHydratePreferences({ state: "connected" }, false), true);
  assert.equal(canHydratePreferences({ state: "connected" }, true), false);
});

test("Live failures become non-blocking while Replay owns the visible workbench", () => {
  const error = {
    error_code: "live_data_unavailable",
    message: "Live 行情加载失败，请重试",
    retryable: true,
    affected_capability: "live",
  };
  assert.deepEqual(liveOperationFailurePresentation("live", error), {
    blocking: true,
    error,
  });
  assert.deepEqual(liveOperationFailurePresentation("replay", error), {
    blocking: false,
    error: {
      ...error,
      message: "后台 Live 行情加载失败，请重试；当前回放不受影响",
    },
  });
});

test("a later search invalidates an older in-flight result", () => {
  const tracker = createLatestRequestTracker();
  const stale = tracker.begin();
  const current = tracker.begin();

  assert.equal(tracker.isCurrent(stale), false);
  assert.equal(tracker.isCurrent(current), true);
});

test("full snapshot validation rejects partial chart-only payloads", () => {
  const complete = {
    timezone: "Asia/Shanghai",
    session: {
      session_id: "live-1",
      symbol: "sh.600000",
      revision: 1,
    },
    market: {
      bars_1m: [],
      bars_5m: [],
      daily_bars: [],
      quote: null,
    },
    indicators: {
      five_minute: {},
      one_minute: {},
    },
    chan_analysis: {},
    warnings: [],
  };

  assert.equal(isCompleteWorkbenchSnapshot(complete), true);
  assert.equal(
    isCompleteWorkbenchSnapshot({
      ...complete,
      market: { bars_1m: [], bars_5m: [] },
    }),
    false,
  );
  assert.equal(
    isCompleteWorkbenchSnapshot({ ...complete, chan_analysis: undefined }),
    false,
  );
  assert.equal(
    isCompleteWorkbenchSnapshot({ ...complete, warnings: undefined }),
    false,
  );
});

test("fuzzy search responses retain all valid standard identities", () => {
  const securities = [
    {
      symbol: "sh.600000",
      code: "600000",
      market: "sh",
      name: "浦发银行",
      security_type: "a_share",
    },
    {
      symbol: "sz.000001",
      code: "000001",
      market: "sz",
      name: "平安银行",
      security_type: "a_share",
    },
  ];
  assert.deepEqual(
    securitiesFromSearchResponse({ data: { securities } }),
    securities,
  );
});

test("the quote sidebar keeps every field and renders missing values in place", () => {
  const rows = quoteRows({
    timestamp: "2026-07-22 09:35:03",
    latest_price: 10.08,
    change_percent: 0.8,
    open: 10,
    high: 10.12,
    low: 9.98,
    previous_close: 10,
    volume: 51_000,
    amount: 513_200,
    volume_ratio: null,
    order_imbalance: null,
    turnover_rate: null,
  });

  assert.deepEqual(rows.map(([label]) => label), [
    "最新价",
    "涨跌幅",
    "今日开盘",
    "最高",
    "最低",
    "昨收",
    "成交量",
    "成交额",
    "量比",
    "实时换手率",
    "委比",
    "行情时间",
  ]);
  assert.equal(rows.find(([label]) => label === "量比")[1], "--");
  assert.equal(rows.find(([label]) => label === "委比")[1], "--");
  assert.equal(rows.find(([label]) => label === "涨跌幅")[1], "+0.80%");
});

test("daily chart selection is bounded and application errors share one path", () => {
  const bars = Array.from({ length: 70 }, (_, index) => ({ timestamp: index }));
  assert.equal(
    latestDailyBars({ market: { daily_bars: bars } }, 60)[0].timestamp,
    10,
  );
  assert.deepEqual(
    applicationErrorFrom({
      error: {
        error_code: "data_unavailable",
        message: "行情暂时不可用",
        retryable: true,
      },
    }),
    {
      error_code: "data_unavailable",
      message: "行情暂时不可用",
      retryable: true,
    },
  );
});
