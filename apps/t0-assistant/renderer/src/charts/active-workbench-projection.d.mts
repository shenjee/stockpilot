import type { ChartProjection } from "./chart-projection.mjs";

export interface ActiveWorkbenchProjectionSources {
  mode: "live" | "replay";
  liveProjection?: ChartProjection | null;
  replayProjection?: ChartProjection | null;
  loadingFallbackProjection?: ChartProjection | null;
}

export function selectActiveWorkbenchProjection(
  sources?: ActiveWorkbenchProjectionSources,
): ChartProjection | null;

export function captureLoadingFallbackProjection(
  foregroundProjection: ChartProjection | null | undefined,
): ChartProjection | null;

export function hasAuthoritativeReplayProjection(
  replayProjection: ChartProjection | null | undefined,
): boolean;
