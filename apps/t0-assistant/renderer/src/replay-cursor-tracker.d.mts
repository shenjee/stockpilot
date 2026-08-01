export type ReplayCursorOutcomeKind = "completed" | "failed";

export type ReplayCursorNoteResult = "settled" | "cached" | "ignored";

export type ReplayCursorAdoptResult =
  | { status: "no_operation"; early: null }
  | { status: "already_settled"; early: ReplayCursorOutcomeKind }
  | { status: "tracking"; early: null };

export interface ReplayCursorTracker {
  readonly activeOperationId: string | null;
  noteOutcome(
    operationId: string | null | undefined,
    kind: ReplayCursorOutcomeKind,
  ): ReplayCursorNoteResult;
  adopt(operationId: string | null | undefined): ReplayCursorAdoptResult;
  clear(): void;
}

export function createReplayCursorTracker(): ReplayCursorTracker;
