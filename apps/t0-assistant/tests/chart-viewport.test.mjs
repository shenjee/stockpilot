import assert from "node:assert/strict";
import test from "node:test";

import {
  FollowState,
  applyModel,
  calculateVisibleCount,
  createViewportState,
  followLatest,
  isAtLatestEdge,
  setManualRange,
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
