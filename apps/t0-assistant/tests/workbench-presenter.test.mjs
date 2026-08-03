import assert from "node:assert/strict";
import test from "node:test";

import {
  applicationErrorFrom,
  canHydratePreferences,
  createLatestRequestTracker,
  initialSecuritySearchState,
  isCompleteWorkbenchSnapshot,
  latestDailyBars,
  liveMarketViewLines,
  liveOperationFailurePresentation,
  operationMatchesEnvelope,
  quoteRows,
  securitiesFromSearchResponse,
  securityCategoryLabel,
  securitySearchEnterTarget,
  securitySearchReducer,
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

test("calendar failures stay non-blocking in Live mode", () => {
  const error = {
    error_code: "calendar_unavailable",
    message: "交易日历覆盖不足，无法权威解析有效交易日",
    retryable: false,
    affected_capability: "market_calendar",
  };
  assert.deepEqual(liveOperationFailurePresentation("live", error), {
    blocking: false,
    error,
  });
});

test("live market view uses latest branch as_of when quote is missing", () => {
  const lines = liveMarketViewLines({
    effective_trade_date: "2026-07-24",
    calendar_status: "available",
    market_phase: "market_closed",
    symbol_availability: "available",
    data_quality: "full",
    polling_profile: "idle",
    quote_as_of: null,
    bars_1m_as_of: "2026-07-24 15:00:00",
    bars_5m_as_of: "2026-07-24 15:00:00",
    daily_as_of: "2026-07-24 15:00:00",
  });
  assert.equal(lines.find(([label]) => label === "快照截止")?.[1], "07-24 15:00:00");
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

test("securityCategoryLabel maps standard fields to market classification labels", () => {
  // 沪市 A 股 -> 沪市
  assert.equal(
    securityCategoryLabel({
      symbol: "sh.600000",
      code: "600000",
      market: "sh",
      name: "浦发银行",
      security_type: "a_share",
    }),
    "沪市",
  );
  // 深市 A 股 (e.g. 300113) -> 深市
  assert.equal(
    securityCategoryLabel({
      symbol: "sz.300113",
      code: "300113",
      market: "sz",
      name: "顺网科技",
      security_type: "a_share",
    }),
    "深市",
  );
  // 沪市 ETF -> 基金
  assert.equal(
    securityCategoryLabel({
      symbol: "sh.510300",
      code: "510300",
      market: "sh",
      name: "沪深300ETF",
      security_type: "etf",
    }),
    "基金",
  );
  // 深市 ETF -> 基金
  assert.equal(
    securityCategoryLabel({
      symbol: "sz.159915",
      code: "159915",
      market: "sz",
      name: "创业板ETF",
      security_type: "etf",
    }),
    "基金",
  );
  // 000001 深市 -> 深市
  assert.equal(
    securityCategoryLabel({
      symbol: "sz.000001",
      code: "000001",
      market: "sz",
      name: "平安银行",
      security_type: "a_share",
    }),
    "深市",
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

test("live market view lines expose trade date, phase, polling and cache status", () => {
  const lines = liveMarketViewLines({
    effective_trade_date: "2026-07-24",
    calendar_status: "available",
    market_phase: "market_closed",
    symbol_availability: "available",
    data_quality: "full",
    polling_profile: "idle",
    quote_as_of: "2026-07-24 15:00:03",
  });
  assert.deepEqual(
    lines.map(([label]) => label),
    ["展示交易日", "市场阶段", "刷新状态", "快照截止"],
  );
  assert.equal(lines[0][1], "2026-07-24");
  assert.equal(lines[1][1], "休市");
  assert.equal(lines[2][1], "暂停轮询");
  assert.equal(lines[3][1], "07-24 15:00:03");
  assert.deepEqual(liveMarketViewLines(null), []);
  assert.deepEqual(liveMarketViewLines({}, { replayMode: true }), []);
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

// ---------------------------------------------------------------------------
// Security search box interaction reducer
//
// PR #137 review: the "select" action must close the dropdown *before* the
// parent's async onSelect resolves, and mouse-enter must sync the keyboard
// highlight so Enter picks the hovered item.
// ---------------------------------------------------------------------------

test("initial security search state has no active item and is not dismissed", () => {
  assert.deepEqual(initialSecuritySearchState, { activeIndex: -1, dismissed: false });
  assert.equal(Object.isFrozen(initialSecuritySearchState), true);
});

test("arrow-down cycles through suggestions and wraps from last to first", () => {
  const count = 3;
  let state = initialSecuritySearchState;

  state = securitySearchReducer(state, { type: "arrow-down", count });
  assert.equal(state.activeIndex, 0);
  assert.equal(state.dismissed, false);

  state = securitySearchReducer(state, { type: "arrow-down", count });
  assert.equal(state.activeIndex, 1);

  state = securitySearchReducer(state, { type: "arrow-down", count });
  assert.equal(state.activeIndex, 2);

  // wrap
  state = securitySearchReducer(state, { type: "arrow-down", count });
  assert.equal(state.activeIndex, 0);
});

test("arrow-up cycles backwards and wraps from first to last", () => {
  const count = 3;
  let state = initialSecuritySearchState;

  // From -1, ArrowUp jumps to the last item
  state = securitySearchReducer(state, { type: "arrow-up", count });
  assert.equal(state.activeIndex, 2);
  assert.equal(state.dismissed, false);

  state = securitySearchReducer(state, { type: "arrow-up", count });
  assert.equal(state.activeIndex, 1);

  state = securitySearchReducer(state, { type: "arrow-up", count });
  assert.equal(state.activeIndex, 0);

  // wrap
  state = securitySearchReducer(state, { type: "arrow-up", count });
  assert.equal(state.activeIndex, 2);
});

test("arrow keys with zero suggestions are a no-op", () => {
  let state = { activeIndex: 1, dismissed: false };
  state = securitySearchReducer(state, { type: "arrow-down", count: 0 });
  assert.equal(state.activeIndex, 1);

  state = securitySearchReducer(state, { type: "arrow-up", count: 0 });
  assert.equal(state.activeIndex, 1);
});

test("escape dismisses the dropdown and clears the active item", () => {
  let state = { activeIndex: 2, dismissed: false };
  state = securitySearchReducer(state, { type: "escape", visible: true });
  assert.equal(state.dismissed, true);
  assert.equal(state.activeIndex, -1);
});

test("escape is a no-op when the dropdown is not visible", () => {
  let state = { activeIndex: 1, dismissed: false };
  state = securitySearchReducer(state, { type: "escape", visible: false });
  assert.equal(state.dismissed, false);
  assert.equal(state.activeIndex, 1);
});

test("query-change resets cursor and reopens the dropdown", () => {
  let state = { activeIndex: 2, dismissed: true };
  state = securitySearchReducer(state, { type: "query-change" });
  assert.equal(state.activeIndex, -1);
  assert.equal(state.dismissed, false);
});

test("reset-cursor clears the active index without touching dismissed", () => {
  let state = { activeIndex: 1, dismissed: true };
  state = securitySearchReducer(state, { type: "reset-cursor" });
  assert.equal(state.activeIndex, -1);
  assert.equal(state.dismissed, true);
});

test("mouse-enter updates the active index to the hovered item", () => {
  let state = { activeIndex: 0, dismissed: false };
  state = securitySearchReducer(state, { type: "mouse-enter", index: 2 });
  assert.equal(state.activeIndex, 2);
  assert.equal(state.dismissed, false);
});

test("select action closes the dropdown immediately even when onSelect is slow", () => {
  // Simulate the component's selectSuggestion(security) flow:
  //   1. dispatch({ type: "select" })  -> dropdown closes immediately
  //   2. onSelect(security)             -> slow async callback (never resolves)
  const onSelectCalls = [];
  const slowOnSelect = (security) => {
    onSelectCalls.push(security);
    return new Promise(() => {}); // never resolves — simulates a slow response
  };

  // Start with an active item and dropdown visible
  let state = { activeIndex: 1, dismissed: false };
  const security = {
    symbol: "sh.600000",
    code: "600000",
    market: "sh",
    name: "浦发银行",
    security_type: "a_share",
  };

  // Step 1: dispatch "select" — this is what selectSuggestion does first
  state = securitySearchReducer(state, { type: "select" });

  // Step 2: call the (slow) onSelect — this happens after the dispatch
  slowOnSelect(security);

  // The dropdown is closed immediately, before the async callback resolves
  assert.equal(state.dismissed, true);
  assert.equal(state.activeIndex, -1);
  assert.equal(onSelectCalls.length, 1);
});

test("select action closes the dropdown even when onSelect throws", () => {
  // Simulate a failing onSelect callback
  const failingOnSelect = () => {
    throw new Error("network failure");
  };

  let state = { activeIndex: 0, dismissed: false };

  // selectSuggestion dispatches "select" first, then calls onSelect
  state = securitySearchReducer(state, { type: "select" });

  // Even if onSelect throws, the dropdown is already closed
  assert.equal(state.dismissed, true);
  assert.equal(state.activeIndex, -1);

  assert.throws(() => failingOnSelect(), /network failure/);

  // State remains closed after the failure
  assert.equal(state.dismissed, true);
  assert.equal(state.activeIndex, -1);
});

test("keyboard highlight A then mouse-enter B makes Enter select B", () => {
  // Regression test for PR #137 review comment 2:
  // User arrows down to item A, then moves the mouse over item B.
  // Enter must select B (the hovered item), not A (the old keyboard item).
  const count = 3;
  let state = initialSecuritySearchState;

  // Keyboard: ArrowDown highlights A (index 0)
  state = securitySearchReducer(state, { type: "arrow-down", count });
  assert.equal(state.activeIndex, 0);

  // Mouse: hover B (index 1) — must sync the active index
  state = securitySearchReducer(state, { type: "mouse-enter", index: 1 });
  assert.equal(state.activeIndex, 1);

  // Enter would select B (index 1), not A (index 0)
  assert.equal(securitySearchEnterTarget(state, count), 1);

  // And the subsequent "select" action closes the dropdown
  state = securitySearchReducer(state, { type: "select" });
  assert.equal(state.dismissed, true);
  assert.equal(state.activeIndex, -1);
});

test("Enter with no active item selects the first suggestion", () => {
  let state = initialSecuritySearchState;
  assert.equal(securitySearchEnterTarget(state, 3), 0);
});

test("Enter with zero suggestions selects nothing", () => {
  let state = { activeIndex: 0, dismissed: false };
  assert.equal(securitySearchEnterTarget(state, 0), null);
});

test("Enter during searching does not select stale results from previous query", () => {
  // Regression test for PR #137 re-review comment 1:
  // After query A returns results, the user types query B.  During the
  // debounce/network window the component clears stale suggestions
  // (count = 0) and sets searching = true.  Enter must NOT select a
  // stale result that is no longer visible.
  const countA = 3;
  let state = initialSecuritySearchState;

  // Query A: user navigates and has active results
  state = securitySearchReducer(state, { type: "arrow-down", count: countA });
  assert.equal(state.activeIndex, 0);

  // User types query B -> query-change resets cursor and reopens dropdown
  state = securitySearchReducer(state, { type: "query-change" });
  assert.equal(state.activeIndex, -1);
  assert.equal(state.dismissed, false);

  // During searching, suggestions is empty (count = 0) because the
  // component clears stale results immediately when a new query starts.
  // Enter must return null (no selection).
  assert.equal(securitySearchEnterTarget(state, 0), null);

  // Arrow keys during searching are also no-ops (count = 0)
  state = securitySearchReducer(state, { type: "arrow-down", count: 0 });
  assert.equal(state.activeIndex, -1);
  state = securitySearchReducer(state, { type: "arrow-up", count: 0 });
  assert.equal(state.activeIndex, -1);
});
