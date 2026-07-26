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

// 适配层：内部排他范围 [visibleStart, visibleEnd) 与 Lightweight Charts 连续逻辑范围
// {from, to} 之间的转换。二者数值不能直接复用，否则引入一位偏移。
//
// 实测（lightweight-charts 4.x，headless 跑通）：
// - setData 后自然最新位置（rightOffset=0）getVisibleLogicalRange().to = length - 1。
// - setVisibleLogicalRange({from,to}) 后读回与 subscribe 回调均为原 {from,to}（整数/小数保留）。
// - to = length 对应 rightOffset = 1（右侧一个空槽）；to = length - 1 对应无空槽、最后一根贴右。
// 故：LC to = visibleEnd - 1（最后可见 K 的索引）；反向 end = floor(to) + 1，使自然最新
// to = length - 1 还原为 end = length，isAtLatestEdge 才能在用户拖回最新边缘时恢复 following。
export function toChartLogicalRange(state) {
  const from = state.visibleStart;
  // 防御：保证 from <= to（count >= 1 时自然成立；空序列不会走到这里）。
  const to = Math.max(from, state.visibleEnd - 1);
  return { from, to };
}

export function fromChartLogicalRange(range, length) {
  const start = Math.max(0, Math.min(Math.floor(range.from), length));
  const end = Math.max(start, Math.min(Math.floor(range.to) + 1, length));
  return { start, end };
}

// 从 React 保存的视口快照恢复：following/无快照 -> 右对齐最新；manual -> 还原范围并
// 夹紧到当前序列长度（回放 seek 缩短时丢弃越界引用，贴到最新边缘则恢复 following）。
// 用于组件重建后从 React 权威状态恢复可见范围，不依赖图表实例未被卸载。
export function restoreViewportFromSnapshot(
  snapshot,
  times,
  visibleCount,
  options = {},
) {
  const state = createViewportState(times, {
    barSlotWidth: options.barSlotWidth,
  });
  if (!snapshot || snapshot.followState !== FollowState.MANUAL || !snapshot.range) {
    return followLatest(state, visibleCount);
  }
  const internal = fromChartLogicalRange(snapshot.range, times.length);
  if (internal.end <= internal.start) {
    return followLatest(state, visibleCount);
  }
  return setManualRange(state, internal.start, internal.end);
}
