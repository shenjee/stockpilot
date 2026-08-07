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
import { DivergenceMarkerPrimitive } from "../renderer/src/charts/divergence-marker-primitive.mjs";

// 与 production primitive 同色的过滤集合（仅用于在共享 canvas 中区分 primitive 输出，
// 不复制绘制逻辑）。见 pivot-zone / czsc-marker / divergence-marker primitive。
const PIVOT_FILL_COLORS = new Set([
  "rgba(245, 158, 11, 0.18)",
  "rgba(148, 163, 184, 0.10)",
]);
const PIVOT_STROKE_COLORS = new Set([
  "rgba(245, 158, 11, 0.75)",
  "rgba(148, 163, 184, 0.55)",
]);
const MARKER_COLORS = new Set(["#22c55e", "#ef4444"]);
const DIVERGENCE_COLORS = new Set(["#ef4444", "#22c55e"]);

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

function toPrimitiveDivergences(model) {
  return model.divergenceMarkers.map((marker) => ({
    time: parseMarketTimestamp(marker.timestamp),
    price: marker.price,
    side: marker.side,
    label: marker.label,
    divergenceType: marker.divergenceType,
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
    // Production hides the time axis on the price pane. In LC 5.x this makes
    // timeScale().width() return 0 even though the drawable pane is 800px wide.
    timeScale: { visible: false },
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
  const divergenceMarkerPrimitive = new DivergenceMarkerPrimitive();
  priceSeries.attachPrimitive(pivotZonePrimitive);
  priceSeries.attachPrimitive(czscMarkerPrimitive);
  priceSeries.attachPrimitive(divergenceMarkerPrimitive);

  return {
    chart,
    priceSeries,
    pivotZonePrimitive,
    czscMarkerPrimitive,
    divergenceMarkerPrimitive,
  };
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

const BUY_MARKER_COLOR = "#ef4444";
const SELL_MARKER_COLOR = "#22c55e";
const BULL_DIV_COLOR = "#ef4444";
const BEAR_DIV_COLOR = "#22c55e";

const isPivotFill = (call) => PIVOT_FILL_COLORS.has(call.style);
const isPivotStroke = (call) => PIVOT_STROKE_COLORS.has(call.style);
const isMarkerText = (call) => MARKER_COLORS.has(call.style);
const isDivergenceText = (call) => DIVERGENCE_COLORS.has(call.style);
const markerTextsWithStyle = (drawCalls, style) =>
  drawCalls
    .filter((c) => c.method === "fillText" && c.style === style)
    .map((c) => String(c.args[0]));

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

    const { chart, priceSeries, czscMarkerPrimitive } = await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    assert.equal(
      chart.timeScale().width(),
      0,
      "regression setup must match the hidden production time axis",
    );
    czscMarkerPrimitive.setMarkers(buyMarkers);
    globalThis.__flushRaf();

    const buyTexts = markerTextsWithStyle(drawCalls, BUY_MARKER_COLOR);
    assert.ok(
      buyTexts.some((t) => t.includes("1B")),
      `CzscMarkerPrimitive.draw must render red 1B label; got ${JSON.stringify(buyTexts)}`,
    );
    assert.ok(
      buyTexts.includes("↑"),
      `CzscMarkerPrimitive.draw must render red ↑ arrow for buy markers; got ${JSON.stringify(buyTexts)}`,
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

    const sellTexts = markerTextsWithStyle(drawCalls, SELL_MARKER_COLOR);
    assert.ok(
      sellTexts.some((t) => t.includes("1S")),
      `CzscMarkerPrimitive.draw must render green 1S label; got ${JSON.stringify(sellTexts)}`,
    );
    assert.ok(
      sellTexts.includes("↓"),
      `CzscMarkerPrimitive.draw must render green ↓ arrow for sell markers; got ${JSON.stringify(sellTexts)}`,
    );
  } finally {
    restore();
  }
});

test("hidden time axis: structural candidates render Buy? and Sell? labels", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    const anchor = model.price.at(-1);
    assert.ok(anchor, "fixture must provide a marker anchor bar");

    const { chart, priceSeries, czscMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    assert.equal(chart.timeScale().width(), 0);
    czscMarkerPrimitive.setMarkers([
      {
        time: parseMarketTimestamp(anchor.timestamp),
        price: anchor.low,
        side: "buy",
        label: "Buy?",
      },
      {
        time: parseMarketTimestamp(anchor.timestamp),
        price: anchor.high,
        side: "sell",
        label: "Sell?",
      },
    ]);
    globalThis.__flushRaf();

    const labels = drawCalls
      .filter((call) => call.method === "fillText" && isMarkerText(call))
      .map((call) => String(call.args[0]));
    assert.ok(labels.includes("Buy?"), `expected Buy? label; got ${JSON.stringify(labels)}`);
    assert.ok(labels.includes("Sell?"), `expected Sell? label; got ${JSON.stringify(labels)}`);
    assert.ok(labels.includes("↑"), `expected buy ↑ arrow; got ${JSON.stringify(labels)}`);
    assert.ok(labels.includes("↓"), `expected sell ↓ arrow; got ${JSON.stringify(labels)}`);

    // 同一根 K 上：绿色卖点绘制在上方（y 更小），红色买点绘制在下方（y 更大）。
    const arrowY = (style, text) =>
      drawCalls.find(
        (c) => c.method === "fillText" && c.style === style && c.args[0] === text,
      )?.args[2];
    const buyArrowY = arrowY(BUY_MARKER_COLOR, "↑");
    const sellArrowY = arrowY(SELL_MARKER_COLOR, "↓");
    assert.ok(
      typeof buyArrowY === "number" && typeof sellArrowY === "number",
      "expected both arrow draw calls with y coordinates",
    );
    assert.ok(
      sellArrowY < buyArrowY,
      `sell arrow must render above buy arrow; sellY=${sellArrowY} buyY=${buyArrowY}`,
    );
  } finally {
    restore();
  }
});

test("mid-body candidate prices anchor label+arrow fully outside the candle", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    const anchor = model.price.at(-1);
    assert.ok(anchor, "fixture must provide a marker anchor bar");

    const { priceSeries, czscMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    const mid = (anchor.high + anchor.low) / 2;
    czscMarkerPrimitive.setMarkers([
      {
        time: parseMarketTimestamp(anchor.timestamp),
        price: mid,
        side: "buy",
        label: "1B",
      },
      {
        time: parseMarketTimestamp(anchor.timestamp),
        price: mid,
        side: "sell",
        label: "1S",
      },
    ]);
    globalThis.__flushRaf();

    // devicePixelRatio=1，drawCalls 记录的是位图坐标，可与 priceToCoordinate 直接比较。
    const highY = priceSeries.priceToCoordinate(anchor.high);
    const lowY = priceSeries.priceToCoordinate(anchor.low);
    assert.ok(highY !== null && lowY !== null, "anchor bar must be on-screen");

    const findY = (style, text) =>
      drawCalls.find(
        (c) => c.method === "fillText" && c.style === style && c.args[0] === text,
      )?.args[2];
    const sellArrowY = findY(SELL_MARKER_COLOR, "↓");
    const sellLabelY = findY(SELL_MARKER_COLOR, "1S");
    const buyArrowY = findY(BUY_MARKER_COLOR, "↑");
    const buyLabelY = findY(BUY_MARKER_COLOR, "1B");
    for (const [name, value] of Object.entries({
      sellArrowY,
      sellLabelY,
      buyArrowY,
      buyLabelY,
    })) {
      assert.ok(typeof value === "number", `expected ${name} draw call y coordinate`);
    }

    // 卖点整体在 K 线最高价的上方：1S 在上，↓ 底端也在 high 之上。
    assert.ok(
      sellArrowY < highY,
      `sell arrow bottom must clear the candle high; arrowY=${sellArrowY} highY=${highY}`,
    );
    assert.ok(
      sellLabelY < sellArrowY,
      `sell label must sit above the arrow; labelY=${sellLabelY} arrowY=${sellArrowY}`,
    );
    // 买点整体在 K 线最低价的下方：↑ 顶端在 low 之下，1B 在箭头之下。
    assert.ok(
      buyArrowY > lowY,
      `buy arrow top must clear the candle low; arrowY=${buyArrowY} lowY=${lowY}`,
    );
    assert.ok(
      buyLabelY > buyArrowY,
      `buy label must sit below the arrow; labelY=${buyLabelY} arrowY=${buyArrowY}`,
    );
  } finally {
    restore();
  }
});

test("same-side same-bar markers stack vertically without overlap", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    const anchor = model.price.at(-1);
    assert.ok(anchor, "fixture must provide a marker anchor bar");

    const { priceSeries, czscMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    const time = parseMarketTimestamp(anchor.timestamp);
    const mid = (anchor.high + anchor.low) / 2;
    czscMarkerPrimitive.setMarkers([
      // 同一根 K 线、两个买点：价格较低的先给，验证组内排序与堆叠。
      { time, price: anchor.low, side: "buy", label: "2B" },
      { time, price: anchor.high, side: "buy", label: "1B" },
      // 同一根 K 线、两个卖点：价格较高的先给。
      { time, price: anchor.high, side: "sell", label: "2S" },
      { time, price: mid, side: "sell", label: "1S" },
    ]);
    globalThis.__flushRaf();

    const findY = (style, text) =>
      drawCalls.find(
        (c) => c.method === "fillText" && c.style === style && c.args[0] === text,
      )?.args[2];
    const highY = priceSeries.priceToCoordinate(anchor.high);
    const lowY = priceSeries.priceToCoordinate(anchor.low);
    assert.ok(highY !== null && lowY !== null, "anchor bar must be on-screen");

    const buy1LabelY = findY(BUY_MARKER_COLOR, "1B");
    const buy2LabelY = findY(BUY_MARKER_COLOR, "2B");
    const sell1LabelY = findY(SELL_MARKER_COLOR, "1S");
    const sell2LabelY = findY(SELL_MARKER_COLOR, "2S");
    for (const [name, value] of Object.entries({
      buy1LabelY,
      buy2LabelY,
      sell1LabelY,
      sell2LabelY,
    })) {
      assert.ok(typeof value === "number", `expected ${name} draw call y coordinate`);
    }

    // 两个买点都完整位于 K 线下方，且纵向错开；价高者（1B）更靠近 K 线。
    assert.ok(
      buy1LabelY > lowY && buy2LabelY > lowY,
      `both buy labels must clear the candle low; 1B=${buy1LabelY} 2B=${buy2LabelY} lowY=${lowY}`,
    );
    assert.ok(
      buy1LabelY < buy2LabelY,
      `higher-priced buy must stack nearer the candle; 1B=${buy1LabelY} 2B=${buy2LabelY}`,
    );
    // 两个卖点都完整位于 K 线上方，且纵向错开；价低者（1S）更靠近 K 线。
    assert.ok(
      sell1LabelY < highY && sell2LabelY < highY,
      `both sell labels must clear the candle high; 1S=${sell1LabelY} 2S=${sell2LabelY} highY=${highY}`,
    );
    assert.ok(
      sell1LabelY > sell2LabelY,
      `lower-priced sell must stack nearer the candle; 1S=${sell1LabelY} 2S=${sell2LabelY}`,
    );
  } finally {
    restore();
  }
});

test("missing candle falls back to candidate price and still stacks", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    assert.ok(model.price.length >= 2, "fixture must provide at least two bars");

    const { priceSeries, czscMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    // 最后一根用 whitespace（只有 time，无 high/low）占位：时间轴上有该点，
    // 但 barByTime 不会索引它，触发候选点价格回退路径。
    const orphan = model.price.at(-1);
    const orphanTime = parseMarketTimestamp(orphan.timestamp);
    priceSeries.setData(
      model.price.slice(0, -1).map((point) => ({
        time: parseMarketTimestamp(point.timestamp),
        open: point.open,
        high: point.high,
        low: point.low,
        close: point.close,
      })).concat([{ time: orphanTime }]),
    );
    globalThis.__flushRaf();

    const mid = (orphan.high + orphan.low) / 2;
    czscMarkerPrimitive.setMarkers([
      { time: orphanTime, price: orphan.low, side: "buy", label: "2B" },
      { time: orphanTime, price: mid, side: "buy", label: "1B" },
      { time: orphanTime, price: mid, side: "sell", label: "1S" },
      { time: orphanTime, price: orphan.high, side: "sell", label: "2S" },
    ]);
    globalThis.__flushRaf();

    // 回退锚点：买组排序后 items[0]=mid（价高），卖组 items[0]=mid（价低）。
    const anchorY = priceSeries.priceToCoordinate(mid);
    assert.ok(anchorY !== null, "fallback anchor price must be on-screen");

    const findY = (style, text) =>
      drawCalls.find(
        (c) => c.method === "fillText" && c.style === style && c.args[0] === text,
      )?.args[2];
    const buy1LabelY = findY(BUY_MARKER_COLOR, "1B");
    const buy2LabelY = findY(BUY_MARKER_COLOR, "2B");
    const sell1LabelY = findY(SELL_MARKER_COLOR, "1S");
    const sell2LabelY = findY(SELL_MARKER_COLOR, "2S");
    for (const [name, value] of Object.entries({
      buy1LabelY,
      buy2LabelY,
      sell1LabelY,
      sell2LabelY,
    })) {
      assert.ok(typeof value === "number", `expected ${name} draw call y coordinate`);
    }

    // 买点整组在回退锚点之下并纵向错开；卖点整组在回退锚点之上并纵向错开。
    assert.ok(
      buy1LabelY > anchorY && buy2LabelY > buy1LabelY,
      `buy stack must fall below fallback anchor; 1B=${buy1LabelY} 2B=${buy2LabelY} anchorY=${anchorY}`,
    );
    assert.ok(
      sell1LabelY < anchorY && sell2LabelY < sell1LabelY,
      `sell stack must rise above fallback anchor; 1S=${sell1LabelY} 2S=${sell2LabelY} anchorY=${anchorY}`,
    );
  } finally {
    restore();
  }
});

test("stacked markers on viewport extreme bars stay inside the canvas", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    assert.ok(model.price.length >= 2, "fixture must provide multiple bars");

    const { priceSeries, czscMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    // 关闭默认比例边距，确保裁切回归只由 primitive 的 AutoScaleMargins 负责。
    priceSeries.priceScale().applyOptions({
      scaleMargins: { top: 0, bottom: 0 },
    });
    setPriceData(priceSeries, model);

    const lowest = model.price.reduce((a, b) => (a.low <= b.low ? a : b));
    const highest = model.price.reduce((a, b) => (a.high >= b.high ? a : b));
    const lowTime = parseMarketTimestamp(lowest.timestamp);
    const highTime = parseMarketTimestamp(highest.timestamp);

    // 深度 3 的堆叠（约 85px）远超零边距视口，没有 autoscaleInfo 必被裁掉。
    czscMarkerPrimitive.setMarkers([
      { time: lowTime, price: lowest.low, side: "buy", label: "1B" },
      { time: lowTime, price: lowest.low + 0.01, side: "buy", label: "2B" },
      { time: lowTime, price: lowest.low + 0.02, side: "buy", label: "3B" },
      { time: highTime, price: highest.high, side: "sell", label: "1S" },
      { time: highTime, price: highest.high - 0.01, side: "sell", label: "2S" },
      { time: highTime, price: highest.high - 0.02, side: "sell", label: "3S" },
    ]);
    globalThis.__flushRaf();

    const info = czscMarkerPrimitive.autoscaleInfo(0, model.price.length);
    assert.ok(info?.margins, "autoscaleInfo must provide pixel margins");
    // 深度 3：ARROW_GAP + 3*(13+2+10) + 2*STACK_GAP = 2 + 75 + 8 = 85
    assert.equal(info.margins.below, 85, "buy stack depth 3 needs 85px below");
    assert.equal(info.margins.above, 85, "sell stack depth 3 needs 85px above");

    const canvasHeight = 400; // installDom 固定 devicePixelRatio=1、容器高 400
    const markerYs = drawCalls
      .filter((c) => c.method === "fillText" && isMarkerText(c))
      .map((c) => Number(c.args[2]));
    assert.ok(markerYs.length >= 12, "expected arrows+labels for 6 markers");
    for (const y of markerYs) {
      assert.ok(
        y >= 0 && y <= canvasHeight,
        `stacked marker text must stay inside the canvas; y=${y}`,
      );
    }
  } finally {
    restore();
  }
});

test("barByTime cache invalidates when high/low shifts cancel in sum", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    const anchor = model.price.at(-1);
    assert.ok(anchor, "fixture must provide a marker anchor bar");

    const { priceSeries, czscMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    const time = parseMarketTimestamp(anchor.timestamp);

    // 预热签名缓存（旧实现只累加 high+low，下面的对称偏移总和不变）。
    const warm = czscMarkerPrimitive.resolveBarByTime().get(time);
    assert.ok(warm, "anchor bar must be indexed");
    assert.equal(warm.high, anchor.high);
    assert.equal(warm.low, anchor.low);

    const delta = 0.1;
    const nextHigh = anchor.high + delta;
    const nextLow = anchor.low - delta;
    assert.equal(
      nextHigh + nextLow,
      anchor.high + anchor.low,
      "test premise: high/low shift must cancel in sum",
    );

    // 保留其余 bar，只改最后一根极值；条数与时间戳不变。
    priceSeries.setData(
      model.price.slice(0, -1).map((point) => ({
        time: parseMarketTimestamp(point.timestamp),
        open: point.open,
        high: point.high,
        low: point.low,
        close: point.close,
      })).concat([
        {
          time,
          open: anchor.open,
          high: nextHigh,
          low: nextLow,
          close: anchor.close,
        },
      ]),
    );
    globalThis.__flushRaf();

    const refreshed = czscMarkerPrimitive.resolveBarByTime().get(time);
    assert.ok(refreshed, "anchor bar must remain indexed after extreme update");
    assert.equal(
      refreshed.high,
      nextHigh,
      "cache must rebuild when high rises even if high+low sum is unchanged",
    );
    assert.equal(
      refreshed.low,
      nextLow,
      "cache must rebuild when low falls even if high+low sum is unchanged",
    );

    czscMarkerPrimitive.setMarkers([
      { time, price: nextLow, side: "buy", label: "1B" },
      { time, price: nextHigh, side: "sell", label: "1S" },
    ]);
    globalThis.__flushRaf();

    const findY = (style, text) =>
      drawCalls.find(
        (c) => c.method === "fillText" && c.style === style && c.args[0] === text,
      )?.args[2];
    const highY = priceSeries.priceToCoordinate(nextHigh);
    const lowY = priceSeries.priceToCoordinate(nextLow);
    const buyArrowY = findY(BUY_MARKER_COLOR, "↑");
    const sellArrowY = findY(SELL_MARKER_COLOR, "↓");
    assert.ok(highY !== null && lowY !== null, "updated extremes must be on-screen");
    assert.ok(typeof buyArrowY === "number", "expected buy arrow draw");
    assert.ok(typeof sellArrowY === "number", "expected sell arrow draw");
    assert.ok(
      buyArrowY > lowY,
      `buy arrow must clear the updated low; arrowY=${buyArrowY} lowY=${lowY}`,
    );
    assert.ok(
      sellArrowY < highY,
      `sell arrow must clear the updated high; arrowY=${sellArrowY} highY=${highY}`,
    );
  } finally {
    restore();
  }
});

test("real LC + production primitive: divergence draw renders Bull Div and Bear Div", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const model = await buildFixtureModel();
    const divergences = toPrimitiveDivergences(model);
    assert.ok(
      divergences.some((m) => m.label === "Bull Div"),
      "fixture must yield a Bull Div marker",
    );
    assert.ok(
      divergences.some((m) => m.label === "Bear Div"),
      "fixture must yield a Bear Div marker",
    );

    const { priceSeries, divergenceMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);
    divergenceMarkerPrimitive.setMarkers(divergences);
    globalThis.__flushRaf();

    const bullTexts = markerTextsWithStyle(drawCalls, BULL_DIV_COLOR);
    const bearTexts = markerTextsWithStyle(drawCalls, BEAR_DIV_COLOR);
    assert.ok(
      bullTexts.includes("Bull Div"),
      `DivergenceMarkerPrimitive.draw must render red Bull Div; got ${JSON.stringify(bullTexts)}`,
    );
    assert.ok(
      bullTexts.includes("↑"),
      `DivergenceMarkerPrimitive.draw must render red ↑ for Bull Div; got ${JSON.stringify(bullTexts)}`,
    );
    assert.ok(
      bearTexts.includes("Bear Div"),
      `DivergenceMarkerPrimitive.draw must render green Bear Div; got ${JSON.stringify(bearTexts)}`,
    );
    assert.ok(
      bearTexts.includes("↓"),
      `DivergenceMarkerPrimitive.draw must render green ↓ for Bear Div; got ${JSON.stringify(bearTexts)}`,
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
    const { priceSeries, pivotZonePrimitive, czscMarkerPrimitive, divergenceMarkerPrimitive } =
      await buildChartWithPrimitives(lc);
    setPriceData(priceSeries, model);

    pivotZonePrimitive.setZones([]);
    czscMarkerPrimitive.setMarkers([]);
    divergenceMarkerPrimitive.setMarkers([]);
    globalThis.__flushRaf();

    const primitiveCalls = drawCalls.filter(
      (c) =>
        (c.method === "fillRect" && isPivotFill(c)) ||
        (c.method === "strokeRect" && isPivotStroke(c)) ||
        (c.method === "fillText" && isMarkerText(c)) ||
        (c.method === "fillText" && isDivergenceText(c)),
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
