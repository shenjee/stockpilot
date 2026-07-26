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
// 实测 lightweight-charts 4.x：自然最新 to = length-1；setVisibleLogicalRange
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

test("restoreViewportFromSnapshot resumes following when a manual snapshot is pinned to the latest edge", () => {
  const bars = Array.from({ length: 100 }, (_, i) => `b${i}`);
  // 存储的 manual 范围贴到最新边缘 -> 恢复后回到 following。
  const pinned = restoreViewportFromSnapshot(
    { range: { from: 50, to: 99 }, followState: "manual" },
    bars,
    50,
  );
  assert.equal(pinned.followState, FollowState.FOLLOWING);
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
