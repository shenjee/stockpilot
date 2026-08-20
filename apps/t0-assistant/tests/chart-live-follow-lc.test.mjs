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
// 全局元素注册表：记录所有创建的 DOM 元素，用于 findDescendantWithListener
// 查找 LC 插入到 price 容器的子元素（.tv-lightweight-charts）。
const ALL_ELEMENTS = [];
// 每个元素独立的事件监听器表，支持 addEventListener/removeEventListener/dispatchEvent。
// setupUserGestureTracking 在 price 容器上注册 pointerdown/wheel 监听器，在
// document 上注册 pointerup/pointercancel 监听器（覆盖容器外释放）。
// 测试用 dispatchGesture(priceContainer) 模拟真实用户手势，触发来源门控。
//
// 支持捕获/冒泡阶段传播：addEventListener 的第三参 options.capture=true 时，
// 回调记录为 capture 阶段。dispatchEvent 从根（document）到目标执行 capture，
// 再从目标到根执行冒泡——模拟真实 DOM 事件顺序。这让 wheel capture 测试能
// 验证"外层 capture handler 先于 LC 子元素 bubble handler 执行"的真实顺序。
function makeElExtras() {
  const listeners = new Map(); // type -> Set<{cb, capture}>
  const self = {
    setAttribute: noop,
    getAttribute: () => null,
    toggleAttribute: noop,
    addEventListener(type, cb, options) {
      const capture = typeof options === "boolean" ? options : !!(options && options.capture);
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add({ cb, capture });
    },
    removeEventListener(type, cb, options) {
      const capture = typeof options === "boolean" ? options : !!(options && options.capture);
      const set = listeners.get(type);
      if (!set) return;
      for (const entry of set) {
        if (entry.cb === cb && entry.capture === capture) {
          set.delete(entry);
          return;
        }
      }
    },
    // 捕获/冒泡传播：从 document 根到目标（capture），再从目标到根（bubble）。
    // 只执行匹配阶段的回调。这让 wheel capture 测试覆盖真实事件顺序。
    dispatchEvent(evt) {
      const type = evt?.type;
      if (!type) return true;
      // 构建从目标到 document 根的祖先链。
      const chain = [];
      let node = self.__parent || null;
      while (node) {
        chain.unshift(node);
        node = node.__parent || null;
      }
      // capture 阶段：根→目标
      for (const ancestor of chain) {
        for (const { cb, capture } of ancestor.__getListeners(type) ?? []) {
          if (capture) cb.call(ancestor, evt);
        }
      }
      // 目标阶段：目标上的 capture + bubble 回调都执行
      for (const { cb } of listeners.get(type) ?? []) {
        cb.call(self, evt);
      }
      // 冒泡阶段：目标→根
      for (let i = chain.length - 1; i >= 0; i--) {
        const ancestor = chain[i];
        for (const { cb, capture } of ancestor.__getListeners(type) ?? []) {
          if (!capture) cb.call(ancestor, evt);
        }
      }
      return true;
    },
    __getListeners(type) {
      return listeners.get(type);
    },
    appendChild: (c) => {
      if (c) c.__parent = self;
      return c;
    },
    removeChild: noop,
    insertBefore: (n) => {
      if (n) n.__parent = self;
      return n;
    },
    focus: noop,
    blur: noop,
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
    contains: () => false,
  };
  return self;
}
// 将 makeElExtras 的方法直接附加到目标对象上，确保闭包中的 self 引用
// 指向实际元素对象（而非 makeElExtras 返回的中间对象）。
// 这对 __parent 链至关重要：appendChild 设置 c.__parent = self，self 必须
// 是实际元素，findDescendantWithListener 才能通过 __parent 链找到后代。
function attachElExtras(el) {
  const listeners = new Map(); // type -> Set<{cb, capture}>
  const self = {
    setAttribute: noop,
    getAttribute: () => null,
    toggleAttribute: noop,
    addEventListener(type, cb, options) {
      const capture = typeof options === "boolean" ? options : !!(options && options.capture);
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add({ cb, capture });
    },
    removeEventListener(type, cb, options) {
      const capture = typeof options === "boolean" ? options : !!(options && options.capture);
      const set = listeners.get(type);
      if (!set) return;
      for (const entry of set) {
        if (entry.cb === cb && entry.capture === capture) {
          set.delete(entry);
          return;
        }
      }
    },
    dispatchEvent(evt) {
      const type = evt?.type;
      if (!type) return true;
      const chain = [];
      let node = el.__parent || null;
      while (node) {
        chain.unshift(node);
        node = node.__parent || null;
      }
      for (const ancestor of chain) {
        for (const { cb, capture } of ancestor.__getListeners?.(type) ?? []) {
          if (capture) cb.call(ancestor, evt);
        }
      }
      for (const { cb } of listeners.get(type) ?? []) {
        cb.call(el, evt);
      }
      for (let i = chain.length - 1; i >= 0; i--) {
        const ancestor = chain[i];
        for (const { cb, capture } of ancestor.__getListeners?.(type) ?? []) {
          if (!capture) cb.call(ancestor, evt);
        }
      }
      return true;
    },
    __getListeners(type) {
      return listeners.get(type);
    },
    appendChild: (c) => {
      if (c) c.__parent = el;
      return c;
    },
    removeChild: noop,
    insertBefore: (n) => {
      if (n) n.__parent = el;
      return n;
    },
    focus: noop,
    blur: noop,
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
    contains: () => false,
  };
  Object.assign(el, self);
  return el;
}
function makeCanvas() {
  const el = attachElExtras({ width: 800, height: 400, style: {}, classList, nodeType: 1, tagName: "canvas", ownerDocument: DOC, getContext: () => ctx });
  ALL_ELEMENTS.push(el);
  return el;
}
function makeEl(tag) {
  const el = attachElExtras({ tagName: tag, nodeName: tag, nodeType: 1, style: {}, classList, children: [], childNodes: [], clientWidth: 800, clientHeight: 400, ownerDocument: DOC, innerHTML: "", textContent: "" });
  ALL_ELEMENTS.push(el);
  return el;
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
  ALL_ELEMENTS.length = 0; // 每个测试独立的元素注册表
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
  DOC = attachElExtras({
    createElement: (t) => (t === "canvas" ? makeCanvas() : makeEl(t)),
    createElementNS: (_n, t) => (t === "canvas" ? makeCanvas() : makeEl(t)),
    documentElement: null,
    body: null,
    defaultView: null,
  });
  DOC.documentElement = makeEl("html");
  DOC.documentElement.ownerDocument = DOC;
  DOC.body = makeEl("body");
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

async function makeGroup(reports, options = {}) {
  const { SynchronizedChartGroup } = await import(
    "../renderer/src/charts/SynchronizedChartGroup.ts"
  );
  const { createChartGroupModel } = await import("../renderer/src/charts/chart-model.mjs");
  const group = new SynchronizedChartGroup({
    containers: { price: makeEl("div"), volume: makeEl("div"), macd: makeEl("div") },
    kind: "five_minute",
    appendFollowPolicy: options.appendFollowPolicy ?? "preserve",
    datasetIdentity: options.datasetIdentity ?? null,
    initialViewport: options.initialViewport ?? null,
    onViewportChange: (snap) => reports.push(snap),
  });
  globalThis.__flushRaf();
  return { group, createChartGroupModel };
}

const settle = () => new Promise((r) => setTimeout(r, 150)); // 越过 onViewportChange 的 120ms 防抖

// 模拟真实用户拖动手势：dispatch 完整 pointer 生命周期 (down→move→up)。
// setupUserGestureTracking 监听 pointerdown/pointermove（容器）+ pointerup/cancel
// （document）。只有 pointermove 确认有效移动后才设活跃尾窗口，授权范围通知。
// 这取代了旧的 pointerdown-only + 200ms 时间窗口方案。
// buttons=1 表示左键按下（P2 修复：pointermove 检查 buttons 防止释放后误授权）。
function dispatchGesture(group) {
  const el = group.containers.price;
  el.dispatchEvent({ type: "pointerdown", buttons: 1 });
  el.dispatchEvent({ type: "pointermove", buttons: 1 });
  // pointerup 在 document 上监听（P2：覆盖容器外释放）。
  document.dispatchEvent({ type: "pointerup", buttons: 0 });
}

// 模拟真实用户滚轮缩放：dispatch wheel 事件。
// wheel 使用可消费 token：每次事件 +1，每次范围通知消费 1 个。
function dispatchWheel(group) {
  group.containers.price.dispatchEvent({ type: "wheel" });
}

// 模拟真实用户单击（pointerdown→pointerup，无 pointermove）。
// 单击不拖动不应授权范围变化——这是 200ms 时间窗口方案的误判场景之一。
function dispatchClick(group) {
  const el = group.containers.price;
  el.dispatchEvent({ type: "pointerdown", buttons: 1 });
  document.dispatchEvent({ type: "pointerup", buttons: 0 });
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
  // 默认 appendFollowPolicy=preserve（回放/关闭路径）：后台期间新增 K 仍保留手工范围。
  // 实盘开启强制贴右的对应行为见 Issue #148 用例。
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

// ============================================================================
// 来源门控修正回归（review P1 #1）
//
// 旧方案：pointerdown 后固定 200ms 时间窗口内所有范围通知视为用户操作。
// 两种误判：
//   1. 长按超过 200ms 后再拖动——真实拖动被当成程序性通知，无法进入 manual。
//   2. 单击但未拖动时，200ms 内恰好到达的程序性范围通知被误判为用户操作。
//
// 新方案：完整 pointer 手势生命周期 + wheel 可消费 token。
//   - pointerdown 仅标记开始，不授权（单击不拖动不授权）。
//   - pointermove 确认有效移动后才授权（长按后拖动仍授权）。
//   - wheel token 可消费，不依赖时间窗口。
// ============================================================================

test("source gating: long press then drag still flips to manual (no 200ms window cutoff)", async () => {
  // 旧方案误判 #1：pointerdown 后 200ms 窗口过期，真实拖动被当成程序性通知。
  // 新方案：pointermove 确认有效移动后才授权，不受时间窗口限制。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 模拟长按：pointerdown 后等待超过旧方案的 200ms 窗口。
    const el = group.containers.price;
    el.dispatchEvent({ type: "pointerdown", buttons: 1 });
    // 等待 250ms（超过旧 200ms 窗口）。
    await new Promise((r) => setTimeout(r, 250));

    // 然后拖动：pointermove + pointerup（pointerup 在 document 上监听）。
    el.dispatchEvent({ type: "pointermove", buttons: 1 });
    document.dispatchEvent({ type: "pointerup", buttons: 0 });

    // 紧随的范围通知应被授权为用户操作。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 30 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "manual",
      "长按后拖动应翻 manual（新方案不依赖 pointerdown 后的时间窗口）",
    );
  } finally {
    restore();
  }
});

test("source gating: single click without drag does not authorize programmatic notification", async () => {
  // 旧方案误判 #2：单击（pointerdown→pointerup 无 move）后 200ms 内到达的
  // 程序性范围通知被误判为用户操作。新方案：单击不设活跃窗口，不授权。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 单击：pointerdown→pointerup，无 pointermove。
    dispatchClick(group);

    // 紧随的程序性范围通知（模拟 LC 布局副作用）。
    // 旧方案：200ms 内会被误判为用户操作 → 翻 manual。
    // 新方案：单击不设活跃窗口 → 保持 following。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 47 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "following",
      "单击不拖动不应授权程序性通知翻 manual",
    );
  } finally {
    restore();
  }
});

test("source gating: wheel zoom uses consumable token (consumed synchronously, no time window)", async () => {
  // wheel 使用可消费 token：每次 wheel 事件 +1，每次范围通知消费 1 个。
  // LC 在 wheel handler 内同步触发范围回调，token 在同一事件分发内被消费——
  // 不依赖时间窗口。注意：setTimeout(0) 清理会在事件结束后清除未消费 token
  // （边界 wheel 无范围变化的场景），因此范围通知必须在同一事件循环内触发。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // wheel 事件：capture handler 设 token。
    dispatchWheel(group);
    assert.equal(group.wheelGestureTokens, 1, "wheel 后 token 应为 1");

    // 范围通知在同一事件循环内触发：消费 token，翻 manual（不依赖时间窗口）。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 30 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "manual",
      "wheel token 同步消费，应翻 manual（不依赖时间窗口）",
    );
    assert.equal(
      group.wheelGestureTokens,
      0,
      "范围通知应消费 token",
    );
  } finally {
    restore();
  }
});

test("source gating: wheel token consumed by one notification (second notification not authorized)", async () => {
  // 验证 token 可消费性：一次 wheel 只授权一次范围通知。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 一次 wheel。
    dispatchWheel(group);

    // 第一次范围通知：消费 token，翻 manual。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 30 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");

    // 第二次范围通知：token 已耗尽，不应被授权。
    // 但当前已是 manual 状态，程序性通知仍走 setManualRange clamp。
    // 验证 token 耗尽：group.wheelGestureTokens 应为 0。
    assert.equal(
      group.wheelGestureTokens,
      0,
      "wheel token 应已被第一次通知消费",
    );
  } finally {
    restore();
  }
});

// ============================================================================
// manual 恢复使用保存范围回归（review P1 #2）
//
// 旧方案：onForegroundRestore 的 manual 分支使用 this.viewport.visibleStart/End
// （当前范围），但后台期间程序性通知可能已漂移当前范围。
// 新方案：使用 fromChartLogicalRange(saved.range, length) 恢复保存的 pre-background 范围。
// ============================================================================

test("restore semantics: manual range drifts during background, restore uses saved range not current", async () => {
  // 验证恢复时使用保存的 saved.range 而非当前漂移后的范围。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    // 初始：80 根 K 线，following 状态。
    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 用户主动平移到 manual，范围 [10, 57]（span=48，满足 MANUAL_MIN_VISIBLE_BARS_5M）。
    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 10, to: 57 });
    globalThis.__flushRaf();
    await settle();
    const beforeBg = reports[reports.length - 1];
    assert.equal(beforeBg.followState, "manual");
    assert.equal(beforeBg.range.from, 10, "用户平移后 from=10");
    assert.equal(beforeBg.range.to, 57, "用户平移后 to=57");

    // 模拟后台：保存快照（manual，range [10, 57]）。
    group.onBackgroundEnter();
    const savedRange = group.preBackgroundViewport.range;
    assert.deepEqual(savedRange, { from: 10, to: 57 }, "快照应保存 [10, 57]");

    // 后台期间追加 5 根新 K 线（80->85）。
    // applyModel 在 manual 下会 clamp 当前范围到新长度，但 [10, 57] 在 85 长度内
    // 不会被裁剪，当前范围仍为 [10, 57]。
    group.setModel(createChartGroupModel(makeSnapshot(85), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 模拟后台期间程序性通知漂移当前范围（manual 状态下程序性通知走 setManualRange）。
    // 注入一条程序性范围通知，将当前范围漂移到 [20, 67]（span=48，满足最小值）。
    // 来源门控：不 dispatch 手势 → 程序性通知 → manual 状态下仍走 setManualRange clamp。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 20, to: 67 });
    globalThis.__flushRaf();
    await settle();
    const drifted = reports[reports.length - 1];
    assert.equal(drifted.range.from, 20, "后台期间范围漂移到 from=20");

    // 恢复前台：应使用保存的 [10, 57] 而非漂移后的 [20, 67]。
    group.onForegroundRestore();
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "manual", "恢复后应保持 manual");
    assert.equal(
      last.range.from,
      10,
      "恢复应使用保存的 from=10，而非漂移后的 from=20",
    );
    assert.equal(
      last.range.to,
      57,
      "恢复应使用保存的 to=57，而非漂移后的 to=67",
    );
  } finally {
    restore();
  }
});

// ============================================================================
// 重复 background 事件不覆盖快照回归（review P2 #3）
//
// main.mjs 会分别发送 blur 和 minimize 两个 background 事件。
// 旧方案：每次 onBackgroundEnter 都覆盖 preBackgroundViewport。
// 新方案：首次进入后台保存一次，直到 onForegroundRestore 消费后才允许再次保存。
// ============================================================================

test("restore semantics: duplicate background events do not overwrite first snapshot", async () => {
  // 模拟 blur + minimize 两个 background 事件，验证第二个不覆盖第一个快照。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    // 初始：48 根 K 线，following 状态。
    group.setModel(createChartGroupModel(makeSnapshot(48), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 第一个 background 事件（blur）：保存快照。
    group.onBackgroundEnter();
    const firstSnapshot = group.preBackgroundViewport;
    assert.notEqual(firstSnapshot, null, "首次 background 应保存快照");
    assert.equal(firstSnapshot.followState, "following");

    // 后台期间追加 K 线（48->52）。
    group.setModel(createChartGroupModel(makeSnapshot(52), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 第二个 background 事件（minimize）：不应覆盖第一个快照。
    group.onBackgroundEnter();
    const secondSnapshot = group.preBackgroundViewport;
    assert.equal(
      secondSnapshot,
      firstSnapshot,
      "重复 background 事件不应覆盖首次快照",
    );
    // 快照仍为 following（首次保存时的状态），而非追加后的当前状态。
    assert.equal(
      secondSnapshot.followState,
      "following",
      "快照应保持首次保存时的状态",
    );

    // 恢复前台：应基于首次快照恢复。
    group.onForegroundRestore();
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following", "恢复后应保持 following");
    assert.equal(last.range.to, 51, "恢复后应右对齐到最新 latest=51");
    // 恢复后快照应已消费（null），允许下次 background 重新保存。
    assert.equal(
      group.preBackgroundViewport,
      null,
      "恢复后快照应已消费，允许下次 background 重新保存",
    );
  } finally {
    restore();
  }
});

test("restore semantics: background → background → foreground preserves first snapshot", async () => {
  // 更完整的 background→background→foreground 生命周期回归。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    // 初始：80 根 K 线，用户手动平移到 [5, 52]（span=48，满足最小值）。
    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();
    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 5, to: 52 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");

    // 第一个 background（blur）：保存快照 [5, 52]。
    group.onBackgroundEnter();
    assert.deepEqual(
      group.preBackgroundViewport.range,
      { from: 5, to: 52 },
      "首次快照应保存 [5, 52]",
    );

    // 后台期间追加 K 线（80->86）。
    group.setModel(createChartGroupModel(makeSnapshot(86), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 第二个 background（minimize）：不覆盖。
    group.onBackgroundEnter();
    assert.deepEqual(
      group.preBackgroundViewport.range,
      { from: 5, to: 52 },
      "重复 background 不应覆盖快照",
    );

    // 恢复前台：应使用保存的 [5, 52]。
    group.onForegroundRestore();
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "manual", "恢复后应保持 manual");
    assert.equal(last.range.from, 5, "恢复应使用保存的 from=5");
    assert.equal(last.range.to, 52, "恢复应使用保存的 to=52");
  } finally {
    restore();
  }
});

// ============================================================================
// P1 回归：wheel token 注册晚于 LC 范围回调（capture 修复）
//
// Bug：wheel 监听器注册在外层 price 容器（bubble），LC 在子元素上处理 wheel
// 并同步触发范围回调。事件顺序：LC 子元素 handler（触发回调）→ 外层 handler
// （设 token）。范围回调执行时 token 仍为 0，真实滚轮缩放被判为程序性通知。
//
// 修复：外层使用 capture，在 LC handler 前建立授权。
// 测试：从 LC 子元素派发 wheel，验证 capture handler 先于 LC handler 执行，
// 真实滚轮缩放能翻 manual。
// ============================================================================

test("source gating: wheel from LC child element authorizes via capture (P1)", async () => {
  // 模拟真实事件顺序：LC 子元素处理 wheel 并同步触发范围回调。
  // 修复前（bubble）：回调先于 token 设置 → 真实缩放被判程序性 → 保持 following。
  // 修复后（capture）：外层 capture handler 先执行 → token 已设 → 翻 manual。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 找到 LC 插入到 price 容器的子元素（.tv-lightweight-charts）。
    // LC 在 createChart 时将子元素 appendChild 到容器。
    const priceContainer = group.containers.price;
    // 测试 stub 的 appendChild 设置 __parent，所以 LC 子元素的 __parent 是容器。
    // 遍历容器的 DOM 子树找到有 wheel 监听器的后代。
    const lcChild = findDescendantWithListener(priceContainer, "wheel");
    assert.ok(lcChild, "应找到 LC 子元素上的 wheel 监听器");

    // 从 LC 子元素派发 wheel：模拟真实事件冒泡顺序。
    // capture 阶段：容器 capture handler 先执行（设 token）。
    // 目标/bubble 阶段：LC 子元素 handler 同步执行并触发范围回调（消费 token）。
    // deltaX/deltaY=0 让 LC 的 _onMousewheel 早返回（无真实 canvas 渲染）。
    // 测试环境中 LC 内部 handler 不一定在当前事件分发内同步触发范围回调。
    // 直接在 LC 子元素 wheel handler 中同步调用 viewportRangeHandler，
    // 模拟目标阶段同步范围通知，验证 capture handler 先于它执行。
    assert.equal(
      typeof group.viewportRangeHandler,
      "function",
      "应在测试时能访问 LC 的可见范围回调处理器",
    );
    lcChild.addEventListener("wheel", () => {
      group.viewportRangeHandler?.({ from: 0, to: 30 });
    });
    lcChild.dispatchEvent({ type: "wheel", deltaX: 0, deltaY: 0 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "manual",
      "wheel 从 LC 子元素派发时，capture 应先设 token，真实缩放应翻 manual",
    );
  } finally {
    restore();
  }
});

test("source gating: boundary wheel with no range change cleans up token (P1)", async () => {
  // 验证 token 清理：边界 wheel（无范围变化）不留下永久 token。
  // 修复：setTimeout(0) 在事件循环结束后清理未消费 token。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // wheel 事件但不触发范围变化（无 setVisibleLogicalRange 调用）。
    dispatchWheel(group);
    // token 应为 1（刚设置）。
    assert.equal(group.wheelGestureTokens, 1, "wheel 后 token 应为 1");

    // 等待 setTimeout(0) 清理执行。
    await new Promise((r) => setTimeout(r, 10));

    // token 应被清理为 0（无范围回调消费）。
    assert.equal(
      group.wheelGestureTokens,
      0,
      "边界 wheel 无范围变化后，token 应被 setTimeout(0) 清理",
    );

    // 后续程序性通知不应被授权（token 已清理）。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 47 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "following",
      "清理后程序性通知不应翻 manual",
    );
  } finally {
    restore();
  }
});

// ============================================================================
// P2 回归：容器外释放指针留下永久 active 状态
//
// Bug：pointerup/pointercancel 只在 price 容器监听。用户拖出图表后在容器外释放，
// 容器收不到 pointerup，pointerGestureActive 永久为 true。鼠标重新进入图表时，
// 即使无按键，pointermove 仍会重新开启授权窗口。
//
// 修复：pointerup/pointercancel 在 document 上监听；pointermove 检查 buttons。
// ============================================================================

test("source gating: pointer released outside container does not leave permanent active (P2)", async () => {
  // 模拟拖出容器后释放：pointerdown + pointermove 在容器内，pointerup 在
  // document 上（容器外）。修复前 pointerup 在容器上监听 → 收不到 → 永久 active。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(49), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // pointerdown + pointermove 在容器内（开始拖动）。
    const el = group.containers.price;
    el.dispatchEvent({ type: "pointerdown", buttons: 1 });
    el.dispatchEvent({ type: "pointermove", buttons: 1 });
    assert.ok(
      group.pointerGestureActive,
      "拖动中 pointerGestureActive 应为 true",
    );

    // 拖出容器后在容器外释放：pointerup 派发到 document（非容器）。
    document.dispatchEvent({ type: "pointerup", buttons: 0 });
    assert.equal(
      group.pointerGestureActive,
      false,
      "容器外释放后 pointerGestureActive 应为 false（document 监听 pointerup）",
    );

    // 鼠标重新进入图表但无按键：pointermove buttons=0 不应重新开启授权窗口。
    el.dispatchEvent({ type: "pointermove", buttons: 0 });
    assert.equal(
      group.pointerGestureActive,
      false,
      "无按键时 pointermove 不应重新激活手势",
    );

    // pointerup 时 pointerMoved=true 会刷新 300ms 尾窗口（覆盖 LC 延迟通知）。
    // 等待尾窗口过期后，程序性通知才不应被授权。
    await new Promise((r) => setTimeout(r, 350));

    // 后续程序性通知不应被授权（无活跃手势 + 无 token + 尾窗口已过期）。
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 47 });
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(
      last.followState,
      "following",
      "容器外释放后程序性通知不应翻 manual",
    );
  } finally {
    restore();
  }
});

// ============================================================================
// P3 回归：首次后台快照日志错误标记为 skipped
//
// Bug：onBackgroundEnter() 的 skipped 在保存快照后才计算，首次成功保存时
// 也输出 skipped: true。
// 修复：在写入前保存 alreadySaved。
// ============================================================================

test("restore semantics: first background-enter logs skipped=false (P3)", async () => {
  // 验证首次 background-enter 的日志正确标记 skipped=false。
  // 用 console.log spy 捕获日志输出。
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports);

    group.setModel(createChartGroupModel(makeSnapshot(48), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 捕获 console.log 输出。
    const logs = [];
    const origLog = console.log;
    console.log = (...args) => logs.push(args);

    try {
      // 首次 background-enter：应保存快照，日志 skipped=false。
      group.onBackgroundEnter();
    } finally {
      console.log = origLog;
    }

    // 找到 background-enter 日志条目。
    const bgLog = logs.find((l) => l[1] === "background-enter");
    assert.ok(bgLog, "应有 background-enter 日志");
    const extra = bgLog[2];
    assert.equal(
      extra.skipped,
      false,
      "首次 background-enter 应 skipped=false（修复前错误地为 true）",
    );
    assert.equal(
      extra.savedFollowState,
      "following",
      "首次应保存 following 状态",
    );

    // 第二个 background-enter：不覆盖，日志 skipped=true。
    const logs2 = [];
    console.log = (...args) => logs2.push(args);
    try {
      group.onBackgroundEnter();
    } finally {
      console.log = origLog;
    }
    const bgLog2 = logs2.find((l) => l[1] === "background-enter");
    const extra2 = bgLog2[2];
    assert.equal(
      extra2.skipped,
      true,
      "重复 background-enter 应 skipped=true",
    );
  } finally {
    restore();
  }
});

// ============================================================================
// Issue #148：实盘 5 分钟新增真实 K 强制贴右
//
// appendFollowPolicy=force-follow-latest 时：
//   - 稳定向前追加打断 manual（含最新端缩放与向左翻历史）
//   - 同根 tick / prepend 不触发
//   - 同股票 Live Session 替换走首次加载，不继承旧 manual
//   - 后台期间最后时间戳前进或 Session identity 变化则恢复时忽略旧 manual
//   - 后台无新 K 且 Session 未变仍保留手工范围
// ============================================================================

test("issue 148: live append from edge-zoomed manual forces follow and right-aligns", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports, {
      appendFollowPolicy: "force-follow-latest",
    });

    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // 最新端缩放到约 48 根 → manual（仍贴边）。
    dispatchWheel(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 32, to: 79 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");

    group.setModel(createChartGroupModel(makeSnapshot(81), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following", "新 K 应切回 following");
    assert.equal(last.range.to, 80, "最新索引应为可见范围 to");
    const span = last.range.to - last.range.from + 1;
    assert.ok(
      span >= 72,
      `应按 following 密度重算 N（≥72），不保留手工 48 跨度；实际 span=${span}`,
    );
    assert.notEqual(last.range.from, 32, "不应保留手工左端");
  } finally {
    restore();
  }
});

test("issue 148: live append while browsing history forces follow", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports, {
      appendFollowPolicy: "force-follow-latest",
    });

    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 0, to: 47 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");
    assert.equal(reports[reports.length - 1].range.from, 0);

    group.setModel(createChartGroupModel(makeSnapshot(81), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following");
    assert.equal(last.range.to, 80);
    assert.ok(last.range.from > 0, "不应停留在历史左端");
  } finally {
    restore();
  }
});

test("issue 148: same-length live tick does not force follow from manual", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports, {
      appendFollowPolicy: "force-follow-latest",
    });

    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 10, to: 57 });
    globalThis.__flushRaf();
    await settle();
    const before = reports[reports.length - 1];
    assert.equal(before.followState, "manual");

    // 同长度刷新（动态 K tick）：不触发强制贴右。
    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "manual");
    assert.equal(last.range.from, before.range.from);
    assert.equal(last.range.to, before.range.to);
  } finally {
    restore();
  }
});

test("issue 148: history prepend does not force follow from manual", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports, {
      appendFollowPolicy: "force-follow-latest",
    });

    const base = makeSnapshot(60);
    group.setModel(createChartGroupModel(base, "five_minute"));
    globalThis.__flushRaf();
    await settle();

    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 5, to: 52 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");

    // 左侧 prepend：length↑，但旧序列不是新序列前缀（前缀是更早交易日）。
    const earlyBars = [];
    const earlyBase = new Date("2026-01-04T09:30:00").getTime();
    for (let i = 0; i < 10; i += 1) {
      const t = new Date(earlyBase + i * 5 * 60000);
      const hh = String(t.getHours()).padStart(2, "0");
      const mm = String(t.getMinutes()).padStart(2, "0");
      earlyBars.push({
        timestamp: `2026-01-04 ${hh}:${mm}:00`,
        open: 10,
        high: 11,
        low: 9,
        close: 10.5,
        volume: 1000,
        closed: true,
      });
    }
    const prependedSnapshot = {
      ...base,
      market: {
        ...base.market,
        bars_5m: [...earlyBars, ...base.market.bars_5m],
      },
    };
    group.setModel(createChartGroupModel(prependedSnapshot, "five_minute"));
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "manual", "prepend 不得强制 following");
    // 手工范围在 prepend 后按 applyModel 夹紧；不应跳到最新端。
    assert.ok(last.range.to < 69, "不应贴到 prepend 后的最新端");
  } finally {
    restore();
  }
});

test("issue 148: background manual + new bars restores to latest", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports, {
      appendFollowPolicy: "force-follow-latest",
    });

    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 10, to: 57 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");

    group.onBackgroundEnter();
    group.setModel(createChartGroupModel(makeSnapshot(85), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    // setModel 已因 force-follow-latest 贴右；恢复仍不得用旧 manual 覆盖。
    group.onForegroundRestore();
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following");
    assert.equal(last.range.to, 84);
  } finally {
    restore();
  }
});

test("issue 148: background manual without new bars keeps manual range", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports, {
      appendFollowPolicy: "force-follow-latest",
    });

    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 10, to: 57 });
    globalThis.__flushRaf();
    await settle();
    const before = reports[reports.length - 1];
    assert.equal(before.followState, "manual");

    group.onBackgroundEnter();
    // 同长度 tick：最后时间戳不变。
    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    group.onForegroundRestore();
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "manual");
    assert.equal(last.range.from, before.range.from);
    assert.equal(last.range.to, before.range.to);
  } finally {
    restore();
  }
});

test("issue 148: same-stock live session replace does not inherit old manual", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports, {
      appendFollowPolicy: "force-follow-latest",
      datasetIdentity: "live-session-a",
      // 模拟 React 仍持有上一 Session 的手工快照；Session 替换不得消费它。
      initialViewport: {
        followState: "manual",
        range: { from: 10, to: 57 },
      },
    });

    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 10, to: 57 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");

    // 同股票重建 Live Session：整段序列可相同或不同，但 identity 已变。
    // 不得走 applyModel 保留旧 manual；应首次加载跟随最新。
    const replaced = makeSnapshot(80);
    replaced.market.bars_5m = replaced.market.bars_5m.map((bar, index) => ({
      ...bar,
      close: 20 + index * 0.01,
    }));
    group.setDatasetIdentity("live-session-b");
    group.setModel(createChartGroupModel(replaced, "five_minute"));
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following", "Session 替换应走首次加载");
    assert.equal(last.range.to, 79, "应贴到最新端");
    assert.notEqual(last.range.from, 10, "不得继承旧手工左端");
  } finally {
    restore();
  }
});

test("issue 148: background session replace restores to latest", async () => {
  const restore = installDom();
  try {
    const reports = [];
    const { group, createChartGroupModel } = await makeGroup(reports, {
      appendFollowPolicy: "force-follow-latest",
      datasetIdentity: "live-session-a",
    });

    group.setModel(createChartGroupModel(makeSnapshot(80), "five_minute"));
    globalThis.__flushRaf();
    await settle();

    dispatchGesture(group);
    group.priceChart.timeScale().setVisibleLogicalRange({ from: 10, to: 57 });
    globalThis.__flushRaf();
    await settle();
    assert.equal(reports[reports.length - 1].followState, "manual");

    group.onBackgroundEnter();
    // 后台期间 Session 替换且时间戳未前进（同长度整段替换）。
    const replaced = makeSnapshot(80);
    replaced.market.bars_5m = replaced.market.bars_5m.map((bar) => ({
      ...bar,
      open: bar.open + 1,
    }));
    group.setDatasetIdentity("live-session-b");
    group.setModel(createChartGroupModel(replaced, "five_minute"));
    globalThis.__flushRaf();
    await settle();

    group.onForegroundRestore();
    globalThis.__flushRaf();
    await settle();

    const last = reports[reports.length - 1];
    assert.equal(last.followState, "following");
    assert.equal(last.range.to, 79);
  } finally {
    restore();
  }
});

// 辅助：在 DOM 子树中查找拥有指定事件类型监听器的后代元素。
// LC createChart 将 .tv-lightweight-charts 子元素 appendChild 到容器，
// 测试 stub 的 appendChild 设置 __parent。扫描全局元素注册表找到
// __parent 链通向 root 且有 type 监听器的元素。
function findDescendantWithListener(root, type) {
  for (const el of ALL_ELEMENTS) {
    if (el === root) continue;
    // 检查 __parent 链是否通向 root。
    let node = el.__parent || null;
    while (node && node !== root) {
      node = node.__parent || null;
    }
    if (node !== root) continue;
    // 检查该元素是否有 type 监听器。
    const listeners = el.__getListeners?.(type);
    if (listeners && listeners.size > 0) return el;
  }
  return null;
}
