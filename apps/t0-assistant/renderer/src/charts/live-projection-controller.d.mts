import type {
  ChartAppEvent,
  ChartProjection,
  ChartProjectionIdentity,
} from "./chart-projection.mjs";
import type { WorkbenchChartSnapshot } from "./chart-model.mjs";

export declare class LiveProjectionController {
  constructor(initialProjection: ChartProjection);
  readonly projection: ChartProjection;
  readonly rebaselineRequestKey: string | null;
  replace(projection: ChartProjection): ChartProjection;
  beginSession(
    snapshot: WorkbenchChartSnapshot,
    serviceGeneration: number | null | undefined,
    sessionId?: string | null,
  ): ChartProjection;
  applySnapshot(
    snapshot: WorkbenchChartSnapshot,
    identity: ChartProjectionIdentity,
  ): ChartProjection;
  applyEvent(event: ChartAppEvent): ChartProjection;
  resetForGeneration(
    emptySnapshot: WorkbenchChartSnapshot,
    serviceGeneration: number,
  ): ChartProjection;
  beginRebaselineRequest(requestKey: string): boolean;
  clearRebaselineRequest(): void;
  subscribe(listener: (projection: ChartProjection) => void): () => void;
}
