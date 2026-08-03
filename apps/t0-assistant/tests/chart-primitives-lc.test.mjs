// Renderer 集成测试：验证 PivotZonePrimitive / CzscMarkerPrimitive 的 draw 被实际执行，
// 且中枢、买点、卖点在 canvas 上留下可见绘制痕迹。
// 使用真实 Lightweight Charts + DOM stub，不启动 Electron。
//
// 注意：本文件内联了 primitive 的简化 JS 实现（与 renderer/src/charts/*.ts 逻辑一致），
// 因为 Node ESM 的 strip-only TypeScript 模式不支持参数属性等语法。
import assert from "node:assert/strict";
import test from "node:test";

import {
  ChartGroupKind,
  createChartGroupModel,
  parseMarketTimestamp,
} from "../renderer/src/charts/chart-model.mjs";

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
  const ctxBase = {
    canvas: {
      width: 800,
      height: 400,
      style: {},
      getBoundingClientRect: rect,
      getClientRects: () => [rect()],
    },
    measureText: (text) => ({ width: String(text).length * 6 }),
    fillRect: (...args) => drawCalls.push({ method: "fillRect", args }),
    strokeRect: (...args) => drawCalls.push({ method: "strokeRect", args }),
    fillText: (...args) => drawCalls.push({ method: "fillText", args }),
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
    color: typeof el?.style?.color === "string" ? el.style.color : "rgb(0,0,0)",
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

// ---------------------------------------------------------------------------
// 内联 PivotZonePrimitive / CzscMarkerPrimitive（与 renderer/src/charts/*.ts 逻辑一致）
// ---------------------------------------------------------------------------

const ACTIVE_FILL = "rgba(245, 158, 11, 0.18)";
const ACTIVE_BORDER = "rgba(245, 158, 11, 0.75)";
const INACTIVE_FILL = "rgba(148, 163, 184, 0.10)";
const INACTIVE_BORDER = "rgba(148, 163, 184, 0.55)";

class PivotZonePrimitive {
  constructor() {
    this.zones = [];
    this.paneViews = [
      {
        zOrder: () => "bottom",
        renderer: () => {
          if (!this.chart || !this.series) return null;
          return {
            draw: (target) => {
              if (this.zones.length === 0) return;
              const timeScale = this.chart.timeScale();
              const plotWidth = timeScale.width();
              if (plotWidth <= 0) return;
              target.useBitmapCoordinateSpace((scope) => {
                const ctx = scope.context;
                const hRatio = scope.horizontalPixelRatio;
                const vRatio = scope.verticalPixelRatio;
                for (const zone of this.zones) {
                  const startX = timeScale.timeToCoordinate(zone.start);
                  const endX = timeScale.timeToCoordinate(zone.end);
                  const highY = this.series.priceToCoordinate(zone.high);
                  const lowY = this.series.priceToCoordinate(zone.low);
                  if (highY === null || lowY === null) continue;
                  const left = startX === null ? 0 : startX;
                  const right = endX === null ? plotWidth : endX;
                  if (right <= 0 || left >= plotWidth) continue;
                  const clampedLeft = Math.max(0, left);
                  const clampedRight = Math.min(plotWidth, right);
                  if (clampedRight <= clampedLeft) continue;
                  const top = Math.min(highY, lowY) * vRatio;
                  const height = Math.abs(highY - lowY) * vRatio;
                  const x = clampedLeft * hRatio;
                  const width = (clampedRight - clampedLeft) * hRatio;
                  ctx.fillStyle = zone.active ? ACTIVE_FILL : INACTIVE_FILL;
                  ctx.fillRect(x, top, width, height);
                  ctx.lineWidth = Math.max(1, hRatio);
                  ctx.strokeStyle = zone.active ? ACTIVE_BORDER : INACTIVE_BORDER;
                  ctx.setLineDash(zone.active ? [] : [4 * hRatio, 3 * hRatio]);
                  ctx.strokeRect(x, top, width, height);
                  ctx.setLineDash([]);
                }
              });
            },
          };
        },
      },
    ];
  }

  attached(params) {
    this.chart = params.chart;
    this.series = params.series;
    this.requestUpdate = params.requestUpdate;
  }

  detached() {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
  }

  setZones(zones) {
    this.zones = zones;
    this.requestUpdate?.();
  }

  updateAllViews() {}

  paneViews() {
    return this.paneViews;
  }
}

const BUY_COLOR = "#22c55e";
const SELL_COLOR = "#ef4444";
const ARROW_SIZE = 7;
const ARROW_GAP = 2;
const LABEL_FONT = 10;
const LABEL_GAP = 2;

class CzscMarkerPrimitive {
  constructor() {
    this.markers = [];
    this.paneViews = [
      {
        zOrder: () => "top",
        renderer: () => {
          if (!this.chart || !this.series) return null;
          return {
            draw: (target) => {
              if (this.markers.length === 0) return;
              const timeScale = this.chart.timeScale();
              if (timeScale.width() <= 0) return;
              target.useBitmapCoordinateSpace((scope) => {
                const ctx = scope.context;
                const hRatio = scope.horizontalPixelRatio;
                const vRatio = scope.verticalPixelRatio;
                const size = ARROW_SIZE * Math.min(hRatio, vRatio);
                const gap = ARROW_GAP * vRatio;
                const labelGap = LABEL_GAP * vRatio;
                const font = `${LABEL_FONT * vRatio}px sans-serif`;
                for (const marker of this.markers) {
                  const x = timeScale.timeToCoordinate(marker.time);
                  const y = this.series.priceToCoordinate(marker.price);
                  if (x === null || y === null) continue;
                  const cx = x * hRatio;
                  const cy = y * vRatio;
                  const color = marker.side === "buy" ? BUY_COLOR : SELL_COLOR;
                  ctx.fillStyle = color;
                  ctx.strokeStyle = color;
                  if (marker.side === "buy") {
                    const tipY = cy + gap;
                    const baseY = tipY + size;
                    ctx.beginPath();
                    ctx.moveTo(cx, tipY);
                    ctx.lineTo(cx - size, baseY);
                    ctx.lineTo(cx + size, baseY);
                    ctx.closePath();
                    ctx.fill();
                    ctx.font = font;
                    ctx.textAlign = "center";
                    ctx.textBaseline = "top";
                    ctx.fillText(marker.label, cx, baseY + labelGap);
                  } else {
                    const tipY = cy - gap;
                    const baseY = tipY - size;
                    ctx.beginPath();
                    ctx.moveTo(cx, tipY);
                    ctx.lineTo(cx - size, baseY);
                    ctx.lineTo(cx + size, baseY);
                    ctx.closePath();
                    ctx.fill();
                    ctx.font = font;
                    ctx.textAlign = "center";
                    ctx.textBaseline = "bottom";
                    ctx.fillText(marker.label, cx, baseY - labelGap);
                  }
                }
              });
            },
          };
        },
      },
    ];
  }

  attached(params) {
    this.chart = params.chart;
    this.series = params.series;
    this.requestUpdate = params.requestUpdate;
  }

  detached() {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
  }

  setMarkers(markers) {
    this.markers = markers;
    this.requestUpdate?.();
  }

  updateAllViews() {}

  paneViews() {
    return this.paneViews;
  }
}

// ---------------------------------------------------------------------------
// 测试辅助
// ---------------------------------------------------------------------------

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
    priceFormat: { type: "custom", formatter: (v) => v.toFixed(2), minMove: 0.01 },
  });

  const pivotZonePrimitive = new PivotZonePrimitive();
  const czscMarkerPrimitive = new CzscMarkerPrimitive();
  priceSeries.attachPrimitive(pivotZonePrimitive);
  priceSeries.attachPrimitive(czscMarkerPrimitive);

  return { chart, priceSeries, pivotZonePrimitive, czscMarkerPrimitive };
}

function buildFixtureModel() {
  const snapshot = {
    timezone: "Asia/Shanghai",
    market: {
      bars_5m: [
        { timestamp: "2026-07-22 09:35:00", open: 10.0, high: 10.2, low: 9.9, close: 10.1, volume: 50000, amount: 500000, closed: true },
        { timestamp: "2026-07-22 09:40:00", open: 10.1, high: 10.3, low: 10.0, close: 10.2, volume: 60000, amount: 600000, closed: true },
        { timestamp: "2026-07-22 09:45:00", open: 10.2, high: 10.25, low: 10.15, close: 10.18, volume: 55000, amount: 550000, closed: true },
        { timestamp: "2026-07-22 09:50:00", open: 10.18, high: 10.22, low: 10.1, close: 10.15, volume: 52000, amount: 520000, closed: true },
        { timestamp: "2026-07-22 09:55:00", open: 10.15, high: 10.18, low: 10.12, close: 10.17, volume: 48000, amount: 480000, closed: true },
        { timestamp: "2026-07-22 10:00:00", open: 10.17, high: 10.35, low: 10.17, close: 10.3, volume: 70000, amount: 700000, closed: true },
      ],
      bars_1m: [],
      daily_bars: [],
      quote: null,
    },
    indicators: {
      five_minute: {
        ma: { ma5: [], ma10: [], ma20: [], ma30: [], ma60: [] },
        volume: { values: [], ma5: [], ma10: [] },
        macd: { fast_period: 12, slow_period: 26, signal_period: 9, dif: [], dea: [], histogram: [] },
      },
      one_minute: {
        vwap: [],
        volume: { values: [] },
        macd: { fast_period: 12, slow_period: 26, signal_period: 9, dif: [], dea: [], histogram: [] },
      },
    },
    chan_analysis: {
      strokes: [
        { start_timestamp: "2026-07-22 09:35:00", end_timestamp: "2026-07-22 09:50:00", start_price: 10.0, end_price: 10.15, confirmed: true },
        { start_timestamp: "2026-07-22 09:50:00", end_timestamp: "2026-07-22 10:00:00", start_price: 10.15, end_price: 10.3, confirmed: false },
      ],
      pivot_zones: [
        { start_timestamp: "2026-07-22 09:35:00", end_timestamp: "2026-07-22 09:55:00", high: 10.25, low: 10.1, active: true },
      ],
      candidate_buy_points: [
        { id: "bp-001", point_type: "first_buy", timestamp: "2026-07-22 09:55:00", price: 10.17, reference_id: "s1", confirmed: true, reason: "test" },
      ],
      candidate_sell_points: [
        { id: "sp-001", point_type: "first_sell", timestamp: "2026-07-22 10:00:00", price: 10.3, reference_id: "s2", confirmed: true, reason: "test" },
      ],
    },
  };
  return createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE, {
    strokes: true,
    pivot_zones: true,
  });
}

// ---------------------------------------------------------------------------
// 测试
// ---------------------------------------------------------------------------

test("real LC: PivotZonePrimitive draw renders fillRect and strokeRect for pivot zones", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const { pivotZonePrimitive } = await buildChartWithPrimitives(lc);
    const model = buildFixtureModel();

    pivotZonePrimitive.setZones(
      model.pivotZones.map((zone) => ({
        start: parseMarketTimestamp(zone.start_timestamp),
        end: parseMarketTimestamp(zone.end_timestamp),
        high: zone.high,
        low: zone.low,
        active: zone.active === true,
      })),
    );
    globalThis.__flushRaf();

    const fillRects = drawCalls.filter((c) => c.method === "fillRect");
    const strokeRects = drawCalls.filter((c) => c.method === "strokeRect");
    assert.ok(
      fillRects.length > 0,
      "PivotZonePrimitive must call fillRect for active zones",
    );
    assert.ok(
      strokeRects.length > 0,
      "PivotZonePrimitive must call strokeRect for zone borders",
    );
  } finally {
    restore();
  }
});

test("real LC: CzscMarkerPrimitive draw renders buy arrow and 1B label", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const { czscMarkerPrimitive } = await buildChartWithPrimitives(lc);
    const model = buildFixtureModel();

    czscMarkerPrimitive.setMarkers(
      model.czscMarkers.map((marker) => ({
        time: parseMarketTimestamp(marker.timestamp),
        price: marker.price,
        side: marker.side,
        label: marker.label,
      })),
    );
    globalThis.__flushRaf();

    const texts = drawCalls
      .filter((c) => c.method === "fillText")
      .map((c) => String(c.args[0]));
    assert.ok(
      texts.some((t) => t.includes("1B")),
      `CzscMarkerPrimitive must draw 1B label; got texts: ${JSON.stringify(texts)}`,
    );
    const paths = drawCalls.filter((c) =>
      ["beginPath", "moveTo", "lineTo", "closePath"].includes(c.method),
    );
    assert.ok(
      paths.length > 0,
      "CzscMarkerPrimitive must draw arrow paths for buy markers",
    );
  } finally {
    restore();
  }
});

test("real LC: CzscMarkerPrimitive draw renders sell arrow and 1S label", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const { czscMarkerPrimitive } = await buildChartWithPrimitives(lc);
    const model = buildFixtureModel();

    czscMarkerPrimitive.setMarkers(
      model.czscMarkers.map((marker) => ({
        time: parseMarketTimestamp(marker.timestamp),
        price: marker.price,
        side: marker.side,
        label: marker.label,
      })),
    );
    globalThis.__flushRaf();

    const texts = drawCalls
      .filter((c) => c.method === "fillText")
      .map((c) => String(c.args[0]));
    assert.ok(
      texts.some((t) => t.includes("1S")),
      `CzscMarkerPrimitive must draw 1S label; got texts: ${JSON.stringify(texts)}`,
    );
  } finally {
    restore();
  }
});

test("real LC: empty zones/markers do not emit draw calls", async () => {
  const drawCalls = [];
  const restore = installDom(drawCalls);
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const { pivotZonePrimitive, czscMarkerPrimitive } =
      await buildChartWithPrimitives(lc);

    pivotZonePrimitive.setZones([]);
    czscMarkerPrimitive.setMarkers([]);
    globalThis.__flushRaf();

    const primitiveCalls = drawCalls.filter((c) =>
      ["fillRect", "strokeRect", "fillText"].includes(c.method),
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
