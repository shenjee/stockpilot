// Renderer 集成测试：通过真实 Lightweight Charts + 真实 production primitive 实现，
// 验证 PivotZonePrimitive / CzscMarkerPrimitive 的 draw() 在 canvas 上留下可见绘制痕迹。
//
// 本测试直接消费 renderer/src/charts/{pivot-zone,czsc-marker}-primitive.mjs 的
// production 实现（与 SynchronizedChartGroup.ts 同源），不复制任何 primitive 代码。
// 阻断 production draw() 时本测试必须失败（Issue #134 回归保护）。
//
// DOM stub 复用 chart-crosshair-lc.test.mjs 的颜色解析模式：LC 5.x ColorParser 通过
// window.getComputedStyle(el).color 读取 rgb/rgba，需把 hex/rgba 字符串归一为 rgb。
//
// 由于 stub 下价格图、VOL、MACD 三张 canvas 共享同一个 drawCalls 数组（网格、轴、
// K 线本身也会产生 fillRect/fillText），这里通过记录每次 fill/stroke 时的 fillStyle/
// strokeStyle 来筛选出 primitive 专属颜色的调用，避免误判。
import assert from "node:assert/strict";
import test from "node:test";

import {
  ChartGroupKind,
  createChartGroupModel,
  parseMarketTimestamp,
} from "../renderer/src/charts/chart-model.mjs";
import { PivotZonePrimitive } from "../renderer/src/charts/pivot-zone-primitive.mjs";
import { CzscMarkerPrimitive } from "../renderer/src/charts/czsc-marker-primitive.mjs";

// 与 production primitive 同色的过滤集合（仅用于在共享 canvas 中区分 primitive 输出，
// 不复制绘制逻辑）。见 pivot-zone-primitive.mjs / czsc-marker-primitive.mjs。
const PIVOT_FILL_COLORS = new Set([
  "rgba(245, 158, 11, 0.18)",
  "rgba(148, 163, 184, 0.10)",
]);
const PIVOT_STROKE_COLORS = new Set([
  "rgba(245, 158, 11, 0.75)",
  "rgba(148, 163, 184, 0.55)",
]);
const MARKER_COLORS = new Set(["#22c55e", "#ef4444"]);

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

function makeCanvas(drawCalls) {
  // 记录当前 fillStyle/strokeStyle，并在每次 fillRect/strokeRect/fillText 时快照到
  // drawCall.style，便于按 primitive 专属颜色筛选。
  const styleState = { fillStyle: "", strokeStyle: "" };
  const ctxBase = {
    canvas: {
      width: 800,
      height: 400,
      style: {},
      getBoundingClientRect: rect,
      getClientRects: () => [rect()],
    },
    measureText: (text) => ({ width: String(text).length * 6 }),
    fillRect: (...args) =>
      drawCalls.push({ method: "fillRect", args, style: styleState.fillStyle }),
    strokeRect: (...args) =>
      drawCalls.push({ method: "strokeRect", args, style: styleState.strokeStyle }),
    fillText: (...args) =>
      drawCalls.push({ method: "fillText", args, style: styleState.fillStyle }),
    beginPath: () => drawCalls.push({ method: "beginPath", args: [] }),
    moveTo: (...args) => drawCalls.push({ method: "moveTo", args }),
    lineTo: (...args) => drawCalls.push({ method: "lineTo", args }),
    closePath: () => drawCalls.push({ method: "closePath", args: [] }),
    set lineDash(_) {},
  };
  const ctx = new Proxy(ctxBase, {
    get: (target, prop) => (prop in target ? target[prop] : noop),
    set: (target, prop, value) => {
      target[prop] = value;
      if (prop === "fillStyle") styleState.fillStyle = String(value);
      if (prop === "strokeStyle") styleState.strokeStyle = String(value);
      return true;
    },
  });
  return {
    width: 800,
    height: 400,
    style: {},
    classList,
    nodeType: 1,
    tagName: "canvas",
    getContext: () => ctx,
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
  };
}

// LC 5.x ColorParser 只接受 rgb/rgba；把 hex/rgba 归一为 rgb，避免
// `Failed to parse color: #191919` 之类错误（PR #135 review）。
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

function installDom(drawCalls) {
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

  function makeEl(tag) {
    return {
      tagName: tag,
      nodeName: tag,
      nodeType: 1,
      style: {},
      classList,
      children: [],
      childNodes: [],
      clientWidth: 800,
      clientHeight: 400,
      innerHTML: "",
      textContent: "",
      setAttribute: noop,
      getAttribute: () => null,
      toggleAttribute: noop,
      addEventListener: noop,
      removeEventListener: noop,
      appendChild: (child) => child,
      removeChild: noop,
      insertBefore: (node) => node,
      focus: noop,
      blur: noop,
      getBoundingClientRect: rect,
      getClientRects: () => [rect()],
      contains: () => false,
      dispatchEvent: noop,
      ownerDocument: undefined,
      ...(tag === "canvas" ? makeCanvas(drawCalls) : {}),
    };
  }

  const DOC = {
    createElement: (tag) => {
      const el = makeEl(tag);
      el.ownerDocument = DOC;
      return el;
    },
    createElementNS: (_ns, tag) => {
      const el = makeEl(tag);
      el.ownerDocument = DOC;
      return el;
    },
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
  globalThis.location = {
    href: "http://localhost/",
    search: "",
    hostname: "localhost",
    pathname: "/",
  };
  globalThis.history = { pushState: noop, replaceState: noop };
  globalThis.getComputedStyle = (el) => ({
    getPropertyValue: () => "",
    color: stubCssColor(el?.style?.color),
  });
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  const rafQ = [];
  globalThis.requestAnimationFrame = (cb) => {
    rafQ.push(cb);
    return rafQ.length;
  };
  globalThis.cancelAnimationFrame = noop;
  globalThis.__flushRaf = () => {
    let guard = 0;
    while (rafQ.length && guard < 500) {
      rafQ.shift()(Date.now());
      guard += 1;
    }
  };
  globalThis.devicePixelRatio = 1;
  globalThis.matchMedia = () => mql;
  return function restore() {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) {
        delete globalThis[key];
      } else {
        globalThis[key] = value;
      }
    }
    delete globalThis.__flushRaf;
  };
}

// 复用 chart-groups-v1.json 的契约 fixture 构造完整 snapshot，再交给
// production createChartGroupModel 映射出 pivotZones / czscMarkers，
// 保证测试覆盖真实数据流（contract -> model -> primitive）。
async function buildFixtureModel() {
  const { readFile } = await import("node:fs/promises");
  const { dirname, resolve } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const testDir = dirname(fileURLToPath(import.meta.url));
  const fixture = JSON.parse(
    await readFile(
      resolve(testDir, "../contracts/fixtures/chart-groups-v1.json"),
      "utf8",
    ),
  );
  return createChartGroupModel(fixture, ChartGroupKind.FIVE_MINUTE, {
    strokes: true,
    pivot_zones: true,
  });
}

// 把 model 中的契约字段映射为 primitive 输入，与 SynchronizedChartGroup
// setStructureData / applyCzscMarkers 的转换保持一致。
function toPrimitiveZones(model) {
  return model.pivotZones.map((zone) => ({
    start: parseMarketTimestamp(zone.start_timestamp),
    end: parseMarketTimestamp(zone.end_timestamp),
    high: zone.high,
    low: zone.low,
    active: zone.active === true,
  }));
}

function toPrimitiveMarkers(model) {
  return model.czscMarkers.map((marker) => ({
    time: parseMarketTimestamp(marker.timestamp),
    price: marker.price,
    side: marker.side,
    label: marker.label,
  }));
}

async function buildChartWithPrimitives(lc) {
  const { createChart, CandlestickSeries } = lc;
  const container = globalThis.document.createElement("div");
  container.clientWidth = 800;
  container.clientHeight = 400;
  const chart = createChart(container, {
    width: 800,
    height: 400,
  });
  globalThis.__flushRaf();

  const priceSeries = chart.addSeries(CandlestickSeries, {
    priceFormat: {
      type: "custom",
      formatter: (v) => v.toFixed(2),
      minMove: 0.01,
    },
  });

  const pivotZonePrimitive = new PivotZonePrimitive();
  const czscMarkerPrimitive = new CzscMarkerPrimitive();
  priceSeries.attachPrimitive(pivotZonePrimitive);
  priceSeries.attachPrimitive(czscMarkerPrimitive);

  return { chart, priceSeries, pivotZonePrimitive, czscMarkerPrimitive };
}

function setPriceData(priceSeries, model) {
  priceSeries.setData(
    model.price.map((point) => ({
      time: parseMarketTimestamp(point.timestamp),
      open: point.open,
      high: point.high,
      low: point.low,
      close: point.close,
    })),
  );
  globalThis.__flushRaf();
}

const isPivotFill = (call) => PIVOT_FILL_COLORS.has(call.style);
const isPivotStroke = (call) => PIVOT_STROKE_COLORS.has(call.style);
const isMarkerText = (call) => MARKER_COLORS.has(call.style);
const isMarkerPath = (call) =>
  ["beginPath", "moveTo", "lineTo", "closePath"].includes(call.method);

test("real LC + production primitive: pivot zone draw renders fillRect and strokeRect", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    assert.ok(
      model.pivotZones.length > 0,
      "fixture must yield at least one pivot zone",
    );

    const { chart, priceSeries, pivotZonePrimitive } =
      await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    pivotZonePrimitive.setZones(toPrimitiveZones(model));
    globalThis.__flushRaf();

    const pivotFillRects = drawCalls.filter(
      (c) => c.method === "fillRect" && isPivotFill(c),
    );
    const pivotStrokeRects = drawCalls.filter(
      (c) => c.method === "strokeRect" && isPivotStroke(c),
    );
    assert.ok(
      pivotFillRects.length > 0,
      "PivotZonePrimitive.draw must call fillRect with pivot fill color",
    );
    assert.ok(
      pivotStrokeRects.length > 0,
      "PivotZonePrimitive.draw must call strokeRect with pivot border color",
    );
  } finally {
    restore();
  }
});

test("real LC + production primitive: czsc marker draw renders buy arrow and 1B label", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    const buyMarkers = toPrimitiveMarkers(model).filter((m) => m.side === "buy");
    assert.ok(
      buyMarkers.some((m) => m.label.includes("1B")),
      "fixture must yield a 1B buy marker",
    );

    const { priceSeries, czscMarkerPrimitive } = await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    czscMarkerPrimitive.setMarkers(buyMarkers);
    globalThis.__flushRaf();

    const markerTexts = drawCalls
      .filter((c) => c.method === "fillText" && isMarkerText(c))
      .map((c) => String(c.args[0]));
    assert.ok(
      markerTexts.some((t) => t.includes("1B")),
      `CzscMarkerPrimitive.draw must render 1B label; got ${JSON.stringify(markerTexts)}`,
    );
    const markerPaths = drawCalls.filter(isMarkerPath);
    assert.ok(
      markerPaths.length > 0,
      "CzscMarkerPrimitive.draw must draw arrow paths for buy markers",
    );
  } finally {
    restore();
  }
});

test("real LC + production primitive: czsc marker draw renders sell arrow and 1S label", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    const sellMarkers = toPrimitiveMarkers(model).filter(
      (m) => m.side === "sell",
    );
    assert.ok(
      sellMarkers.some((m) => m.label.includes("1S")),
      "fixture must yield a 1S sell marker",
    );

    const { priceSeries, czscMarkerPrimitive } = await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    czscMarkerPrimitive.setMarkers(sellMarkers);
    globalThis.__flushRaf();

    const markerTexts = drawCalls
      .filter((c) => c.method === "fillText" && isMarkerText(c))
      .map((c) => String(c.args[0]));
    assert.ok(
      markerTexts.some((t) => t.includes("1S")),
      `CzscMarkerPrimitive.draw must render 1S label; got ${JSON.stringify(markerTexts)}`,
    );
    const markerPaths = drawCalls.filter(isMarkerPath);
    assert.ok(
      markerPaths.length > 0,
      "CzscMarkerPrimitive.draw must draw arrow paths for sell markers",
    );
  } finally {
    restore();
  }
});

test("real LC + production primitive: empty zones/markers do not emit primitive draw calls", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    const { priceSeries, pivotZonePrimitive, czscMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);

    pivotZonePrimitive.setZones([]);
    czscMarkerPrimitive.setMarkers([]);
    globalThis.__flushRaf();

    const primitiveCalls = drawCalls.filter(
      (c) =>
        (c.method === "fillRect" && isPivotFill(c)) ||
        (c.method === "strokeRect" && isPivotStroke(c)) ||
        (c.method === "fillText" && isMarkerText(c)),
    );
    assert.equal(
      primitiveCalls.length,
      0,
      "Empty zones/markers must not produce primitive draw calls",
    );
  } finally {
    restore();
  }
});
