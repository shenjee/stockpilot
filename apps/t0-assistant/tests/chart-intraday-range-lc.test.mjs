// 真实 Lightweight Charts 集成测试：验证分时价格图前收居中对称坐标缩放
// (spec §6.2.1, issue #143) 与 LC priceScale API 的交互语义。
//
// 验证要点：
// 1. setVisibleRange 设置自定义范围后，autoScale=false 使范围在 setData 和
//    水平 logical range 变化时保持不变（只扩不缩 / 不收缩）。
// 2. setAutoScale(true) 正确回退到自动缩放。
// 3. calculateIntradayPriceRange 的输出通过 setVisibleRange 应用后，P0 位于纵向正中央。
import assert from "node:assert/strict";
import test from "node:test";

import { calculateIntradayPriceRange } from "../renderer/src/charts/chart-model.mjs";

// ---- minimal DOM/canvas stub (与 chart-viewport-lc.test.mjs 共享同一模式) ----
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
  left: 0, top: 0, width: 800, height: 400, right: 800, bottom: 400, x: 0, y: 0,
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
  setAttribute: noop, getAttribute: () => null, toggleAttribute: noop,
  addEventListener: noop, removeEventListener: noop,
  appendChild: (c) => c, removeChild: noop, insertBefore: (n) => n,
  focus: noop, blur: noop,
  getBoundingClientRect: rect, getClientRects: () => [rect()],
  contains: () => false, dispatchEvent: noop,
};
function makeCanvas() {
  return { width: 800, height: 400, style: {}, classList, nodeType: 1, tagName: "canvas", ownerDocument: DOC, getContext: () => ctx, ...elExtras };
}
function makeEl(tag) {
  return { tagName: tag, nodeName: tag, nodeType: 1, style: {}, classList, children: [], childNodes: [], clientWidth: 800, clientHeight: 400, ownerDocument: DOC, innerHTML: "", textContent: "", ...elExtras };
}
function stubCssColor(value) {
  if (typeof value === "string" && /^rgba?\(/i.test(value)) return value;
  if (typeof value === "string" && /^#([0-9a-f]{6})$/i.test(value)) {
    const n = Number.parseInt(value.slice(1), 16);
    return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
  }
  return "rgb(0, 0, 0)";
}
function installDom() {
  const saved = {
    document: globalThis.document, window: globalThis.window,
    location: globalThis.location, history: globalThis.history,
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
    addEventListener: noop, removeEventListener: noop,
    documentElement: makeEl("html"), body: makeEl("body"), defaultView: null,
  };
  DOC.documentElement.ownerDocument = DOC;
  DOC.body.ownerDocument = DOC;
  globalThis.document = DOC;
  globalThis.window = globalThis;
  DOC.defaultView = globalThis;
  globalThis.location = { href: "http://localhost/", search: "", hostname: "localhost", pathname: "/" };
  globalThis.history = { pushState: noop, replaceState: noop };
  globalThis.getComputedStyle = (el) => ({
    getPropertyValue: () => "",
    color: stubCssColor(el?.style?.color),
  });
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
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

async function makeIntradayChart() {
  const { createChart, LineSeries } = await import(
    "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
  );
  const container = makeEl("div");
  container.clientWidth = 800;
  container.clientHeight = 400;
  const chart = createChart(container, {
    width: 800,
    height: 400,
    localization: { locale: "en-US" },
  });
  const series = chart.addSeries(LineSeries);
  return { chart, series };
}

function minuteTime(offsetMinutes) {
  return Math.floor(Date.UTC(2026, 6, 22, 9, 30, 0) / 1000) + offsetMinutes * 60;
}

test("real LC: setVisibleRange disables autoScale and sets custom price range", async () => {
  const restore = installDom();
  try {
    const { series } = await makeIntradayChart();
    const scale = series.priceScale();
    assert.equal(scale.options().autoScale, true);

    scale.setVisibleRange({ from: 90, to: 110 });
    globalThis.__flushRaf();

    assert.equal(scale.options().autoScale, false);
    const range = scale.getVisibleRange();
    assert.ok(range);
    assert.ok(Math.abs(range.from - 90) < 1e-6);
    assert.ok(Math.abs(range.to - 110) < 1e-6);
  } finally {
    restore();
  }
});

test("real LC: custom price range persists across setData (live data update)", async () => {
  const restore = installDom();
  try {
    const { series } = await makeIntradayChart();
    const scale = series.priceScale();

    // Initial data
    series.setData([
      { time: minuteTime(0), value: 100 },
      { time: minuteTime(1), value: 101 },
    ]);
    globalThis.__flushRaf();

    // Set custom range centred on P0=100
    scale.setVisibleRange({ from: 95, to: 105 });
    globalThis.__flushRaf();

    // Append new data (live update)
    series.setData([
      { time: minuteTime(0), value: 100 },
      { time: minuteTime(1), value: 101 },
      { time: minuteTime(2), value: 102 },
      { time: minuteTime(3), value: 103 },
    ]);
    globalThis.__flushRaf();

    // Range should NOT change (autoScale is off)
    const range = scale.getVisibleRange();
    assert.ok(range);
    assert.ok(Math.abs(range.from - 95) < 1e-6);
    assert.ok(Math.abs(range.to - 105) < 1e-6);
  } finally {
    restore();
  }
});

test("real LC: custom price range persists across horizontal logical range change", async () => {
  const restore = installDom();
  try {
    const { chart, series } = await makeIntradayChart();
    const scale = series.priceScale();
    const ts = chart.timeScale();

    series.setData([
      { time: minuteTime(0), value: 100 },
      { time: minuteTime(1), value: 101 },
      { time: minuteTime(2), value: 102 },
      { time: minuteTime(3), value: 99 },
      { time: minuteTime(4), value: 100 },
    ]);
    globalThis.__flushRaf();

    // Set custom range centred on P0=100
    scale.setVisibleRange({ from: 95, to: 105 });
    globalThis.__flushRaf();

    // Simulate user horizontal pan/zoom
    ts.setVisibleLogicalRange({ from: 0, to: 2 });
    globalThis.__flushRaf();

    // Price range should NOT change
    const range = scale.getVisibleRange();
    assert.ok(range);
    assert.ok(Math.abs(range.from - 95) < 1e-6);
    assert.ok(Math.abs(range.to - 105) < 1e-6);
  } finally {
    restore();
  }
});

test("real LC: updating setVisibleRange with expanded range works (new high)", async () => {
  const restore = installDom();
  try {
    const { series } = await makeIntradayChart();
    const scale = series.priceScale();

    series.setData([
      { time: minuteTime(0), value: 100 },
      { time: minuteTime(1), value: 102 },
    ]);
    globalThis.__flushRaf();

    // Initial range: R=2%, yMin=98, yMax=102
    scale.setVisibleRange({ from: 98, to: 102 });
    globalThis.__flushRaf();

    // New data with higher H -> expanded range: R=5%, yMin=95, yMax=105
    scale.setVisibleRange({ from: 95, to: 105 });
    globalThis.__flushRaf();

    const range = scale.getVisibleRange();
    assert.ok(range);
    assert.ok(Math.abs(range.from - 95) < 1e-6);
    assert.ok(Math.abs(range.to - 105) < 1e-6);
  } finally {
    restore();
  }
});

test("real LC: setAutoScale(true) reverts to automatic scaling", async () => {
  const restore = installDom();
  try {
    const { series } = await makeIntradayChart();
    const scale = series.priceScale();

    series.setData([
      { time: minuteTime(0), value: 100 },
      { time: minuteTime(1), value: 108 },
    ]);
    globalThis.__flushRaf();

    // Set custom range
    scale.setVisibleRange({ from: 92, to: 108 });
    globalThis.__flushRaf();
    assert.equal(scale.options().autoScale, false);

    // Revert to autoScale
    scale.setAutoScale(true);
    globalThis.__flushRaf();
    assert.equal(scale.options().autoScale, true);

    // Auto-scaled range should differ from the custom [92, 108] range
    const range = scale.getVisibleRange();
    assert.ok(range);
    const changed = Math.abs(range.from - 92) > 0.01 || Math.abs(range.to - 108) > 0.01;
    assert.ok(changed, `auto-scale range should differ from [92, 108], got [${range.from}, ${range.to}]`);
  } finally {
    restore();
  }
});

test("real LC: calculateIntradayPriceRange output applied to LC keeps P0 at centre", async () => {
  const restore = installDom();
  try {
    const { series } = await makeIntradayChart();
    const scale = series.priceScale();

    const P0 = 65.41;
    const bars = [
      { open: 65.50, high: 70.59, low: 65.20 },
      { open: 70.00, high: 70.59, low: 69.50 },
    ];
    const result = calculateIntradayPriceRange(P0, bars);
    assert.ok(result);

    // Apply to LC
    scale.setVisibleRange({ from: result.yMin, to: result.yMax });
    globalThis.__flushRaf();

    const range = scale.getVisibleRange();
    assert.ok(range);
    assert.ok(Math.abs(range.from - result.yMin) < 1e-6);
    assert.ok(Math.abs(range.to - result.yMax) < 1e-6);

    // P0 at vertical centre: (yMin + yMax) / 2 ≈ P0
    const centre = (range.from + range.to) / 2;
    assert.ok(Math.abs(centre - P0) < 1e-6);

    // Symmetric: yMax - P0 ≈ P0 - yMin
    assert.ok(Math.abs((range.to - P0) - (P0 - range.from)) < 1e-6);
  } finally {
    restore();
  }
});

test("real LC: zero scaleMargins maps custom range edge-to-edge", async () => {
  const restore = installDom();
  try {
    const { chart, series } = await makeIntradayChart();
    const scale = series.priceScale();

    series.setData([
      { time: minuteTime(0), value: 100 },
      { time: minuteTime(1), value: 105 },
    ]);
    globalThis.__flushRaf();

    // Set zero margins + custom range
    scale.applyOptions({ scaleMargins: { top: 0, bottom: 0 } });
    scale.setVisibleRange({ from: 95, to: 105 });
    globalThis.__flushRaf();

    // With zero margins, yMin should map to the bottom edge and yMax to the top edge.
    // LC coordinate: 0 = top, height = bottom (non-inverted).
    // priceToCoordinate lives on the series API in LC v5.
    const bottomCoord = series.priceToCoordinate(95);
    const topCoord = series.priceToCoordinate(105);
    const midCoord = series.priceToCoordinate(100);
    assert.ok(typeof bottomCoord === "number", "priceToCoordinate should return a number");
    assert.ok(typeof topCoord === "number");
    assert.ok(typeof midCoord === "number");

    // P0=100 should be at the vertical centre: midCoord ≈ (topCoord + bottomCoord) / 2
    const centre = (topCoord + bottomCoord) / 2;
    assert.ok(Math.abs(midCoord - centre) < 1, `P0 coordinate ${midCoord} should be at centre ${centre}`);

    // yMax (105) maps near top edge (small coordinate), yMin (95) near bottom (large coordinate)
    assert.ok(topCoord < bottomCoord, `yMax should have smaller coordinate than yMin`);
    assert.ok(topCoord < midCoord && midCoord < bottomCoord, `coordinates should be ordered yMax < P0 < yMin`);
  } finally {
    restore();
  }
});
