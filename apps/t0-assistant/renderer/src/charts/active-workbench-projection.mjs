/**
 * Read-only ActiveWorkbenchProjection selector (#155).
 *
 * Owns no state and performs no revision/generation gating. Live and Replay
 * controllers (PR2) own projections and gates; App only composes sources and
 * renders whatever this selector returns.
 *
 * Replay loading fallback is owned by ReplaySessionController as
 * `loadingFallbackProjection`: captured at Replay mode entry (not at
 * beginReplay), held by immutable reference, never updated by Live increments.
 */

import { WorkbenchMode } from "../workbench-layout.mjs";

/**
 * @typedef {import("./chart-projection.mjs").ChartProjection} ChartProjection
 *
 * @typedef {object} ActiveWorkbenchProjectionSources
 * @property {"live" | "replay"} mode
 * @property {ChartProjection | null | undefined} [liveProjection]
 * @property {ChartProjection | null | undefined} [replayProjection]
 * @property {ChartProjection | null | undefined} [loadingFallbackProjection]
 */

/**
 * Select the single projection the chart and (when ready) Replay controls share.
 *
 * @param {ActiveWorkbenchProjectionSources} sources
 * @returns {ChartProjection | null}
 */
export function selectActiveWorkbenchProjection({
  mode,
  liveProjection = null,
  replayProjection = null,
  loadingFallbackProjection = null,
} = {}) {
  if (mode === WorkbenchMode.REPLAY) {
    return replayProjection ?? loadingFallbackProjection ?? null;
  }
  return liveProjection ?? null;
}

/**
 * Capture the foreground projection when entering Replay mode.
 *
 * Returns the same object identity (no structuredClone). Projection reducers
 * must keep using immutable updates so this frozen hold stays stable.
 *
 * @param {ChartProjection | null | undefined} foregroundProjection
 * @returns {ChartProjection | null}
 */
export function captureLoadingFallbackProjection(foregroundProjection) {
  return foregroundProjection ?? null;
}

/**
 * True when Replay owns an authoritative projection (controls + chart identity).
 *
 * @param {ChartProjection | null | undefined} replayProjection
 * @returns {boolean}
 */
export function hasAuthoritativeReplayProjection(replayProjection) {
  return replayProjection != null;
}
