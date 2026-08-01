import type { WorkbenchChartSnapshot } from "./charts/chart-model.mjs";

export const REPLAY_SPEEDS: readonly [1, 2, 5, 10];

export interface ReplayFacts {
  sessionId: string;
  state: "ready" | "playing" | "paused";
  granularity: "one_minute" | "five_minute";
  currentTime: string;
  nextBarTime: string | null;
  startTime: string;
  endTime: string;
  playbackSpeed: 1 | 2 | 5 | 10;
  startValue: number;
  currentValue: number;
  endValue: number;
}

export interface ReplayControlState {
  active: boolean;
  playing: boolean;
  canTogglePlayback: boolean;
  canSeek: boolean;
  canStep: boolean;
  canChangeSpeed: boolean;
  stepLabel: "前进 1 分钟" | "前进 5 分钟";
  granularityLabel: "" | "1 分钟回放" | "5 分钟回放";
}

export function replayFactsFromSnapshot(
  snapshot: WorkbenchChartSnapshot | null | undefined,
): Readonly<ReplayFacts> | null;
export function deriveReplayControls(
  facts: ReplayFacts | null,
  options?: { busy?: boolean },
): Readonly<ReplayControlState>;
export function replaySessionMatches(
  activeSessionId: string | null,
  candidateSessionId: string | null,
): boolean;
export function replayOperationMatches(
  activeOperationId: string | null,
  candidateOperationId: string | null,
): boolean;
export function isReplayOwnedError(error: unknown): boolean;
/** @deprecated Prefer {@link isReplayOwnedError}. */
export function isReplayScopedError(error: unknown): boolean;
export function asReplayOwnedError<T extends object>(error: T): T & {
  source: "replay";
};
export function marketTimeValue(timestamp: string): number | null;
export function marketTimeFromValue(value: number): string;
export function marketClockLabel(timestamp: string): string;
