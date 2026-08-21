// Real SynchronizedChartGroup acceptance for Issue #163 trade overlay isolation:
// extreme trade prices must not expand the candle price axis, setTradeMarkers
// must not call candle/indicator setData, and viewport follow state stays put.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { projectTradeMarkers } from "../renderer/src/charts/trade-markers.mjs";

const noop = () => {};
const classList = {
  add: noop,
  remove: noop,
  contains: () => false,
  toggle: noop,
};
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
  canvas: {
    width: 800,
    height: 400,
    style: {},
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
  },
  measureText: () => ({ width: 0 }),
};
const ctx = new Proxy(ctxBase, {
  get: (t, p) => (p in t ? t[p] : noop),
  set: (t, p, v) => {
    t[p] = v;
    return true;
  },
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
  return {
    width: 800,
    height: 400,
    style: {},
    classList,
    nodeType: 1,
    tagName: "canvas",
    ownerDocument: DOC,
    getContext: () => ctx,
    ...elExtras,
  };
}
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
    ownerDocument: DOC,
    innerHTML: "",
    textContent: "",
    ...elExtras,
  };
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
    let g = 0;
    while (rafQ.length && g < 500) {
      rafQ.shift()(Date.now());
      g++;
    }
  };
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

const testDir = dirname(fileURLToPath(import.meta.url));
const baseSnapshot = JSON.parse(
  readFileSync(
    resolve(testDir, "../contracts/fixtures/chart-groups-v1.json"),
    "utf8",
  ),
);

function withSession(snapshot) {
  return {
    ...snapshot,
    session: {
      ...(snapshot.session ?? {}),
      session_id: "live-test",
      session_type: "live",
      symbol: "sh.600519",
      trade_date: "2024-07-22",
      state: "ready",
      revision: 1,
    },
  };
}

async function makeFiveMinuteGroup(onViewportChange) {
  const { SynchronizedChartGroup } = await import(
    "../renderer/src/charts/SynchronizedChartGroup.ts"
  );
  const { createChartGroupModel } = await import(
    "../renderer/src/charts/chart-model.mjs"
  );
  const group = new SynchronizedChartGroup({
    containers: {
      price: makeEl("div"),
      volume: makeEl("div"),
      macd: makeEl("div"),
    },
    kind: "five_minute",
    onViewportChange,
  });
  globalThis.__flushRaf();
  return { group, createChartGroupModel };
}

const settle = () => new Promise((r) => setTimeout(r, 150));

test("real controller: extreme trade markers do not expand candle price axis", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeFiveMinuteGroup((snap) =>
      reports.push(snap),
    );
    const model = createChartGroupModel(
      withSession(baseSnapshot),
      "five_minute",
    );
    group.setModel(model);
    globalThis.__flushRaf();
    await settle();

    const scaleBefore = group.priceSeries.priceScale().getVisibleRange();
    assert.ok(scaleBefore, "candle scale should be ready after setModel");

    const candleSetDataCalls = [];
    const originalSetData = group.priceSeries.setData.bind(group.priceSeries);
    group.priceSeries.setData = (data) => {
      candleSetDataCalls.push(data);
      return originalSetData(data);
    };

    const volumeSetDataCalls = [];
    const originalVolumeSetData = group.volumeSeries.setData.bind(
      group.volumeSeries,
    );
    group.volumeSeries.setData = (data) => {
      volumeSetDataCalls.push(data);
      return originalVolumeSetData(data);
    };

    const followBefore = reports[reports.length - 1]?.followState ?? null;
    const rangeBefore = reports[reports.length - 1]?.range
      ? { ...reports[reports.length - 1].range }
      : null;

    const extremeMarkers = projectTradeMarkers(
      [
        {
          trade_id: "extreme-high",
          bucket_start: model.timestamps[0],
          trade_scope: "real",
          symbol: "sh.600519",
          side: "buy",
          executed_at: model.timestamps[0],
          price: 1_000_000,
          quantity: 200,
          fee: 1,
          note: "",
          fee_plan_id: null,
        },
        {
          trade_id: "extreme-low",
          bucket_start: model.timestamps[0],
          trade_scope: "real",
          symbol: "sh.600519",
          side: "sell",
          executed_at: model.timestamps[0],
          price: 0.01,
          quantity: 200,
          fee: 1,
          note: "",
          fee_plan_id: null,
        },
      ],
      { allowedTimes: new Set(Object.values(model.timeByTimestamp)) },
    );
    assert.equal(extremeMarkers.length, 2);

    group.setTradeMarkers(extremeMarkers);
    globalThis.__flushRaf();
    await settle();

    const scaleAfter = group.priceSeries.priceScale().getVisibleRange();
    assert.deepEqual(
      scaleAfter,
      scaleBefore,
      "extreme trade prices must not change candle autoscale",
    );
    assert.equal(candleSetDataCalls.length, 0, "must not refresh candle setData");
    assert.equal(volumeSetDataCalls.length, 0, "must not refresh volume setData");
    assert.equal(group.tradeMarkerSeries.size, 2);

    const followAfter = reports[reports.length - 1]?.followState ?? null;
    const rangeAfter = reports[reports.length - 1]?.range ?? null;
    assert.equal(followAfter, followBefore);
    assert.deepEqual(rangeAfter, rangeBefore);

    group.setTradeMarkers([]);
    globalThis.__flushRaf();
    await settle();
    const scaleCleared = group.priceSeries.priceScale().getVisibleRange();
    assert.deepEqual(
      scaleCleared,
      scaleBefore,
      "clearing markers must keep the same price scale",
    );
    assert.equal(group.tradeMarkerSeries.size, 0);
    assert.equal(candleSetDataCalls.length, 0);
  } finally {
    restore();
  }
});
