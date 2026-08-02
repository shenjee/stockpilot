import assert from "node:assert/strict";
import test from "node:test";

import {
  FollowState,
  applyModel,
  calculateVisibleCount,
  createViewportState,
  followLatest,
  fromChartLogicalRange,
  isAtLatestEdge,
  restoreViewportFromSnapshot,
  setManualRange,
  toChartLogicalRange,
  visibleLogicalRange,
} from "../renderer/src/charts/chart-viewport.mjs";

const TIMES = [
  "2026-07-22 09:30:00",
  "2026-07-22 09:35:00",
  "2026-07-22 09:40:00",
  "2026-07-22 09:45:00",
  "2026-07-22 09:50:00",
  "2026-07-22 09:55:00",
  "2026-07-22 10:00:00",
  "2026-07-22 10:05:00",
];

test("calculateVisibleCount derives N from plot width and slot width", () => {
  assert.equal(calculateVisibleCount(800, 8), 100);
  assert.equal(calculateVisibleCount(83, 8), 10);
  // 不足一根时至少返回 1；非正输入安全降级。
  assert.equal(calculateVisibleCount(3, 8), 1);
  assert.equal(calculateVisibleCount(0, 8), 1);
  assert.equal(calculateVisibleCount(800, 0), 1);
});

test("calculateVisibleCount supports Chan Viewer compatible 40–360 bounds", () => {
  const bounds = { minimum: 40, maximum: 360 };
  assert.equal(calculateVisibleCount(3, 8, bounds), 40);
  assert.equal(calculateVisibleCount(800, 8, bounds), 100);
  assert.equal(calculateVisibleCount(8_000, 8, bounds), 360);
});

test("calculateVisibleCount can lock intraday to the complete trading session", () => {
  const fullSessionMinutes = 242;
  const completeSession = {
    minimum: fullSessionMinutes,
    maximum: fullSessionMinutes,
  };
  assert.equal(calculateVisibleCount(320, 8, completeSession), fullSessionMinutes);
  assert.equal(
    calculateVisibleCount(2_000, 8, completeSession),
    fullSessionMinutes,
  );
});

test("followLatest right-aligns the latest N bars", () => {
  const state = createViewportState(TIMES);
  const followed = followLatest(state, 3);
  assert.equal(followed.followState, FollowState.FOLLOWING);
  assert.equal(followed.visibleEnd, TIMES.length);
  assert.equal(followed.visibleStart, TIMES.length - 3);
});

test("followLatest backfills into prior trading days when the day has fewer than N bars", () => {
  const state = createViewportState(TIMES);
  const followed = followLatest(state, 50);
  // 不足 N 根时从 0 开始铺满已加载历史，不产生空槽。
  assert.equal(followed.visibleStart, 0);
  assert.equal(followed.visibleEnd, TIMES.length);
});

test("setManualRange enters manual away from the latest edge", () => {
  const state = followLatest(createViewportState(TIMES), 4);
  const manual = setManualRange(state, 1, 3);
  assert.equal(manual.followState, FollowState.MANUAL);
  assert.equal(manual.visibleStart, 1);
  assert.equal(manual.visibleEnd, 3);
});

test("setManualRange resumes following when the user returns to the latest edge", () => {
  const state = followLatest(createViewportState(TIMES), 4);
  const manual = setManualRange(state, 2, 6);
  assert.equal(manual.followState, FollowState.MANUAL);
  const resumed = setManualRange(manual, 4, TIMES.length);
  assert.equal(resumed.followState, FollowState.FOLLOWING);
  assert.ok(isAtLatestEdge(resumed));
});

test("applyModel in following re-right-aligns on new data (forward roll)", () => {
  let state = followLatest(createViewportState(TIMES), 4);
  assert.deepEqual(visibleLogicalRange(state), { from: 4, to: 8 });
  const grown = [...TIMES, "2026-07-22 10:10:00"];
  state = applyModel(state, grown, 4);
  assert.equal(state.followState, FollowState.FOLLOWING);
  assert.deepEqual(visibleLogicalRange(state), { from: 5, to: 9 });
});

test("applyModel in manual preserves the logical range across new data", () => {
  let state = setManualRange(followLatest(createViewportState(TIMES), 4), 1, 3);
  // 新数据到达（前滚），manual 不跳回最新。
  state = applyModel(state, [...TIMES, "2026-07-22 10:10:00"], 4);
  assert.equal(state.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(state), { from: 1, to: 3 });
});

test("applyModel in manual clamps on backward replay seek and drops future refs", () => {
  let state = setManualRange(followLatest(createViewportState(TIMES), 4), 5, 7);
  // 回放向后定位：序列缩短到前 5 根，原范围 [5,7] 越界 -> 夹紧并回退 following。
  state = applyModel(state, TIMES.slice(0, 5), 4);
  assert.equal(state.followState, FollowState.FOLLOWING);
  assert.ok(state.visibleEnd <= 5);
});

test("applyModel in manual with a still-valid range on shrink stays manual", () => {
  let state = setManualRange(followLatest(createViewportState(TIMES), 4), 1, 3);
  // 序列缩短到前 5 根，原范围 [1,3] 仍有效 -> 保留 manual。
  state = applyModel(state, TIMES.slice(0, 5), 4);
  assert.equal(state.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(state), { from: 1, to: 3 });
});

// --- 缩放 vs 平移：最新端缩放进入 manual，仅平移回最新端恢复 following ---
// 评审 P1：仅凭“右端点贴最新边缘”无法区分缩放与平移。setManualRange 通过
// allowResumeFollowing 表达交互意图（由图表层按 LC 跨度变化判定后传入）：缩放传 false
// 始终 manual，平移传 true 才在贴边时恢复 following。

test("setManualRange with allowResumeFollowing:false stays manual at the latest edge (zoom-at-edge)", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const following = followLatest(createViewportState(bars), 50); // {50,100}
  // 用户在最新端缩放到最后 30 根：跨度变化 -> 缩放 -> 强制 manual（不恢复 following）。
  const zoomed = setManualRange(following, 70, 100, {
    allowResumeFollowing: false,
  });
  assert.equal(zoomed.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(zoomed), { from: 70, to: 100 });
  // 同一贴边范围，平移意图（allowResumeFollowing:true）则恢复 following--对照证明
  // 区分缩放/平移的是意图参数，而非端点位置。
  const resumed = setManualRange(following, 70, 100, {
    allowResumeFollowing: true,
  });
  assert.equal(resumed.followState, FollowState.FOLLOWING);
});

test("manual zoom cannot show fewer than 40 bars when history is available", () => {
  const bars = Array.from({ length: 120 }, (_, i) => `b${i}`);
  const following = followLatest(createViewportState(bars), 80);
  const bounded = setManualRange(following, 118, 120, {
    allowResumeFollowing: false,
    minimumVisibleCount: 40,
    maximumVisibleCount: 360,
  });
  assert.equal(bounded.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(bounded), { from: 80, to: 120 });
});

test("manual zoom-out is capped at 360 bars", () => {
  const bars = Array.from({ length: 500 }, (_, i) => `b${i}`);
  const following = followLatest(createViewportState(bars), 120);
  const bounded = setManualRange(following, 0, 500, {
    allowResumeFollowing: false,
    minimumVisibleCount: 40,
    maximumVisibleCount: 360,
  });
  assert.equal(bounded.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(bounded), { from: 140, to: 500 });
});

test("minimum visible count degrades to all bars for a genuinely short series", () => {
  const bars = Array.from({ length: 12 }, (_, i) => `b${i}`);
  const bounded = setManualRange(createViewportState(bars), 10, 12, {
    allowResumeFollowing: false,
    minimumVisibleCount: 40,
    maximumVisibleCount: 360,
  });
  assert.deepEqual(visibleLogicalRange(bounded), { from: 0, to: 12 });
});

test("setManualRange defaults to allowResumeFollowing:true (restore / pan-back contract)", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const following = followLatest(createViewportState(bars), 50);
  // 不传 options：贴边恢复 following（兼容 restoreViewportFromSnapshot 与平移回最新端）。
  assert.equal(
    setManualRange(following, 50, 100).followState,
    FollowState.FOLLOWING,
  );
  assert.equal(
    setManualRange(following, 30, 80).followState,
    FollowState.MANUAL,
  );
});

test("applyModel preserves a zoomed manual range across new data (no density recompute)", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  // 最新端缩放到 30 根 -> manual {70,100}。
  const zoomed = setManualRange(
    followLatest(createViewportState(bars), 50),
    70,
    100,
    { allowResumeFollowing: false },
  );
  assert.equal(zoomed.followState, FollowState.MANUAL);
  // 数据刷新（前滚一根）：manual 保留逻辑范围，不按密度重算 N（不会回到 50 根）。
  const refreshed = applyModel(zoomed, [...bars, "b100"], 50);
  assert.equal(refreshed.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(refreshed), { from: 70, to: 100 });
});

test("applyModel keeps a zoomed manual range on a same-length live-tick refresh (no flip to following)", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  // 最新端缩放到 30 根贴边 -> manual {70,100}（length 100）。
  const zoomed = setManualRange(
    followLatest(createViewportState(bars), 50),
    70,
    100,
    { allowResumeFollowing: false },
  );
  // 同长度刷新（动态 K tick 更新最后一根，length 仍 100）：范围贴边但不恢复 following，
  // 否则下一次布局变化会按密度重算 N 丢弃缩放。
  const ticked = applyModel(zoomed, bars, 50);
  assert.equal(ticked.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(ticked), { from: 70, to: 100 });
});

test("applyModel resumes following only when a replay-seek shrink clamps the manual range to the new latest edge", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  // manual 范围 {70,100}（length 100，贴边）。
  const zoomed = setManualRange(
    followLatest(createViewportState(bars), 50),
    70,
    100,
    { allowResumeFollowing: false },
  );
  // 回放向后 seek：序列缩短到 80，范围夹紧到 {70,80} 贴新最新边缘 -> 恢复 following。
  const shrunken = applyModel(zoomed, bars.slice(0, 80), 50);
  assert.equal(shrunken.followState, FollowState.FOLLOWING);
  assert.equal(shrunken.visibleEnd, 80);
});

// --- 评审 P2 回归：数据长度缩短但 manual 范围仍有效时不得误切 following ---

test("P2: applyModel keeps manual when the range is still fully valid after data shrinks", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  // manual 范围 [20,60)，数据缩短到 60：范围完整有效，没有发生裁剪。
  const manual = setManualRange(
    followLatest(createViewportState(bars), 50),
    20,
    60,
    { allowResumeFollowing: false },
  );
  const shrunken = applyModel(manual, bars.slice(0, 60), 50);
  assert.equal(shrunken.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(shrunken), { from: 20, to: 60 });
});

test("P2: applyModel resumes following when the manual range is truly clamped by shrink", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  // manual 范围 [70,100)，数据缩短到 80：end 被真实裁剪到 80 并贴新边缘 -> following。
  const manual = setManualRange(
    followLatest(createViewportState(bars), 50),
    70,
    100,
    { allowResumeFollowing: false },
  );
  const shrunken = applyModel(manual, bars.slice(0, 80), 50);
  assert.equal(shrunken.followState, FollowState.FOLLOWING);
  assert.equal(shrunken.visibleEnd, 80);
});

test("P2: applyModel falls back to following when the manual range is completely outside the new data", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const manual = setManualRange(
    followLatest(createViewportState(bars), 50),
    80,
    100,
    { allowResumeFollowing: false },
  );
  const shrunken = applyModel(manual, bars.slice(0, 50), 50);
  assert.equal(shrunken.followState, FollowState.FOLLOWING);
  assert.deepEqual(toChartLogicalRange(shrunken), { from: 0, to: 49 });
});

test("P2: applyModel preserves a valid manual range across a same-length live-tick refresh", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const manual = setManualRange(
    followLatest(createViewportState(bars), 50),
    20,
    60,
    { allowResumeFollowing: false },
  );
  const refreshed = applyModel(manual, bars, 50);
  assert.equal(refreshed.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(refreshed), { from: 20, to: 60 });
});

test("P2: applyModel right-aligns following state after a replay seek backward", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const following = followLatest(createViewportState(bars), 50);
  assert.deepEqual(toChartLogicalRange(following), { from: 50, to: 99 });
  const shrunken = applyModel(following, bars.slice(0, 80), 50);
  assert.equal(shrunken.followState, FollowState.FOLLOWING);
  assert.deepEqual(toChartLogicalRange(shrunken), { from: 30, to: 79 });
});

test("pan away from and back to the latest edge resumes following (span preserved)", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const following = followLatest(createViewportState(bars), 50); // {50,100}
  // 平移离开最新端：跨度不变 -> allowResumeFollowing:true，未贴边 -> manual。
  const pannedAway = setManualRange(following, 30, 80, {
    allowResumeFollowing: true,
  });
  assert.equal(pannedAway.followState, FollowState.MANUAL);
  // 平移回最新端：跨度不变且贴边 -> following。
  const pannedBack = setManualRange(pannedAway, 50, 100, {
    allowResumeFollowing: true,
  });
  assert.equal(pannedBack.followState, FollowState.FOLLOWING);
});

test("logical slots have no gaps across an overnight break", () => {
  const overnight = [
    "2026-07-21 15:00:00",
    "2026-07-22 09:35:00",
    "2026-07-22 09:40:00",
  ];
  const state = createViewportState(overnight);
  // 相邻实际 K 占相邻逻辑槽位，午休/隔夜不产生空槽。
  assert.equal(state.timeToLogical.get("2026-07-22 09:35:00"), 1);
  assert.equal(state.timeToLogical.get("2026-07-22 09:40:00"), 2);
});

// --- 适配层：内部排他范围 <-> Lightweight Charts 连续逻辑范围 ---
// 实测 lightweight-charts 5.x：自然最新 to = length-1；setVisibleLogicalRange
// 原样保留整数/小数；to = length 会产生右侧空槽。适配层据此转换。

test("toChartLogicalRange maps exclusive internal range to LC range without a right empty slot", () => {
  // following: 8 根显示 4 根 -> 内部 [4, 8)，LC to = 7 = length-1（无空槽）。
  const followed = followLatest(createViewportState(TIMES), 4);
  assert.deepEqual(toChartLogicalRange(followed), { from: 4, to: 7 });
  // manual [1, 3) -> LC {from:1, to:2}。
  const manual = setManualRange(followed, 1, 3);
  assert.deepEqual(toChartLogicalRange(manual), { from: 1, to: 2 });
});

test("fromChartLogicalRange restores following when the user returns to the latest edge", () => {
  const length = TIMES.length; // 8
  // 自然最新：LC to = length-1 = 7 -> end = floor(7)+1 = 8 = length -> 命中最新边缘。
  const atLatest = fromChartLogicalRange({ from: 4, to: 7 }, length);
  assert.deepEqual(atLatest, { start: 4, end: 8 });
  const resumed = setManualRange(
    followLatest(createViewportState(TIMES), 4),
    atLatest.start,
    atLatest.end,
  );
  assert.equal(resumed.followState, FollowState.FOLLOWING);
  assert.ok(isAtLatestEdge(resumed));
});

test("fromChartLogicalRange stays manual for a mid-range viewport", () => {
  const length = TIMES.length;
  const mid = fromChartLogicalRange({ from: 2, to: 5 }, length);
  assert.deepEqual(mid, { start: 2, end: 6 });
  const state = setManualRange(
    followLatest(createViewportState(TIMES), 4),
    mid.start,
    mid.end,
  );
  assert.equal(state.followState, FollowState.MANUAL);
});

test("fromChartLogicalRange handles fractional LC callback values close to real Lightweight Charts output", () => {
  const length = 100;
  // 实测 LC 回调原样保留整数/小数。to = 99.1 -> end = 100 -> 命中最新边缘。
  const fracLatest = fromChartLogicalRange({ from: 70.3, to: 99.1 }, length);
  assert.deepEqual(fracLatest, { start: 70, end: 100 });
  assert.ok(fracLatest.end >= length);
  // to = 98.6 -> end = 99 < 100 -> manual（已离开最新边缘）。
  const fracManual = fromChartLogicalRange({ from: 70.3, to: 98.6 }, length);
  assert.deepEqual(fracManual, { start: 70, end: 99 });
  assert.ok(fracManual.end < length);
  // to = 99.4（略超过最新边缘）仍算回到最新。
  const slightlyPast = fromChartLogicalRange({ from: 70, to: 99.4 }, length);
  assert.equal(slightlyPast.end, 100);
});

test("adapter round-trips an internal manual range through LC and back", () => {
  const state = setManualRange(followLatest(createViewportState(TIMES), 4), 1, 6);
  const lc = toChartLogicalRange(state);
  const back = fromChartLogicalRange(lc, TIMES.length);
  assert.deepEqual(back, {
    start: state.visibleStart,
    end: state.visibleEnd,
  });
});

test("following viewport on 100 bars shows index 99 as the last visible bar with no empty slot", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const state = followLatest(createViewportState(bars), 50);
  // 内部 [50, 100)；LC to = 99 = 最后可见 K 索引 = length-1，右侧无额外空槽。
  const lc = toChartLogicalRange(state);
  assert.equal(lc.from, 50);
  assert.equal(lc.to, 99);
  assert.equal(lc.to, bars.length - 1);
  // 用户范围包含索引 99 时恢复 following。
  const back = fromChartLogicalRange({ from: 60, to: 99 }, bars.length);
  const resumed = setManualRange(state, back.start, back.end);
  assert.equal(resumed.followState, FollowState.FOLLOWING);
});

test("following rolls forward correctly when a new bar arrives", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  let state = followLatest(createViewportState(bars), 50);
  assert.deepEqual(toChartLogicalRange(state), { from: 50, to: 99 });
  // 新增第 101 根：following 自然前滚，最后一根为索引 100 = 新 length-1。
  const grown = [...bars, "b100"];
  state = applyModel(state, grown, 50);
  assert.equal(state.followState, FollowState.FOLLOWING);
  assert.deepEqual(toChartLogicalRange(state), { from: 51, to: 100 });
});

// --- 组件重建后从 React 快照恢复可见范围 ---

test("restoreViewportFromSnapshot follows latest for a null or following snapshot", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const fromNull = restoreViewportFromSnapshot(null, bars, 50);
  assert.equal(fromNull.followState, FollowState.FOLLOWING);
  assert.deepEqual(toChartLogicalRange(fromNull), { from: 50, to: 99 });

  const fromFollowing = restoreViewportFromSnapshot(
    { range: { from: 40, to: 89 }, followState: "following" },
    bars,
    50,
  );
  assert.equal(fromFollowing.followState, FollowState.FOLLOWING);
  // following 快照忽略存储的 range，按当前宽度右对齐最新。
  assert.deepEqual(toChartLogicalRange(fromFollowing), { from: 50, to: 99 });
});

test("restoreViewportFromSnapshot restores a manual range clamped to the current length", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const manual = restoreViewportFromSnapshot(
    { range: { from: 20, to: 59 }, followState: "manual" },
    bars,
    50,
  );
  assert.equal(manual.followState, FollowState.MANUAL);
  assert.equal(manual.visibleStart, 20);
  assert.equal(manual.visibleEnd, 60);
});

test("restoreViewportFromSnapshot keeps manual when a snapshot is pinned to the latest edge", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  // 评审 P1：快照明确为 manual 且范围有效时，不得仅因右端点等于 length - 1 就改为 following。
  const pinned = restoreViewportFromSnapshot(
    { range: { from: 50, to: 99 }, followState: "manual" },
    bars,
    50,
  );
  assert.equal(pinned.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(pinned), { from: 50, to: 100 });
});

test("restoreViewportFromSnapshot clamps a manual range that exceeds a shrunken (replay seek) length", () => {
  // 回放向后 seek：序列从 100 缩短到 60，存储的 manual 范围 [50,100) 越界 -> 夹紧到最新。
  const shrunken = Array.from({ length: 60 }, (_, i) => `b${i}`);
  const clamped = restoreViewportFromSnapshot(
    { range: { from: 50, to: 99 }, followState: "manual" },
    shrunken,
    50,
  );
  assert.equal(clamped.followState, FollowState.FOLLOWING);
  assert.equal(clamped.visibleEnd, 60);
});

// --- 评审 P1 回归：贴最新边缘的 manual 快照组件重建后不得错误恢复为 following ---

test("P1: restoreViewportFromSnapshot keeps manual for an edge-pinned manual snapshot", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const restored = restoreViewportFromSnapshot(
    { range: { from: 70, to: 99 }, followState: "manual" },
    bars,
    50,
  );
  assert.equal(restored.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(restored), { from: 70, to: 100 });
});

test("P1: after restoring an edge-pinned manual snapshot, a new bar keeps the same logical range", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  let restored = restoreViewportFromSnapshot(
    { range: { from: 70, to: 99 }, followState: "manual" },
    bars,
    50,
  );
  assert.equal(restored.followState, FollowState.MANUAL);
  const grown = [...bars, "b100"];
  restored = applyModel(restored, grown, 50);
  assert.equal(restored.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(restored), { from: 70, to: 100 });
});

test("P1: after restoring an edge-pinned manual snapshot, layout/width change keeps the manual range", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const restored = restoreViewportFromSnapshot(
    { range: { from: 70, to: 99 }, followState: "manual" },
    bars,
    50,
  );
  // 模拟不同宽度（visibleCount 变化）：manual 状态下不应重算 N。
  const wider = applyModel(restored, bars, 80);
  assert.equal(wider.followState, FollowState.MANUAL);
  assert.deepEqual(visibleLogicalRange(wider), { from: 70, to: 100 });
});

test("P1: restoreViewportFromSnapshot resumes following for a following snapshot", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  const restored = restoreViewportFromSnapshot(
    { range: { from: 40, to: 89 }, followState: "following" },
    bars,
    50,
  );
  assert.equal(restored.followState, FollowState.FOLLOWING);
  assert.deepEqual(toChartLogicalRange(restored), { from: 50, to: 99 });
});

test("P1: restoreViewportFromSnapshot falls back to following when a manual snapshot is fully outside the new data", () => {
  const bars = Array.from({ length: 50 }, (_, i) => `b${i}`);
  const restored = restoreViewportFromSnapshot(
    { range: { from: 70, to: 99 }, followState: "manual" },
    bars,
    50,
  );
  assert.equal(restored.followState, FollowState.FOLLOWING);
  assert.deepEqual(toChartLogicalRange(restored), { from: 0, to: 49 });
});
