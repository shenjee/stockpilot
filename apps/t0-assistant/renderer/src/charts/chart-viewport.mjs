/**
 * 图表组视口状态机（纯逻辑，不依赖 Lightweight Charts）。
 *
 * 在正式 Renderer 状态层重新实现 spikes/0005-.../chart-group-state.ts 的逻辑索引
 * 映射、following/manual 转换与回放截断证据，不直接复制 spike 代码。
 *
 * - logical index：对应实际 K 线序号，午休/隔夜/周末/节假日/停牌不产生空槽。
 * - following：右对齐最新一根可见 K；宽度变化重算 N；新数据自然前滚。
 * - manual：保留逻辑可见范围；刷新/动态 K/布局/react 重渲不强制跳回最新；
 *   用户回到最新边缘后恢复 following。
 * - 回放截断：applyModel 在 manual 下将范围夹紧到新序列长度，丢弃对未来时点的引用。
 *
 * 时间戳为字符串（与契约一致），比较按字典序——契约时间戳为定长
 * "YYYY-MM-DD HH:MM:SS"，字典序与时间序一致。
 */

export const FollowState = Object.freeze({
  FOLLOWING: "following",
  MANUAL: "manual",
});

const DEFAULT_BAR_SLOT_WIDTH = 8;

export function createViewportState(times, options = {}) {
  const barSlotWidth =
    options.barSlotWidth && options.barSlotWidth > 0
      ? options.barSlotWidth
      : DEFAULT_BAR_SLOT_WIDTH;
  const timeToLogical = new Map();
  times.forEach((time, index) => {
    timeToLogical.set(time, index);
  });
  return {
    logicalToTime: times,
    timeToLogical,
    visibleStart: 0,
    visibleEnd: times.length,
    followState: FollowState.FOLLOWING,
    barSlotWidth,
  };
}

// 以价格图绘图区宽度（已扣除坐标轴/边距）和目标槽位宽度计算可见 N 根。
export function calculateVisibleCount(plotWidth, barSlotWidth) {
  if (!Number.isFinite(plotWidth) || plotWidth <= 0 || barSlotWidth <= 0) {
    return 1;
  }
  return Math.max(1, Math.floor(plotWidth / barSlotWidth));
}

// following：右对齐最新 N 根。
export function followLatest(state, visibleCount) {
  const end = state.logicalToTime.length;
  const start = Math.max(0, end - visibleCount);
  return {
    ...state,
    visibleStart: start,
    visibleEnd: end,
    followState: FollowState.FOLLOWING,
  };
}

// 用户拖动/缩放后设置手动范围；若已回到最新边缘则恢复 following。
export function setManualRange(state, start, end) {
  const length = state.logicalToTime.length;
  const clampedStart = Math.max(0, Math.min(start, length));
  const clampedEnd = Math.max(0, Math.min(end, length));
  const atLatestEdge = clampedEnd >= length;
  return {
    ...state,
    visibleStart: clampedStart,
    visibleEnd: clampedEnd,
    followState: atLatestEdge ? FollowState.FOLLOWING : FollowState.MANUAL,
  };
}

export function isAtLatestEdge(state) {
  return state.visibleEnd >= state.logicalToTime.length;
}

// 应用新模型（新时间戳数组）：
// - following：右对齐最新（新数据自然前滚；回放 seek 重跟随）。
// - manual：保留逻辑范围并夹紧到新长度（回放向后定位丢弃未来引用）；
//   若范围因夹紧失效则回退 following；若夹紧后贴到最新边缘则恢复 following。
export function applyModel(state, newTimes, visibleCount) {
  const timeToLogical = new Map();
  newTimes.forEach((time, index) => {
    timeToLogical.set(time, index);
  });
  const base = { ...state, logicalToTime: newTimes, timeToLogical };

  if (state.followState === FollowState.FOLLOWING) {
    return followLatest(base, visibleCount);
  }

  const length = newTimes.length;
  const start = Math.max(0, Math.min(state.visibleStart, length));
  const end = Math.max(0, Math.min(state.visibleEnd, length));
  if (end <= start) {
    return followLatest(base, visibleCount);
  }
  return {
    ...base,
    visibleStart: start,
    visibleEnd: end,
    followState: end >= length ? FollowState.FOLLOWING : FollowState.MANUAL,
  };
}

export function visibleLogicalRange(state) {
  return { from: state.visibleStart, to: state.visibleEnd };
}
