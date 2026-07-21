/**
 * 图表组状态模型
 * - logical index: 逻辑索引，对应实际 K 线，无空槽
 * - 视口范围管理
 * - 跟随最新/手工浏览状态机
 */

export type FollowState = 'following' | 'manual';

export interface ChartGroupState {
  // 逻辑索引映射
  readonly logicalToTime: readonly string[];
  readonly timeToLogical: ReadonlyMap<string, number>;

  // 视口范围（逻辑索引）
  readonly visibleStart: number;
  readonly visibleEnd: number;

  // 跟随状态
  readonly followState: FollowState;

  // 配置
  readonly barSlotWidth: number; // 每根 K 线占用的像素宽度
}

// 初始状态
export function createInitialState(times: string[], barSlotWidth: number = 10): ChartGroupState {
  const timeToLogical = new Map<string, number>();
  times.forEach((time, index) => {
    timeToLogical.set(time, index);
  });

  return {
    logicalToTime: times,
    timeToLogical,
    visibleStart: Math.max(0, times.length - 80), // 默认显示 80 根
    visibleEnd: times.length,
    followState: 'following',
    barSlotWidth,
  };
}

// 根据容器宽度计算应显示的 N 根 K 线
export function calculateVisibleCount(containerWidth: number, barSlotWidth: number): number {
  return Math.max(1, Math.floor(containerWidth / barSlotWidth));
}

// 跟随最新：右对齐，显示最新 N 根
export function followLatest(state: ChartGroupState, visibleCount: number): ChartGroupState {
  const end = state.logicalToTime.length;
  const start = Math.max(0, end - visibleCount);
  return {
    ...state,
    visibleStart: start,
    visibleEnd: end,
    followState: 'following',
  };
}

// 设置手动范围（用户拖动/缩放后）
export function setManualRange(state: ChartGroupState, start: number, end: number): ChartGroupState {
  const clampedStart = Math.max(0, start);
  const clampedEnd = Math.min(state.logicalToTime.length, end);

  // 如果用户已经拖动到最新边缘，恢复跟随
  // visibleEnd 是排他右端，等于 length 时表示包含最后一根
  const isAtLatestEdge = clampedEnd >= state.logicalToTime.length;

  return {
    ...state,
    visibleStart: clampedStart,
    visibleEnd: clampedEnd,
    followState: isAtLatestEdge ? 'following' : 'manual',
  };
}

// 增量更新数据（新 K 线到来）
export function appendData(state: ChartGroupState, newTimes: string[]): ChartGroupState {
  const newLogicalToTime = [...state.logicalToTime, ...newTimes];
  const newTimeToLogical = new Map(state.timeToLogical);
  newTimes.forEach((time, i) => {
    newTimeToLogical.set(time, state.logicalToTime.length + i);
  });

  let newState: ChartGroupState = {
    ...state,
    logicalToTime: newLogicalToTime,
    timeToLogical: newTimeToLogical,
  };

  // 如果在跟随模式，自动滚动到最新
  if (state.followState === 'following') {
    const visibleCount = state.visibleEnd - state.visibleStart;
    newState = followLatest(newState, visibleCount);
  }

  return newState;
}

// 获取当前可见范围内的时间戳
export function getVisibleTimes(state: ChartGroupState): string[] {
  return state.logicalToTime.slice(state.visibleStart, state.visibleEnd);
}

// 根据时间戳获取逻辑索引
export function timeToLogicalIndex(state: ChartGroupState, time: string): number | undefined {
  return state.timeToLogical.get(time);
}

// 检查是否在最新边缘
export function isAtLatestEdge(state: ChartGroupState): boolean {
  // visibleEnd 是排他右端，等于 length 时表示包含最后一根
  return state.visibleEnd >= state.logicalToTime.length;
}

// 回放截断：丢弃 T 之后的数据
export function truncateAtTime(state: ChartGroupState, maxTime: string): ChartGroupState {
  const maxIndex = state.logicalToTime.findIndex(t => t > maxTime);
  const effectiveEnd = maxIndex === -1 ? state.logicalToTime.length : maxIndex;

  const truncatedTimes = state.logicalToTime.slice(0, effectiveEnd);
  const newTimeToLogical = new Map<string, number>();
  truncatedTimes.forEach((time, index) => {
    newTimeToLogical.set(time, index);
  });

  // 调整视口，确保不会超出截断后范围
  let newVisibleStart = state.visibleStart;
  let newVisibleEnd = Math.min(state.visibleEnd, effectiveEnd);

  // 如果视口完全在截断点之后，重置到显示最后 N 根
  if (newVisibleStart >= effectiveEnd) {
    const visibleCount = state.visibleEnd - state.visibleStart;
    newVisibleEnd = effectiveEnd;
    newVisibleStart = Math.max(0, effectiveEnd - visibleCount);
  }

  return {
    ...state,
    logicalToTime: truncatedTimes,
    timeToLogical: newTimeToLogical,
    visibleStart: newVisibleStart,
    visibleEnd: newVisibleEnd,
    // 回放始终保持跟随最新（截断点）
    followState: 'following',
  };
}
