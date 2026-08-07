// 真实 SynchronizedChartGroup 集成测试：分时图全日交易轴视口。
//
// 正确行为：时间轴固定为当日 09:30→15:00（约 242 分钟，含午休压缩），
// 已发生分钟从左往右填充；追加 1 分钟不得把视图钉在右侧或整体左移。
//
// 回归：#143 曾把 fixLeftEdge/fixRightEdge 全局打开；LC 会把右缘钉在最后一根
// 有值分钟上，把程序设定的满日范围钳成 {from:负值, to:最新有值索引}，表现为
// 「从最右边显示、每进步 1 分钟往左移动」。分时必须关闭这两项边缘钳制。
import assert from "node:assert/strict";
import test from "node:test";

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
  width: 400,
  height: 300,
  right: 400,
  bottom: 300,
  x: 0,
  y: 0,
});
const ctxBase = {
  canvas: {
    width: 400,
    height: 300,
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
    width: 400,
    height: 300,
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
    clientWidth: 400,
    clientHeight: 300,
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

const DAY = "2026-01-05";
const FULL_SESSION = 242;

function make1mBars(n) {
  const bars = [];
  let minute = 9 * 60 + 30;
  for (let i = 0; i < n; i++) {
    if (minute === 11 * 60 + 31) minute = 13 * 60;
    const hh = String(Math.floor(minute / 60)).padStart(2, "0");
    const mm = String(minute % 60).padStart(2, "0");
    bars.push({
      timestamp: `${DAY} ${hh}:${mm}:00`,
      open: 10,
      high: 11,
      low: 9,
      close: 10 + i * 0.01,
      volume: 1000,
      amount: 10000,
      closed: true,
    });
    minute += 1;
  }
  return bars;
}

function makeSnapshot(n) {
  const bars = make1mBars(n);
  return {
    session: { trade_date: DAY },
    market: {
      bars_1m: bars,
      bars_5m: [],
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
          ma5: [],
          ma10: [],
        },
        macd: {
          dif: bars.map((b) => ({ timestamp: b.timestamp, value: 0.1 })),
          dea: bars.map((b) => ({ timestamp: b.timestamp, value: 0.05 })),
          histogram: bars.map((b) => ({
            timestamp: b.timestamp,
            value: 0.05,
          })),
        },
      },
    },
    chan_analysis: {},
  };
}

const settle = () => new Promise((r) => setTimeout(r, 150));

async function makeIntradayGroup() {
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
    kind: "one_minute",
  });
  globalThis.__flushRaf();
  return { group, createChartGroupModel };
}

function assertFullSessionRange(range, label) {
  assert.ok(range, `${label}: expected visible range`);
  assert.ok(
    Math.abs(range.from) < 0.5,
    `${label}: 分时应左锚 09:30（from≈0），实际 from=${range.from}`,
  );
  assert.ok(
    Math.abs(range.to - (FULL_SESSION - 1)) < 0.5,
    `${label}: 分时应满轴到 15:00（to≈${FULL_SESSION - 1}），实际 to=${range.to}`,
  );
}

test("real controller: intraday keeps full-session left-to-right axis on live append", async () => {
  const restore = installDom();
  try {
    const { group, createChartGroupModel } = await makeIntradayGroup();

    const model30 = createChartGroupModel(makeSnapshot(30), "one_minute");
    assert.equal(model30.timestamps.length, FULL_SESSION);
    assert.equal(model30.timestamps[0], `${DAY} 09:30:00`);
    assert.equal(model30.timestamps.at(-1), `${DAY} 15:00:00`);

    group.setModel(model30);
    globalThis.__flushRaf();
    await settle();
    assertFullSessionRange(
      group.priceChart.timeScale().getVisibleLogicalRange(),
      "after 30 bars",
    );

    group.setModel(createChartGroupModel(makeSnapshot(31), "one_minute"));
    globalThis.__flushRaf();
    await settle();
    const afterAppend = group.priceChart.timeScale().getVisibleLogicalRange();
    assertFullSessionRange(afterAppend, "after 31 bars");
    // 追加不得把右缘钉到最新有值分钟（旧 bug: to===30）。
    assert.notEqual(
      Math.round(afterAppend.to),
      30,
      "追加后右缘不得等于最新有值索引",
    );
  } finally {
    restore();
  }
});
