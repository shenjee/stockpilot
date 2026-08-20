export const AppendFollowPolicy: Readonly<{
  PRESERVE: "preserve";
  FORCE_FOLLOW_LATEST: "force-follow-latest";
}>;

export type AppendFollowPolicyValue =
  (typeof AppendFollowPolicy)[keyof typeof AppendFollowPolicy];

export const FollowState: Readonly<{
  FOLLOWING: "following";
  MANUAL: "manual";
}>;

export type FollowStateValue = (typeof FollowState)[keyof typeof FollowState];

export interface ChartViewportState {
  logicalToTime: readonly string[];
  timeToLogical: ReadonlyMap<string, number>;
  visibleStart: number;
  visibleEnd: number;
  followState: FollowStateValue;
  barSlotWidth: number;
}

export interface LogicalRange {
  from: number;
  to: number;
}

/** 图表可见范围快照：range 为 LC 连续逻辑范围，followState 标记跟随/手工。 */
export interface ChartViewportSnapshot {
  range: LogicalRange;
  followState: FollowStateValue;
}

/** 一个完整交易日 5 分钟 K 线根数（早盘 24 + 午盘 24）。 */
export const BARS_PER_TRADING_DAY_5M: number;
/** 跟随最新状态下 N 的下限（1.5 个交易日 = 72 根）。 */
export const FOLLOW_MIN_VISIBLE_BARS_5M: number;
/** 手动浏览状态下 N 的下限（1 个交易日 = 48 根）。 */
export const MANUAL_MIN_VISIBLE_BARS_5M: number;
/** 两种状态下 N 的上限（360 根）。 */
export const MAX_VISIBLE_BARS: number;

export function createViewportState(
  times: readonly string[],
  options?: { barSlotWidth?: number },
): ChartViewportState;

export function calculateVisibleCount(
  plotWidth: number,
  barSlotWidth: number,
  options?: { minimum?: number; maximum?: number },
): number;

export function followLatest(
  state: ChartViewportState,
  visibleCount: number,
): ChartViewportState;

export function setManualRange(
  state: ChartViewportState,
  start: number,
  end: number,
  options?: {
    allowResumeFollowing?: boolean;
    minimumVisibleCount?: number;
    maximumVisibleCount?: number;
  },
): ChartViewportState;

export function isAtLatestEdge(state: ChartViewportState): boolean;

/**
 * 稳定 Live Session 内的向前追加：旧序列是新序列的严格前缀，且最后时间戳变晚。
 * 同时间戳 OHLCV 更新、左侧 prepend、换股/整段替换均返回 false。
 */
export function isStableForwardAppend(
  oldTimes: readonly string[],
  newTimes: readonly string[],
): boolean;

export function applyModel(
  state: ChartViewportState,
  newTimes: readonly string[],
  visibleCount: number,
): ChartViewportState;

export function visibleLogicalRange(state: ChartViewportState): LogicalRange;

/**
 * 内部排他范围 [visibleStart, visibleEnd) -> Lightweight Charts 连续逻辑范围。
 * LC 的 `to` = 最后可见 K 的索引 = visibleEnd - 1，使 following（end=length）无右侧空槽。
 */
export function toChartLogicalRange(state: ChartViewportState): LogicalRange;

/**
 * Lightweight Charts 连续逻辑范围 -> 内部排他范围。
 * `end = floor(to) + 1`，使自然最新 to = length-1 还原为 end = length（命中最新边缘）。
 */
export function fromChartLogicalRange(
  range: LogicalRange,
  length: number,
): { start: number; end: number };

/**
 * 从 React 保存的快照恢复视口：following/无快照 -> 右对齐最新；manual -> 还原范围并
 * 夹紧到当前序列长度。用于组件重建后恢复可见范围。
 */
export function restoreViewportFromSnapshot(
  snapshot: ChartViewportSnapshot | null,
  times: readonly string[],
  visibleCount: number,
  options?: {
    barSlotWidth?: number;
    minimumVisibleCount?: number;
    maximumVisibleCount?: number;
  },
): ChartViewportState;
