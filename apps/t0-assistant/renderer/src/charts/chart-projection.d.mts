import type { WorkbenchChartSnapshot } from "./chart-model.mjs";

export interface ChartAppEvent {
  event_type: string;
  service_generation?: number;
  session_id?: string | null;
  revision?: number;
  payload: unknown;
}

export interface ChartProjectionIdentity {
  service_generation?: number;
  session_id?: string | null;
  revision?: number;
}

export interface ChartProjection {
  snapshot: WorkbenchChartSnapshot;
  serviceGeneration: number | null;
  sessionId: string | null;
  revision: number | null;
  rebaselineRequired: boolean;
}

export function createChartProjection(
  snapshot: WorkbenchChartSnapshot,
  identity?: ChartProjectionIdentity,
): ChartProjection;

export function applyWorkbenchSnapshot(
  projection: ChartProjection,
  snapshot: WorkbenchChartSnapshot,
  identity: ChartProjectionIdentity,
): ChartProjection;

export function applyLiveChartEvent(
  projection: ChartProjection,
  event: ChartAppEvent,
): ChartProjection;
