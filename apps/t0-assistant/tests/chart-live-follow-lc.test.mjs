// 真实 SynchronizedChartGroup 集成测试：Live 自动跟随（5 分钟 K 线实盘追加右移）
// 与 suppressUntilFrame 竞态修复的回归，以及后台/恢复生命周期回归。
//
// 背景：实盘交易时间新增 5 分钟 K 线触发 setModel 后，LC 可能在 guard
// （applyingViewportRange）释放后、于数据追加的下一帧布局时再发出一条“视图尚未
// 跟进”的落后可见范围通知（to 仍指向追加前旧右边缘）。若被 setupViewportTracking
// 当作用户平移走 setManualRange，atLatestEdge=false 会把 following 翻成 manual 并
// 把视图钉在旧右边缘——之后的新 K 线全部停在可视窗口右缘之外（Live 不右移）。
// 修复：setModel 末尾用嵌套 rAF 把 suppressUntilFrame 标记到“下一帧结束之后”，
// 窗口内忽略这类落后通知。
//
// 后台/恢复回归：后台时 rAF 被节流，suppressUntilFrame 的嵌套回调可能延迟执行，
// 与 LC 布局通知的相对顺序改变。恢复时若 suppress 标记先被清除、旧范围通知后进入
// handler，会误触发 following→manual。测试用逐帧 rAF 调度器模拟该时序。
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
// 每个元素独立的事件监听器表，支持 addEventListener/removeEventListener/dispatchEvent。
// setupUserGestureTracking 在 price 容器上注册 pointerdown/wheel/touchstart 监听器；
// 测试用 dispatchGesture(priceContainer) 模拟真实用户手势，触发来源门控。
function makeElExtras() {
  const listeners = new Map(); // type -> Set<cb>
  return {
    setAttribute: noop,
    getAttribute: () => null,
    toggleAttribute: noop,
    addEventListener(type, cb) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(cb);
    },
    removeEventListener(type, cb) {
      listeners.get(type)?.delete(cb);
    },
    dispatchEvent(evt) {
      const type = evt?.type;
      if (!type) return true;
      for (const cb of listeners.get(type) ?? []) cb(evt);
      return true;
    },
    appendChild: (c) => c,
    removeChild: noop,
    insertBefore: (n) => n,
    focus: noop,
    blur: noop,
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
    contains: () => false,
  };
}
function makeCanvas() {
  return { width: 800, height: 400, style: {}, classList, nodeType: 1, tagName: "canvas", ownerDocument: DOC, getContext: () => ctx, ...makeElExtras() };
}
function makeEl(tag) {
  return { tagName: tag, nodeName: tag, nodeType: 1, style: {}, classList, children: [], childNodes: [], clientWidth: 800, clientHeight: 400, ownerDocument: DOC, innerHTML: "", textContent: "", ...makeElExtras() };
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
  // 逐帧 rAF 调度器：支持真实取消语义、逐帧推进、嵌套 rAF 进入下一帧、暂停/恢复。
  // 替代旧 stub 的两个缺陷：cancelAnimationFrame=nooop（取消不生效）、
  // flush() 一次排干嵌套 rAF（把两帧压缩成一帧）。
  const raf = createRafScheduler();
  globalThis.requestAnimationFrame = raf.requestAnimationFrame;
  globalThis.cancelAnimationFrame = raf.cancelAnimationFrame;
  globalThis.__flushRaf = raf.flush; // 生产代码 flushChartLayout() 调用此 hook
  globalThis.__rafScheduler = raf; // 测试用例直接访问调度器
  globalThis.devicePixelRatio = 1;
  globalThis.matchMedia = () => mql;
  return function restore() {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete globalThis[k];
      else globalThis[k] = v;
    }
    delete globalThis.__flushRaf;
    delete globalThis.__rafScheduler;
  };
}

// 逐帧 rAF 调度器，模拟真实浏览器的 requestAnimationFrame 语义。
//
// 与旧 stub 的关键区别：
// - cancelAnimationFrame 真实生效：取消的回调不会在后续帧执行。
// - 每帧只执行该帧已排队的回调；嵌套 rAF（在回调内再调 rAF）进入下一帧，
//   而非在同一帧内排干。这精确模拟 suppressUntilFrame 的两帧窗口。
// - 支持暂停（模拟后台 rAF 节流）和恢复，让测试能控制回调执行顺序。
// - flush() 默认排干所有帧直到队列空（兼容旧测试的 __flushRaf 语义），
//   但可指定 maxFrames 限制推进帧数。
//
// 实现：pending = 下一帧的回调 ID 队列；nextPending = 下下帧的队列（仅在
// advanceFrame 执行期间被嵌套 rAF 填充）。advanceFrame 交换 pending→nextPending，
// 执行旧 pending 中的回调。回调内嵌套 rAF 进入新的 pending（即下一帧）。
function createRafScheduler() {
  let nextId = 1;
  let pending = []; // 下一帧的回调 ID 队列
  let nextPending = []; // 下下帧队列（仅 advanceFrame 执行期间填充）
  const callbacks = new Map(); // id -> cb
  let paused = false;
  let frameTime = 0;

  function requestAnimationFrame(cb) {
    const id = nextId++;
    callbacks.set(id, cb);
    pending.push(id);
    return id;
  }

  function cancelAnimationFrame(id) {
    callbacks.delete(id);
  }

  // 推进一帧：执行当前 pending 中的所有回调。回调内嵌套的 rAF 进入新的 pending。
  function advanceFrame() {
    if (paused) return;
    const current = pending;
    pending = nextPending;
    nextPending = [];
    frameTime += 16;
    for (const id of current) {
      const cb = callbacks.get(id);
      if (!cb) continue; // 已取消
      callbacks.delete(id);
      cb(frameTime); // 回调内可能嵌套 rAF，追加到 pending（下一帧）
    }
  }

  // 排干所有帧（兼容旧 __flushRaf 语义）。可指定 maxFrames 限制。
  function flush(maxFrames = 500) {
    let count = 0;
    while (pendingCallbacks() > 0 && count < maxFrames) {
      advanceFrame();
      count++;
    }
  }

  function pause() { paused = true; }
  function resume() { paused = false; }
  function isPaused() { return paused; }
  function pendingCallbacks() {
    return pending.length + nextPending.length;
  }

  return {
    requestAnimationFrame,
    cancelAnimationFrame,
    advanceFrame,
    flush,
    pause,
    resume,
    isPaused,
    pendingCallbacks,
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

// 模拟真实用户手势：在价格图容器上 dispatch pointerdown 事件。
// setupUserGestureTracking 监听 pointerdown/wheel/touchstart，递增 gestureGeneration
// 并设置 200ms 活跃窗口。测试调用此函数后，紧随的范围通知会被来源门控判定为
// 用户操作，允许 following→manual。
function dispatchGesture(group) {
  group.containers.price.dispatchEvent({ type: "pointerdown" });
}

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
    // 来源门控：必须先 dispatch 真实手势，否则程序性 setVisibleLogicalRange
    // 不会翻 manual（这正是修复的核心——区分用户操作与程序性通知）。
    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 30 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "manual", "suppress 不误伤窗口外的真实用户平移");
  } finally {
    restore();
  }
});

test("real controller: 5m scroll clamped at both edges (no blank beyond latest/oldest)", async () => {
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

// ============================================================================
// 后台/恢复生命周期回归：Issue #146
//
// Bug 触发假设：后台/恢复改变 rAF 与 LC 布局通知的相对顺序，使
// suppressUntilFrame 抑制窗口失效。具体时序：
//   1. setModel 追加新 K 线，设置 suppressUntilFrame（嵌套 rAF：帧A回调→帧B清除）
//   2. 后台导致 rAF 节流：帧A回调延迟执行，但最终仍先于 LC 布局通知完成
//   3. suppressUntilFrame 被清除（=null）
//   4. LC 随后发出"视图尚未跟进"的落后范围通知（to 指向旧右缘）
//   5. handler 检查 suppressUntilFrame===null → 不抑制 → 走 setManualRange
//   6. following 翻成 manual，视图钉在旧右缘
//
// 以下用例用逐帧 rAF 调度器精确模拟该时序，验证当前代码在特定帧顺序下的行为。
// ============================================================================

test("lifecycle: stale notification after suppress window clears does not flip to manual (source gating fix)", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);
    const raf = globalThis.__rafScheduler;

    // 初始：48 根 K 线，following 状态。
    group.setModel(createChartGroupModel(makeSnapshot(48), "five_minute"));
    globalThis.__flushRaf();
    await settle();
    assert.equal(
      reports[reports.length - 1].followState,
      "following",
      "初始应为 following",
    );

    // 追加 48->49：setModel 设置 suppressUntilFrame（嵌套 rAF）。
    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    // 此时 suppressUntilFrame !== null（两个嵌套 rAF 回调在队列中）。
    assert.notEqual(
      group.suppressUntilFrame,
      null,
      "setModel 后 suppressUntilFrame 应已设置",
    );

    // 模拟后台/恢复时序：先排干 suppressUntilFrame 的两帧回调（清除标记），
    // 再注入落后范围通知。这模拟"抑制窗口先过期、旧通知后到达"的竞态。
    // 帧 A：第一层 rAF 回调（调度第二层 rAF）
    raf.advanceFrame();
    assert.notEqual(
      group.suppressUntilFrame,
      null,
      "第一帧后 suppressUntilFrame 仍非 null（第二层 rAF 未执行）",
    );
    // 帧 B：第二层 rAF 回调（清除 suppressUntilFrame）
    raf.advanceFrame();
    assert.equal(
      group.suppressUntilFrame,
      null,
      "第二帧后 suppressUntilFrame 应已清除",
    );

    // 抑制窗口已过期：注入落后范围通知（to 指向旧 latest=47）。
    // 这模拟 LC 在后台/恢复后发出的"视图尚未跟进"过渡通知。
    // 注意：不 dispatch 手势——这是程序性通知，来源门控应阻止 following→manual。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 47 });
    globalThis.__flushRaf();
    await settle();

    // 来源门控修复后：程序性通知不翻 manual，保持 following。
    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "following",
      "来源门控：suppress 窗口过期后的程序性通知不应翻 manual",
    );
  } finally {
    restore();
  }
});

test("lifecycle: resyncPriceScaleAfterLayout delayed notification does not flip following (guarded)", async () => {
  // 验证 resyncPriceScaleAfterLayout 的 rAF 延迟 applyVisibleRange 不会翻 manual。
  // 该路径有 applyingViewportRange 守卫保护，不应触发 following→manual。
  // 但若 suppress 窗口已清除且守卫因任何原因失效，该延迟通知会成为触发源。
  // 修复后（来源门控），该路径应被来源门控覆盖，不依赖 applyingViewportRange。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);
    const raf = globalThis.__rafScheduler;

    // 初始：49 根 K 线，following 状态。
    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 追加 49->50：setModel 设置 suppressUntilFrame + schedulePriceScaleResync。
    group.setModel(createChartGroupModel(makeSnapshot(50), "five_minute"));
    assert.notEqual(group.suppressUntilFrame, null, "suppress 窗口应已设置");

    // 排干所有帧：suppressUntilFrame 清除 + resyncPriceScaleAfterLayout 执行。
    raf.flush();
    await settle();

    // 当前行为：resyncPriceScaleAfterLayout 的 applyVisibleRange 有
    // applyingViewportRange 守卫，其范围通知被抑制，保持 following。
    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "following",
      "resyncPriceScaleAfterLayout 延迟通知有 applyingViewportRange 守卫，应保持 following",
    );
  } finally {
    restore();
  }
});

test("lifecycle: consecutive setModel during paused rAF preserves suppress window", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);
    const raf = globalThis.__rafScheduler;

    // 初始：48 根 K 线，following 状态。
    group.setModel(createChartGroupModel(makeSnapshot(48), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 模拟后台：暂停 rAF 调度。
    raf.pause();
    assert.ok(raf.isPaused(), "rAF 应已暂停");

    // 后台期间连续追加两根 K 线（48->49->50）。
    // 每次 setModel 取消前一次 suppressUntilFrame 并重新设置。
    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    const suppressAfter49 = group.suppressUntilFrame;
    assert.notEqual(suppressAfter49, null, "第一次追加应设置 suppress 窗口");

    group.setModel(createChartGroupModel(makeSnapshot(50), "five_minute"));
    const suppressAfter50 = group.suppressUntilFrame;
    assert.notEqual(suppressAfter50, null, "第二次追加应重新设置 suppress 窗口");
    assert.notEqual(
      suppressAfter50,
      suppressAfter49,
      "第二次 setModel 应取消前一次的 suppress 窗口并重新设置",
    );

    // 恢复 rAF：排干所有帧（suppressUntilFrame 的嵌套回调执行并清除）。
    raf.resume();
    globalThis.__flushRaf();
    await settle();

    // 恢复后 suppressUntilFrame 应已清除。
    assert.equal(
      group.suppressUntilFrame,
      null,
      "恢复排干后 suppressUntilFrame 应已清除",
    );

    // 后台期间未注入落后通知，恢复后应保持 following。
    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "following",
      "无落后通知时恢复后应保持 following",
    );
    assert.equal(
      last.range.to,
      49,
      "视图应已跟进到最新 latest=49（length-1）",
    );
  } finally {
    restore();
  }
});

test("lifecycle: stale notification after restore with paused rAF does not flip to manual (source gating fix)", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);
    const raf = globalThis.__rafScheduler;

    // 初始：48 根 K 线，following 状态。
    group.setModel(createChartGroupModel(makeSnapshot(48), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 模拟后台：暂停 rAF。
    raf.pause();

    // 后台期间追加 K 线（48->49），suppressUntilFrame 被设置但 rAF 暂停未执行。
    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    assert.notEqual(
      group.suppressUntilFrame,
      null,
      "后台追加应设置 suppress 窗口（rAF 暂停未执行）",
    );

    // 恢复 rAF：先排干 suppressUntilFrame 的两帧回调（清除标记）。
    raf.resume();
    raf.advanceFrame(); // 第一层 rAF
    raf.advanceFrame(); // 第二层 rAF → suppressUntilFrame = null
    assert.equal(
      group.suppressUntilFrame,
      null,
      "恢复排干两帧后 suppress 应已清除",
    );

    // 恢复后 LC 发出落后范围通知（to 指向旧 latest=47）。
    // 这模拟恢复后 LC 布局重新计算时发出的过渡通知。
    // 注意：不 dispatch 手势——这是程序性通知，来源门控应阻止 following→manual。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 47 });
    globalThis.__flushRaf();
    await settle();

    // 来源门控修复后：程序性通知不翻 manual，保持 following。
    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "following",
      "来源门控：恢复后的程序性通知不应翻 manual",
    );
  } finally {
    restore();
  }
});

// ============================================================================
// 恢复语义回归（Issue #146 第 4 步）
//
// onBackgroundEnter() 保存 pre-background 视口快照；onForegroundRestore() 基于
// 快照决定恢复行为：
//   - following → 重新右对齐最新 K 线
//   - manual → 保持原手动范围，按最新数据边界合法 clamp
// ============================================================================

test("restore semantics: following before background re-aligns to latest after restore", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    // 初始：48 根 K 线，following 状态。
    group.setModel(createChartGroupModel(makeSnapshot(48), "five_minute"));
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "following");

    // 模拟后台：保存快照。
    group.onBackgroundEnter();

    // 后台期间追加 4 根新 K 线（48->52），模拟跨过 4 根 5 分钟 K 线。
    group.setModel(createChartGroupModel(makeSnapshot(52), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 恢复前台：应基于保存的 following 状态重新右对齐到最新 K 线。
    group.onForegroundRestore();
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following", "恢复后应保持 following");
    assert.equal(last.range.to, 51, "恢复后应右对齐到最新 latest=51");
  } finally {
    restore();
  }
});

test("restore semantics: manual before background preserves range after restore", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    // 初始：49 根 K 线，following 状态。
    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 用户主动平移到 manual（dispatch 手势让来源门控允许切换）。
    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 30 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");

    // 模拟后台：保存快照（manual 状态）。
    group.onBackgroundEnter();

    // 后台期间追加 3 根新 K 线（49->52）。
    group.setModel(createChartGroupModel(makeSnapshot(52), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 恢复前台：应保持 manual，不强制跳回最新。
    group.onForegroundRestore();
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "manual", "恢复后应保持 manual，不跳回 following");
    // 原手动范围 from=0 应保留（数据增长不改变旧范围左端）。
    assert.equal(last.range.from, 0, "恢复后原手动范围左端应保留");
  } finally {
    restore();
  }
});
