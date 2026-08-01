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
  formatVolumeAxisLabel,
} from "../renderer/src/charts/chart-model.mjs";
import {
  plotWidthsAligned,
  syncChartGroupPriceScaleWidths,
} from "../renderer/src/charts/chart-scale-alignment.mjs";

// ---- minimal DOM/canvas stub (足以让 lightweight-charts 4.x 初始化与时间轴运算) ----
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
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
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
  const { createChart } = await import(
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
  const series = chart.addCandlestickSeries();
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
    const { createChart } = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 0, 0) / 1000;
    const container = makeEl("div");
    container.clientWidth = 800;
    container.clientHeight = 200;
    const volumePriceFormat = {
      type: "custom",
      formatter: formatVolumeAxisLabel,
      minMove: 1,
    };
    const attachVolumeSeries = (chart) => {
      const lineSeries = chart.addLineSeries();
      lineSeries.setData([{ time, value: 3_800_000 }]);
      chart
        .addHistogramSeries({ priceFormat: volumePriceFormat })
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
      localization: { priceFormatter: formatVolumeAxisLabel },
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
    const { createChart } = await import(
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
        rightPriceScale: { minimumWidth: 58 },
      });
    });
    globalThis.__flushRaf();

    charts[0].addCandlestickSeries().setData(
      bars.map(({ time, open, high, low, close }) => ({
        time,
        open,
        high,
        low,
        close,
      })),
    );
    charts[1].addHistogramSeries({
      priceFormat: {
        type: "custom",
        formatter: formatVolumeAxisLabel,
        minMove: 1,
      },
    }).setData(
      bars.map(({ time, volume, close, open }) => ({
        time,
        value: volume,
        color: close >= open ? "#26a69aaa" : "#ef5350aa",
      })),
    );
    charts[2].addHistogramSeries().setData(
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
    assert.equal(result.converged, true, result.plotWidths.join(", "));
    assert.equal(plotWidthsAligned(result.plotWidths), true);
  } finally {
    restore();
  }
});
