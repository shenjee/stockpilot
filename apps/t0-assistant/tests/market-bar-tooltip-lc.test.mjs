// 真实 SynchronizedChartGroup 集成：5 分钟固定角落行情 Tooltip（issue #144）。
import assert from "node:assert/strict";
import test from "node:test";

import { parseMarketTimestamp } from "../renderer/src/charts/chart-model.mjs";

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
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
    setAttribute: noop,
    getAttribute: () => null,
    addEventListener: noop,
    removeEventListener: noop,
    appendChild: (c) => c,
    removeChild: noop,
    insertBefore: (n) => n,
  };
}

function makeEl(tag) {
  const el = {
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
    parentNode: null,
    innerHTML: "",
    _textContent: "",
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
    setAttribute: noop,
    getAttribute: () => null,
    toggleAttribute: noop,
    addEventListener: noop,
    removeEventListener: noop,
    focus: noop,
    blur: noop,
    contains: () => false,
    dispatchEvent: noop,
    insertBefore: (n) => n,
  };
  Object.defineProperty(el, "firstChild", {
    get() {
      return el.childNodes[0] ?? null;
    },
  });
  Object.defineProperty(el, "textContent", {
    get() {
      if (el.childNodes.length > 0) {
        return el.childNodes
          .map((child) => child.textContent ?? "")
          .join("");
      }
      return el._textContent;
    },
    set(value) {
      el._textContent = String(value);
      el.childNodes = [];
      el.children = [];
    },
  });
  el.appendChild = (child) => {
    child.parentNode = el;
    el.children.push(child);
    el.childNodes.push(child);
    return child;
  };
  el.removeChild = (child) => {
    el.children = el.children.filter((node) => node !== child);
    el.childNodes = el.childNodes.filter((node) => node !== child);
    child.parentNode = null;
    return child;
  };
  return el;
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
  globalThis.cancelAnimationFrame = (id) => {
    const index = Number(id) - 1;
    if (index >= 0 && index < rafQ.length) {
      rafQ[index] = null;
    }
  };
  globalThis.__flushRaf = () => {
    let guard = 0;
    while (rafQ.length && guard < 500) {
      const cb = rafQ.shift();
      guard += 1;
      if (cb) {
        cb(Date.now());
      }
    }
  };
  globalThis.devicePixelRatio = 1;
  globalThis.matchMedia = () => mql;
  return function restore() {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) {
        delete globalThis[k];
      } else {
        globalThis[k] = v;
      }
    }
    delete globalThis.__flushRaf;
  };
}

const DAY = "2026-07-22";

function makeBars(overrides = {}) {
  const bars = [
    {
      timestamp: `${DAY} 09:35:00`,
      open: 10,
      high: 10.12,
      low: 9.98,
      close: 10.08,
      volume: 51000,
      amount: 0,
      closed: true,
    },
    {
      timestamp: `${DAY} 09:40:00`,
      open: 10.08,
      high: 10.2,
      low: 10.0,
      close: 10.15,
      volume: 42000,
      amount: 0,
      closed: false,
    },
  ];
  return bars.map((bar, index) => ({ ...bar, ...(overrides[index] ?? {}) }));
}

function makeSnapshot(bars = makeBars()) {
  return {
    session: { trade_date: DAY },
    market: { bars_5m: bars, bars_1m: [], quote: { previous_close: 10 } },
    indicators: {
      five_minute: {
        ma: {},
        boll: { upper: [], middle: [], lower: [] },
        volume: {
          values: bars.map((b) => ({
            timestamp: b.timestamp,
            value: b.volume,
          })),
          ma5: [],
          ma10: [],
        },
        macd: { dif: [], dea: [], histogram: [] },
      },
      one_minute: {
        vwap: [],
        volume: { values: [] },
        macd: { dif: [], dea: [], histogram: [] },
      },
    },
    chan_analysis: {},
  };
}

function makeIntradaySnapshot() {
  const bars = Array.from({ length: 3 }, (_, i) => {
    const minute = 30 + i;
    return {
      timestamp: `${DAY} 09:${String(minute).padStart(2, "0")}:00`,
      open: 10,
      high: 10.1,
      low: 9.9,
      close: 10.05,
      volume: 1000,
      amount: 0,
      closed: true,
    };
  });
  return {
    session: { trade_date: DAY },
    market: {
      bars_5m: [],
      bars_1m: bars,
      quote: { previous_close: 10 },
    },
    indicators: {
      five_minute: {
        ma: {},
        boll: { upper: [], middle: [], lower: [] },
        volume: { values: [], ma5: [], ma10: [] },
        macd: { dif: [], dea: [], histogram: [] },
      },
      one_minute: {
        vwap: bars.map((b) => ({ timestamp: b.timestamp, value: b.close })),
        volume: {
          values: bars.map((b) => ({
            timestamp: b.timestamp,
            value: b.volume,
          })),
        },
        macd: { dif: [], dea: [], histogram: [] },
      },
    },
    chan_analysis: {},
  };
}

async function makeGroup(kind) {
  const { SynchronizedChartGroup } = await import(
    "../renderer/src/charts/SynchronizedChartGroup.ts"
  );
  const { createChartGroupModel } = await import(
    "../renderer/src/charts/chart-model.mjs"
  );
  // LC container 使用与其它 LC 测试相同的 noop DOM 挂载，避免 chart.remove 依赖真实父节点。
  const price = {
    tagName: "div",
    nodeName: "div",
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
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
    setAttribute: noop,
    getAttribute: () => null,
    addEventListener: noop,
    removeEventListener: noop,
    appendChild: (c) => c,
    removeChild: noop,
    insertBefore: (n) => n,
    focus: noop,
    blur: noop,
    contains: () => false,
    dispatchEvent: noop,
  };
  const host = makeEl("div");
  const group = new SynchronizedChartGroup({
    containers: { price, volume: { ...price }, macd: { ...price } },
    tooltipHost: host,
    kind,
  });
  globalThis.__flushRaf();
  return { group, createChartGroupModel, host };
}

test("5m chart group owns a tooltip DOM node; 1m does not", async () => {
  const restore = installDom();
  try {
    const five = await makeGroup("five_minute");
    assert.ok(five.group.marketBarTooltipEl);
    assert.equal(five.group.marketBarTooltipEl.className, "market-bar-tooltip");
    assert.equal(five.group.marketBarTooltipEl.style.pointerEvents, "none");
    assert.equal(
      five.host.children.includes(five.group.marketBarTooltipEl),
      true,
    );

    const one = await makeGroup("one_minute");
    assert.equal(one.group.marketBarTooltipEl, null);
    five.group.teardownMarketBarTooltip();
    one.group.teardownMarketBarTooltip();
  } finally {
    restore();
  }
});

test("tooltip shows exact bar, updates in place, hides on missing time", async () => {
  const restore = installDom();
  try {
    const { group, createChartGroupModel } = await makeGroup("five_minute");
    const tip = group.marketBarTooltipEl;
    group.setModel(createChartGroupModel(makeSnapshot(), "five_minute"));
    globalThis.__flushRaf();

    const active = parseMarketTimestamp(`${DAY} 09:40:00`);
    group.marketBarTooltipPointerOverPlot = true;
    group.marketBarTooltipDragging = false;
    group.marketBarTooltipActiveTime = active;
    group.scheduleMarketBarTooltipRefresh();
    globalThis.__flushRaf();

    assert.equal(tip.style.display, "block");
    assert.match(tip.textContent ?? "", /09:40/);
    assert.match(tip.textContent ?? "", /10\.15/);
    assert.match(tip.textContent ?? "", /42,000/);

    const refreshed = makeBars({
      1: { close: 10.33, volume: 99000, closed: false },
    });
    group.setModel(createChartGroupModel(makeSnapshot(refreshed), "five_minute"));
    globalThis.__flushRaf();
    assert.equal(tip.style.display, "block");
    assert.match(tip.textContent ?? "", /10\.33/);
    assert.match(tip.textContent ?? "", /99,000/);
    assert.equal(
      group.marketBarTooltipActiveTime,
      active,
      "追加/刷新不得改写当前激活时间",
    );

    group.marketBarTooltipActiveTime = active + 60;
    group.scheduleMarketBarTooltipRefresh();
    globalThis.__flushRaf();
    assert.equal(tip.style.display, "none");

    group.teardownMarketBarTooltip();
    assert.equal(group.marketBarTooltipEl, null);
  } finally {
    restore();
  }
});

test("drag and leave hide tooltip; destroy clears listeners frame", async () => {
  const restore = installDom();
  try {
    const { group, createChartGroupModel } = await makeGroup("five_minute");
    const tip = group.marketBarTooltipEl;
    group.setModel(createChartGroupModel(makeSnapshot(), "five_minute"));
    globalThis.__flushRaf();

    group.marketBarTooltipPointerOverPlot = true;
    group.marketBarTooltipActiveTime = parseMarketTimestamp(`${DAY} 09:35:00`);
    group.scheduleMarketBarTooltipRefresh();
    globalThis.__flushRaf();
    assert.equal(tip.style.display, "block");

    group.marketBarTooltipDragging = true;
    group.scheduleMarketBarTooltipRefresh();
    globalThis.__flushRaf();
    assert.equal(tip.style.display, "none");

    group.marketBarTooltipDragging = false;
    group.marketBarTooltipPointerOverPlot = false;
    group.marketBarTooltipActiveTime = null;
    group.scheduleMarketBarTooltipRefresh();
    globalThis.__flushRaf();
    assert.equal(tip.style.display, "none");

    group.teardownMarketBarTooltip();
    assert.equal(group.marketBarTooltipCrosshairHandler, null);
    assert.equal(group.marketBarTooltipPointerHandlers, null);
    assert.equal(group.marketBarTooltipFrame, null);
  } finally {
    restore();
  }
});
