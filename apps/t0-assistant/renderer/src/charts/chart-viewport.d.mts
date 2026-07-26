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

export function createViewportState(
  times: readonly string[],
  options?: { barSlotWidth?: number },
): ChartViewportState;

export function calculateVisibleCount(
  plotWidth: number,
  barSlotWidth: number,
): number;

export function followLatest(
  state: ChartViewportState,
  visibleCount: number,
): ChartViewportState;

export function setManualRange(
  state: ChartViewportState,
  start: number,
  end: number,
): ChartViewportState;

export function isAtLatestEdge(state: ChartViewportState): boolean;

export function applyModel(
  state: ChartViewportState,
  newTimes: readonly string[],
  visibleCount: number,
): ChartViewportState;

export function visibleLogicalRange(state: ChartViewportState): LogicalRange;
