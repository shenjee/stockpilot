// 真实 Lightweight Charts 集成测试：验证 chart-viewport 适配层与 LC 的 LogicalRange
// 语义一致。用最小 DOM/canvas stub 在 node 中跑通 createChart（不依赖 jsdom/canvas 包）。
//
// 这些测试复现评审中指出的语义：自然最新 to = length-1；to = length 产生右侧空槽；
// 适配层 toChartLogicalRange/fromChartLogicalRange 与 LC 回调 round-trip 一致。
import assert from "node:assert/strict";
import test from "node:test";

import {
  FollowState,
  createViewportState,
  followLatest,
  fromChartLogicalRange,
  setManualRange,
  toChartLogicalRange,
  visibleLogicalRange,
} from "../renderer/src/charts/chart-viewport.mjs";
import {
  PRICE_AXIS_FINE_MIN_MOVE,
  PRICE_AXIS_INTEGER_MIN_MOVE,
  createPriceExactPriceFormat,
  formatPriceAxisTickLabel,
  formatPriceAxisTickLabels,
  formatPriceExactLabel,
  formatVolumeAxisLabel,
  formatVolumeAxisLabels,
  resolvePriceAxisMinMove,
} from "../renderer/src/charts/chart-model.mjs";
import {
  CHART_RIGHT_Y_AXIS_WIDTH,
  plotWidthsAligned,
  syncChartGroupPriceScaleWidths,
} from "../renderer/src/charts/chart-scale-alignment.mjs";

// ---- minimal DOM/canvas stub (足以让 lightweight-charts 5.x 初始化与时间轴运算) ----
const noop = () => {};
const classList = { add: noop, remove: noop, contains: () => false, toggle: noop };
const mql = {
  matches: false,
  addEventListener: noop,
  removeEventListener: noop,
  addListener: noop,
  removeListener: noop,
};
const rect = () => ({
  left: 0,
  top: 0,
  width: 800,
  height: 400,
  right: 800,
  bottom: 400,
  x: 0,
  y: 0,
});
const ctxBase = {
  canvas: { width: 800, height: 400, style: {}, getBoundingClientRect: rect, getClientRects: () => [rect()] },
  measureText: () => ({ width: 0 }),
};
const ctx = new Proxy(ctxBase, {
  get: (t, p) => (p in t ? t[p] : noop),
  set: (t, p, v) => { t[p] = v; return true; },
});

let DOC;
const elExtras = {
  setAttribute: noop,
  getAttribute: () => null,
  toggleAttribute: noop,
  addEventListener: noop,
  removeEventListener: noop,
  appendChild: (c) => c,
  removeChild: noop,
  insertBefore: (n) => n,
  focus: noop,
  blur: noop,
  getBoundingClientRect: rect,
  getClientRects: () => [rect()],
  contains: () => false,
  dispatchEvent: noop,
};
function makeCanvas() {
  return { width: 800, height: 400, style: {}, classList, nodeType: 1, tagName: "canvas", ownerDocument: DOC, getContext: () => ctx, ...elExtras };
}
function makeEl(tag) {
  return { tagName: tag, nodeName: tag, nodeType: 1, style: {}, classList, children: [], childNodes: [], clientWidth: 800, clientHeight: 400, ownerDocument: DOC, innerHTML: "", textContent: "", ...elExtras };
}

function stubCssColor(value) {
  if (typeof value === "string" && /^rgba?\(/i.test(value)) {
    return value;
  }
  if (typeof value === "string" && /^#([0-9a-f]{6})$/i.test(value)) {
    const n = Number.parseInt(value.slice(1), 16);
    return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
  }
  return "rgb(0, 0, 0)";
}

function installDom() {
  const saved = {
    document: globalThis.document,
    window: globalThis.window,
    location: globalThis.location,
    history: globalThis.history,
    getComputedStyle: globalThis.getComputedStyle,
    ResizeObserver: globalThis.ResizeObserver,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    cancelAnimationFrame: globalThis.cancelAnimationFrame,
    devicePixelRatio: globalThis.devicePixelRatio,
    matchMedia: globalThis.matchMedia,
  };
  DOC = {
    createElement: (t) => (t === "canvas" ? makeCanvas() : makeEl(t)),
    createElementNS: (_n, t) => (t === "canvas" ? makeCanvas() : makeEl(t)),
    addEventListener: noop,
    removeEventListener: noop,
    documentElement: makeEl("html"),
    body: makeEl("body"),
    defaultView: null,
  };
  DOC.documentElement.ownerDocument = DOC;
  DOC.body.ownerDocument = DOC;
  globalThis.document = DOC;
  globalThis.window = globalThis;
  DOC.defaultView = globalThis;
  globalThis.location = { href: "http://localhost/", search: "", hostname: "localhost", pathname: "/" };
  globalThis.history = { pushState: noop, replaceState: noop };
  // LC 5.x ColorParser reads window.getComputedStyle(el).color as rgb/rgba.
  globalThis.getComputedStyle = (el) => ({
    getPropertyValue: () => "",
    color: stubCssColor(el?.style?.color),
  });
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  // 同步驱动 rAF：LC 的 invalidate mask 在 rAF 回调中处理，no-op 会使
  // setVisibleLogicalRange 不生效。这里排空队列（有界防死循环）。
  const rafQ = [];
  globalThis.requestAnimationFrame = (cb) => { rafQ.push(cb); return 0; };
  globalThis.cancelAnimationFrame = noop;
  const flush = () => { let g = 0; while (rafQ.length && g < 500) { rafQ.shift()(Date.now()); g++; } };
  globalThis.__flushRaf = flush;
  globalThis.devicePixelRatio = 1;
  globalThis.matchMedia = () => mql;
  return function restore() {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete globalThis[k];
      else globalThis[k] = v;
    }
    delete globalThis.__flushRaf;
  };
}

async function makeChartWithBars(n) {
  // 相对路径加载开发构建（可读堆栈）；bare specifier 亦可，此处显式指定 dev 构建。
  const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
  const container = makeEl("div");
  const chart = createChart(container, {
    width: 800,
    height: 400,
    localization: { locale: "en-US" },
  });
  globalThis.__flushRaf();
  const data = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(Date.UTC(2026, 0, 1) + i * 86400000);
    data.push({
      time: Math.floor(d.getTime() / 1000),
      open: 10 + i * 0.01,
      high: 11 + i * 0.01,
      low: 9 + i * 0.01,
      close: 10.5 + i * 0.01,
    });
  }
  const series = chart.addSeries(CandlestickSeries);
  series.setData(data);
  globalThis.__flushRaf();
  return chart;
}

test("real LC: natural latest after setData reports to = length - 1 (no empty slot)", async () => {
  const restore = installDom();
  try {
    const N = 100;
    const chart = await makeChartWithBars(N);
    const range = chart.timeScale().getVisibleLogicalRange();
    // 自然最新（rightOffset=0）：to = N-1，最后一根贴右、无空槽。
    assert.equal(range.to, N - 1);
  } finally {
    restore();
  }
});

test("real LC: toChartLogicalRange(following) produces no right empty slot, unlike to=length", async () => {
  const restore = installDom();
  try {
    const N = 100;
    const chart = await makeChartWithBars(N);
    const ts = chart.timeScale();
    const state = followLatest(
      createViewportState(Array.from({ length: N }, (_, i) => `b${i}`)),
      50,
    );
    // 适配层输出 {from:50, to:99}：读回 to=99（无空槽）。
    const adapted = toChartLogicalRange(state);
    assert.deepEqual(adapted, { from: 50, to: 99 });
    ts.setVisibleLogicalRange(adapted);
    globalThis.__flushRaf();
    assert.equal(ts.getVisibleLogicalRange().to, 99);

    // 对照：旧实现直接传 {from:50, to:100} 会产生右侧空槽（to=100=length）。
    ts.setVisibleLogicalRange({ from: 50, to: 100 });
    globalThis.__flushRaf();
    assert.equal(ts.getVisibleLogicalRange().to, 100);
  } finally {
    restore();
  }
});

test("real LC: adapter round-trips through setVisibleLogicalRange and the change callback", async () => {
  const restore = installDom();
  try {
    const N = 100;
    const chart = await makeChartWithBars(N);
    const ts = chart.timeScale();
    const state = setManualRange(
      followLatest(
        createViewportState(Array.from({ length: N }, (_, i) => `b${i}`)),
        50,
      ),
      20,
      60,
    );
    const lc = toChartLogicalRange(state); // {from:20, to:59}
    let emitted = null;
    ts.subscribeVisibleLogicalRangeChange((r) => { emitted = r; });
    ts.setVisibleLogicalRange(lc);
    globalThis.__flushRaf();
    // LC 回调原样保留适配层输出。
    assert.deepEqual(emitted, lc);
    // 反向还原为内部排他范围，与原 state 一致。
    const back = fromChartLogicalRange(emitted, N);
    assert.deepEqual(back, { start: state.visibleStart, end: state.visibleEnd });
  } finally {
    restore();
  }
});

test("real LC: dragging back to the latest edge restores following via the adapter", async () => {
  const restore = installDom();
  try {
    const N = 100;
    const chart = await makeChartWithBars(N);
    const ts = chart.timeScale();
    const bars = Array.from({ length: N }, (_, i) => `b${i}`);
    // 先进入 manual（远离最新）。
    ts.setVisibleLogicalRange({ from: 20, to: 50 });
    globalThis.__flushRaf();
    let emitted = null;
    ts.subscribeVisibleLogicalRangeChange((r) => { emitted = r; });
    // 用户拖回最新边缘：LC 回调 to = N-1 = 99。
    ts.setVisibleLogicalRange({ from: 60, to: 99 });
    globalThis.__flushRaf();
    const internal = fromChartLogicalRange(emitted, N);
    const manual = setManualRange(
      followLatest(createViewportState(bars), 50),
      20,
      50,
    );
    const resumed = setManualRange(manual, internal.start, internal.end);
    assert.equal(resumed.followState, FollowState.FOLLOWING);
  } finally {
    restore();
  }
});

// 评审 P1：最新端缩放（跨度变小、仍贴边）应进入 manual；仅平移回最新端（跨度不变、
// 贴边）才恢复 following。用真实 LC 的 setVisibleLogicalRange 模拟用户缩放/平移，
// 复刻生产 handler 的“原始 LC 跨度比较”判定，再交 setManualRange 验证终态。
test("real LC: zoom at the latest edge stays manual; pan back to the latest edge resumes following", async () => {
  const restore = installDom();
  try {
    const N = 100;
    const chart = await makeChartWithBars(N);
    const ts = chart.timeScale();
    const bars = Array.from({ length: N }, (_, i) => `b${i}`);

    // 基线 following：最后 50 根 -> LC {from:50, to:99}，跨度 49。
    const following = followLatest(createViewportState(bars), 50);
    const baselineLc = toChartLogicalRange(following);
    ts.setVisibleLogicalRange(baselineLc);
    globalThis.__flushRaf();

    const EPSILON = 0.01;
    // 复刻生产 setupViewportTracking handler：比较原始 LC 跨度判定缩放/平移。
    const applyInteraction = (state, prevLc, range) => {
      const prevSpan = prevLc.to - prevLc.from;
      const curSpan = range.to - range.from;
      const isZoom = Math.abs(curSpan - prevSpan) > EPSILON;
      const internal = fromChartLogicalRange(range, N);
      return setManualRange(state, internal.start, internal.end, {
        allowResumeFollowing: !isZoom,
      });
    };

    // 1) 最新端缩放：{from:70,to:99}（跨度 29 < 49）-> manual（不恢复 following）。
    ts.setVisibleLogicalRange({ from: 70, to: 99 });
    globalThis.__flushRaf();
    const zoomedRange = ts.getVisibleLogicalRange();
    const zoomed = applyInteraction(following, baselineLc, zoomedRange);
    assert.equal(zoomed.followState, FollowState.MANUAL);
    assert.deepEqual(visibleLogicalRange(zoomed), { from: 70, to: 100 });

    // 2) 从 following 平移离开最新端：{from:30,to:79}（跨度 49 不变、离边）-> manual。
    const pannedAway = applyInteraction(following, baselineLc, {
      from: 30,
      to: 79,
    });
    assert.equal(pannedAway.followState, FollowState.MANUAL);

    // 3) 平移回最新端：{from:50,to:99}（跨度 49 不变、贴边）-> following。
    const pannedBack = applyInteraction(pannedAway, { from: 30, to: 79 }, {
      from: 50,
      to: 99,
    });
    assert.equal(pannedBack.followState, FollowState.FOLLOWING);
  } finally {
    restore();
  }
});

test("real LC: volume axis keeps compact labels when MA lines are added first", async () => {
  const restore = installDom();
  try {
    const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 0, 0) / 1000;
    const container = makeEl("div");
    container.clientWidth = 800;
    container.clientHeight = 200;
    const volumePriceFormat = {
      type: "custom",
      formatter: formatVolumeAxisLabel,
      tickmarksFormatter: formatVolumeAxisLabels,
      minMove: 1,
    };
    const attachVolumeSeries = (chart) => {
      const lineSeries = chart.addSeries(LineSeries);
      lineSeries.setData([{ time, value: 3_800_000 }]);
      chart
        .addSeries(HistogramSeries, { priceFormat: volumePriceFormat })
        .setData([{ time, value: 4_000_000, color: "#26a69aaa" }]);
      globalThis.__flushRaf();
      return lineSeries;
    };

    const withoutChartFormatter = createChart(container, {
      width: 800,
      height: 200,
    });
    const defaultLineSeries = attachVolumeSeries(withoutChartFormatter);
    assert.equal(
      defaultLineSeries.priceFormatter().format(4_000_000),
      "4000000.00",
    );

    const withChartFormatter = createChart(container, {
      width: 800,
      height: 200,
      localization: {
        priceFormatter: formatVolumeAxisLabel,
        tickmarksPriceFormatter: formatVolumeAxisLabels,
      },
    });
    attachVolumeSeries(withChartFormatter);
    assert.equal(
      withChartFormatter.options().localization.priceFormatter?.(4_000_000),
      "400万",
    );
  } finally {
    restore();
  }
});

test("real LC: synced chart group plot widths align with large volume labels", async () => {
  const restore = installDom();
  try {
    const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const width = 800;
    const height = 200;
    const bars = [];
    for (let i = 0; i < 80; i += 1) {
      const day = 1 + Math.floor(i / 48);
      const minute = (9 * 60 + 35 + (i % 48) * 5) % (24 * 60);
      const hour = String(Math.floor(minute / 60)).padStart(2, "0");
      const min = String(minute % 60).padStart(2, "0");
      bars.push({
        time: Date.UTC(2026, 6, day, Number(hour), Number(min), 0) / 1000,
        open: 10 + i * 0.01,
        high: 11 + i * 0.01,
        low: 9 + i * 0.01,
        close: 10.5 + i * 0.01,
        volume: i === 40 ? 4_000_000 : 120_000 + i * 100,
        macd: Math.sin(i / 8) * 0.05,
      });
    }

    const charts = ["price", "volume", "macd"].map(() => {
      const container = makeEl("div");
      container.clientWidth = width;
      container.clientHeight = height;
      return createChart(container, {
        width,
        height,
        rightPriceScale: { minimumWidth: CHART_RIGHT_Y_AXIS_WIDTH },
      });
    });
    globalThis.__flushRaf();

    charts[0].addSeries(CandlestickSeries).setData(
      bars.map(({ time, open, high, low, close }) => ({
        time,
        open,
        high,
        low,
        close,
      })),
    );
    charts[1].addSeries(HistogramSeries, {
      priceFormat: {
        type: "custom",
        formatter: formatVolumeAxisLabel,
        tickmarksFormatter: formatVolumeAxisLabels,
        minMove: 1,
      },
    }).setData(
      bars.map(({ time, volume, close, open }) => ({
        time,
        value: volume,
        color: close >= open ? "#26a69aaa" : "#ef5350aa",
      })),
    );
    charts[2].addSeries(HistogramSeries).setData(
      bars.map(({ time, macd }) => ({
        time,
        value: macd,
        color: macd >= 0 ? "#26a69aaa" : "#ef5350aa",
      })),
    );
    globalThis.__flushRaf();

    const result = syncChartGroupPriceScaleWidths(charts, {
      flush: () => globalThis.__flushRaf(),
    });
    assert.equal(result.alignedPriceScaleWidth, CHART_RIGHT_Y_AXIS_WIDTH);
    assert.equal(result.converged, true, result.plotWidths.join(", "));
    assert.equal(plotWidthsAligned(result.plotWidths), true);
  } finally {
    restore();
  }
});

test("real LC: render path uses separate exact and tickmark formatters", async () => {
  const restore = installDom();
  try {
    const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 0, 0) / 1000;
    const container = makeEl("div");
    container.clientWidth = 800;
    container.clientHeight = 240;

    const exactCalls = [];
    const tickCalls = [];
    const exactFormatter = (value) => {
      exactCalls.push(value);
      return formatPriceExactLabel(value);
    };
    const tickFormatter = (values) => {
      tickCalls.push(values.map(Number));
      return formatPriceAxisTickLabels(values);
    };

    const priceChart = createChart(container, {
      width: 800,
      height: 240,
      localization: {
        priceFormatter: exactFormatter,
        tickmarksPriceFormatter: tickFormatter,
      },
    });
    const priceSeries = priceChart.addSeries(LineSeries, {
      priceFormat: {
        type: "custom",
        formatter: exactFormatter,
        tickmarksFormatter: tickFormatter,
        minMove: PRICE_AXIS_INTEGER_MIN_MOVE,
      },
      lastValueVisible: true,
    });
    const data = [];
    for (let i = 0; i < 40; i += 1) {
      data.push({ time: time + i * 60, value: 100 + i * 0.4 });
    }
    // Last value = 100 so LC last-value label must render via exact formatter.
    data[data.length - 1] = { time: time + 39 * 60, value: 100 };
    priceSeries.setData(data);
    globalThis.__flushRaf();
    // Force price-scale mark rebuild / last-value formatting through LC internals.
    priceChart.priceScale("right").applyOptions({ autoScale: true });
    globalThis.__flushRaf();
    // Also exercise the crosshair exact-label path without manually calling formatter.
    priceChart.setCrosshairPosition(100, data[data.length - 1].time, priceSeries);
    globalThis.__flushRaf();

    assert.ok(tickCalls.length > 0, "tickmarksFormatter should be invoked");
    for (const batch of tickCalls) {
      assert.ok(batch.length > 0);
      for (const value of batch) {
        if (Math.abs(value) >= 100) {
          assert.equal(
            value,
            Math.round(value),
            `tick ${value} must be integer when abs >= 100`,
          );
        }
      }
      const labels = formatPriceAxisTickLabels(batch);
      assert.equal(new Set(labels).size, labels.length, labels.join(","));
    }

    assert.ok(
      exactCalls.some((value) => formatPriceExactLabel(value) === "100.00"),
      `exact formatter path must format 100 as 100.00; calls=${exactCalls.join(",")}`,
    );
    assert.equal(formatPriceExactLabel(100), "100.00");
    assert.equal(formatPriceAxisTickLabel(100), "100");
  } finally {
    restore();
  }
});

test("real LC: pan across 100 threshold switches minMove 0.01↔1", async () => {
  const restore = installDom();
  try {
    const { createChart, LineSeries } = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 0, 0) / 1000;
    const container = makeEl("div");
    container.clientWidth = 800;
    container.clientHeight = 320;
    const chart = createChart(container, { width: 800, height: 320 });
    const series = chart.addSeries(LineSeries, {
      priceFormat: createPriceExactPriceFormat(PRICE_AXIS_FINE_MIN_MOVE),
    });
    const data = [];
    for (let i = 0; i < 100; i += 1) {
      const value = i < 50 ? 50 + (i % 10) * 0.3 : 100.1 + (i % 10) * 0.4;
      data.push({ time: time + i * 60, value });
    }
    series.setData(data);
    globalThis.__flushRaf();

    // Mirrors SynchronizedChartGroup: after visible-range change, rAF then
    // recompute minMove from the auto-scaled visible price range.
    const syncMinMoveAfterRange = () => {
      requestAnimationFrame(() => {
        const visible = series.priceScale().getVisibleRange();
        const minMove = resolvePriceAxisMinMove(
          visible?.from ?? null,
          visible?.to ?? null,
        );
        series.applyOptions({
          priceFormat: createPriceExactPriceFormat(minMove),
        });
      });
      globalThis.__flushRaf();
      return series.options().priceFormat.minMove;
    };

    chart.timeScale().setVisibleLogicalRange({ from: 0, to: 20 });
    assert.equal(syncMinMoveAfterRange(), PRICE_AXIS_FINE_MIN_MOVE);

    chart.timeScale().setVisibleLogicalRange({ from: 70, to: 90 });
    assert.equal(syncMinMoveAfterRange(), PRICE_AXIS_INTEGER_MIN_MOVE);

    chart.timeScale().setVisibleLogicalRange({ from: 5, to: 25 });
    assert.equal(syncMinMoveAfterRange(), PRICE_AXIS_FINE_MIN_MOVE);
  } finally {
    restore();
  }
});

test("real LC: abs>=100 range keeps integer ticks via minMove=1", async () => {
  const restore = installDom();
  try {
    const { createChart, LineSeries } = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 0, 0) / 1000;
    const container = makeEl("div");
    container.clientWidth = 800;
    container.clientHeight = 320;
    const tickBatches = [];
    const chart = createChart(container, { width: 800, height: 320 });
    const series = chart.addSeries(LineSeries, {
      priceFormat: createPriceExactPriceFormat(PRICE_AXIS_INTEGER_MIN_MOVE),
    });
    const data = [];
    for (let i = 0; i < 80; i += 1) {
      data.push({ time: time + i * 60, value: 100.1 + (i % 10) * 0.05 });
    }
    series.setData(data);
    series.applyOptions({
      priceFormat: {
        type: "custom",
        formatter: formatPriceExactLabel,
        tickmarksFormatter: (values) => {
          tickBatches.push(values.map(Number));
          return formatPriceAxisTickLabels(values);
        },
        minMove: resolvePriceAxisMinMove(100.1, 100.55),
      },
    });
    globalThis.__flushRaf();
    chart.priceScale("right").applyOptions({ autoScale: true });
    globalThis.__flushRaf();
    assert.ok(tickBatches.length > 0);
    for (const batch of tickBatches) {
      for (let i = 0; i < batch.length; i += 1) {
        const value = batch[i];
        assert.equal(value, Math.round(value), `non-integer tick ${value}`);
        if (i > 0) {
          assert.ok(
            Math.abs(batch[i] - batch[i - 1]) >= 1 - 1e-9,
            `tick step too small: ${batch[i - 1]} -> ${batch[i]}`,
          );
        }
      }
      const labels = formatPriceAxisTickLabels(batch);
      assert.equal(new Set(labels).size, labels.length);
      for (let i = 0; i < batch.length; i += 1) {
        assert.equal(labels[i], String(Math.round(batch[i])));
      }
    }
  } finally {
    restore();
  }
});

test("real LC: fixed right axis width stays stable across repeated sync", async () => {
  const restore = installDom();
  try {
    const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const width = 800;
    const height = 200;
    const time = Date.UTC(2026, 6, 22, 10, 0, 0) / 1000;
    const charts = ["price", "volume", "macd"].map(() => {
      const container = makeEl("div");
      container.clientWidth = width;
      container.clientHeight = height;
      return createChart(container, {
        width,
        height,
        layout: { fontSize: 10 },
        rightPriceScale: { minimumWidth: CHART_RIGHT_Y_AXIS_WIDTH },
      });
    });
    const priceSeries = charts[0].addSeries(LineSeries, {
      priceFormat: createPriceExactPriceFormat(PRICE_AXIS_INTEGER_MIN_MOVE),
      lastValueVisible: true,
    });
    priceSeries.setData([{ time, value: 9999.99 }]);
    charts[1]
      .addSeries(HistogramSeries, {
        priceFormat: {
          type: "custom",
          formatter: formatVolumeAxisLabel,
          tickmarksFormatter: formatVolumeAxisLabels,
          minMove: 1,
        },
        lastValueVisible: true,
      })
      .setData([{ time, value: 12_345_700, color: "#26a69aaa" }]); // 1234.57万
    charts[2]
      .addSeries(LineSeries, {
        priceFormat: createPriceExactPriceFormat(PRICE_AXIS_INTEGER_MIN_MOVE),
        lastValueVisible: true,
      })
      .setData([{ time, value: -9999.99 }]);
    globalThis.__flushRaf();

    const observedRightWidths = [];
    const observedPlotWidths = [];
    for (let tick = 0; tick < 8; tick += 1) {
      priceSeries.setData([
        {
          time: time + tick * 60,
          value: tick % 2 === 0 ? 9999.99 : -9999.99,
        },
      ]);
      const result = syncChartGroupPriceScaleWidths(charts, {
        flush: () => globalThis.__flushRaf(),
      });
      observedRightWidths.push(...result.rightPriceScaleWidths);
      observedPlotWidths.push(...result.plotWidths);
      assert.equal(result.converged, true, result.rightPriceScaleWidths.join(","));
      assert.deepEqual(
        result.rightPriceScaleWidths,
        Array(3).fill(CHART_RIGHT_Y_AXIS_WIDTH),
      );
      assert.equal(plotWidthsAligned(result.plotWidths), true);
    }
    assert.ok(observedRightWidths.every((value) => value === CHART_RIGHT_Y_AXIS_WIDTH));
    assert.ok(observedPlotWidths.every((value) => value === observedPlotWidths[0]));
  } finally {
    restore();
  }
});
