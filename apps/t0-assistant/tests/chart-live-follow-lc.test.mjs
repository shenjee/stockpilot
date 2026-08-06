// 真实 SynchronizedChartGroup 集成测试：Live 自动跟随（5 分钟 K 线实盘追加右移）
// 与 suppressUntilFrame 竞态修复的回归。
//
// 背景：实盘交易时间新增 5 分钟 K 线触发 setModel 后，LC 可能在 guard
// （applyingViewportRange）释放后、于数据追加的下一帧布局时再发出一条“视图尚未
// 跟进”的落后可见范围通知（to 仍指向追加前旧右边缘）。若被 setupViewportTracking
// 当作用户平移走 setManualRange，atLatestEdge=false 会把 following 翻成 manual 并
// 把视图钉在旧右边缘——之后的新 K 线全部停在可视窗口右缘之外（Live 不右移）。
// 修复：setModel 末尾用嵌套 rAF 把 suppressUntilFrame 标记到“下一帧结束之后”，
// 窗口内忽略这类落后通知。
//
// 这是项目首个直接 import 真实 .ts 控制器的 node --test 测试（此前 tests/ 只加载
// 纯逻辑 .mjs 与真实 LC）。依赖 node 原生 type-stripping（node ≥ 22.18，实测 v24）。
// 用与 chart-viewport-lc.test.mjs 相同的最小 DOM/canvas stub 跑通真实控制器。
import assert from "node:assert/strict";
import test from "node:test";

// ---- minimal DOM/canvas stub (足以让 lightweight-charts 5.x 与真实控制器初始化) ----
const noop = () => {};
const classList = { add: noop, remove: noop, contains: () => false, toggle: noop };
const mql = {
  matches: false,
  addEventListener: noop,
  removeEventListener: noop,
  addListener: noop,
  removeListener: noop,
};
const rect = () => ({ left: 0, top: 0, width: 800, height: 400, right: 800, bottom: 400, x: 0, y: 0 });
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
  globalThis.location = { href: "http://localhost/", search: "", hostname: "localhost", pathname: "/" };
  globalThis.history = { pushState: noop, replaceState: noop };
  globalThis.getComputedStyle = (el) => ({ getPropertyValue: () => "", color: stubCssColor(el?.style?.color) });
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  const rafQ = [];
  globalThis.requestAnimationFrame = (cb) => { rafQ.push(cb); return rafQ.length; };
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

// ---- 最小 5 分钟快照生成（仅含 createChartGroupModel 必需字段） ----
const DAY = "2026-01-05";
function makeBars(n) {
  const bars = [];
  const base = new Date(`${DAY}T09:30:00`).getTime();
  for (let i = 0; i < n; i++) {
    const t = new Date(base + i * 5 * 60000);
    const hh = String(t.getHours()).padStart(2, "0");
    const mm = String(t.getMinutes()).padStart(2, "0");
    bars.push({ timestamp: `${DAY} ${hh}:${mm}:00`, open: 10, high: 11, low: 9, close: 10.5, volume: 1000, closed: true });
  }
  return bars;
}
function makeSnapshot(n) {
  const bars = makeBars(n);
  return {
    session: { trade_date: DAY },
    market: { bars_5m: bars, quote: { previous_close: 10 } },
    indicators: {
      five_minute: {
        ma: {},
        boll: { upper: [], middle: [], lower: [] },
        volume: { values: bars.map((b) => ({ timestamp: b.timestamp, value: b.volume })), ma5: [], ma10: [] },
        macd: { dif: [], dea: [], histogram: [] },
      },
      one_minute: {},
    },
    chan_analysis: {},
  };
}

async function makeGroup(reports) {
  const { SynchronizedChartGroup } = await import(
    "../renderer/src/charts/SynchronizedChartGroup.ts"
  );
  const { createChartGroupModel } = await import("../renderer/src/charts/chart-model.mjs");
  const group = new SynchronizedChartGroup({
    containers: { price: makeEl("div"), volume: makeEl("div"), macd: makeEl("div") },
    kind: "five_minute",
    onViewportChange: (snap) => reports.push(snap),
  });
  globalThis.__flushRaf();
  return { group, createChartGroupModel };
}

const settle = () => new Promise((r) => setTimeout(r, 150)); // 越过 onViewportChange 的 120ms 防抖

test("real controller: 5m live append keeps following and shifts right", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(48), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    reports.length = 0;
    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following", "实盘追加应保持 following");
    assert.equal(last.range.to, 48, "最新 K 线应右移到可视窗口右缘（length-1）");
  } finally {
    restore();
  }
});

test("real controller: stale append notification inside suppress window does not flip to manual", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // setModel 追加 49->50：suppress 窗口在 flush 前置位。
    group.setModel(createChartGroupModel(makeSnapshot(50), "five_minute"));
    // suppress 窗口内注入落后通知：to 指向追加前旧 latest=48（长度 50 的旧右缘）。
    // 经运行时访问 private priceChart（TS private 仅编译期，type-stripping 后可访问）。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 48 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following", "suppress 窗口内的落后通知不应翻 manual");
    assert.equal(last.range.to, 49, "视图应已跟进到新 latest，而非钉在旧边缘");
  } finally {
    restore();
  }
});

test("real controller: genuine user pan outside suppress window still flips to manual", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // suppress 窗口外（上一帧已 flush、已 settle）：用户真实左移 -> 应翻 manual。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 30 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "manual", "suppress 不误伤窗口外的真实用户平移");
  } finally {
    restore();
  }
});

test("real controller: scroll clamped at both edges (no blank beyond latest/oldest)", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(48), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    const ts = group.priceChart.timeScale();
    // 尝试向右拖出空白（to 越过 latest=47）：应被钳回数据右缘，无右侧空槽。
    ts.setVisibleLogicalRange({ from: 12, to: 60 });
    globalThis.__flushRaf();
    const afterRight = ts.getVisibleLogicalRange();
    assert.equal(afterRight.to, 47, `右拖出应精确钳制在 latest=47，实际 to=${afterRight.to}`);

    // 尝试向左拖出空白（from 越过 oldest=0）：应被钳回数据左缘，无左侧空槽。
    ts.setVisibleLogicalRange({ from: -15, to: 33 });
    globalThis.__flushRaf();
    const afterLeft = ts.getVisibleLogicalRange();
    assert.equal(afterLeft.from, 0, `左拖出应精确钳制在 oldest=0，实际 from=${afterLeft.from}`);
  } finally {
    restore();
  }
});
