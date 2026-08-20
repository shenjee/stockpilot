import type { ChartProjection } from "./chart-projection.mjs";
import type { WorkbenchChartSnapshot } from "./chart-model.mjs";

export declare class ReplaySessionController {
  constructor();
  readonly projection: ChartProjection | null;
  readonly loadingFallbackProjection: ChartProjection | null;
  readonly inReplayMode: boolean;
  readonly sessionId: string | null;
  readonly loadOperationId: string | null;
  readonly loading: boolean;
  readonly busy: boolean;
  readonly playbackPending: boolean;
  readonly resumeAfterSeek: boolean;
  readonly hasAuthoritativeProjection: boolean;
  enterMode(foregroundProjection: ChartProjection | null | undefined): void;
  exitMode(): string | null;
  clearForGenerationChange(): void;
  setServiceGeneration(generation: number | null): void;
  beginSession(sessionId: string, loadOperationId?: string | null): void;
  acceptSnapshot(
    snapshot: WorkbenchChartSnapshot,
    identity?: {
      service_generation?: number;
      session_id?: string;
      revision?: number;
    },
  ): boolean;
  applySessionStatus(payload: {
    state?: unknown;
    playback_speed?: unknown;
    revision?: number | null;
  }): boolean;
  matchesLoadOperation(operationId: string | null | undefined): boolean;
  clearLoadOperation(): void;
  failLoadOperation(): void;
  setLoading(value: boolean): void;
  setBusy(value: boolean): void;
  setPlaybackPending(value: boolean): void;
  setResumeAfterSeek(value: boolean): void;
  adoptCursorOperation(
    operationId: string | null | undefined,
  ): { status: string; early: string | null };
  noteCursorOutcome(
    operationId: string | null | undefined,
    kind: "completed" | "failed",
  ): "settled" | "cached" | "ignored";
  clearCursor(): void;
  takeResumeAfterSeek(): boolean;
  subscribe(listener: () => void): () => void;
}
