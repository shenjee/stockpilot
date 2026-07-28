export type TradeCommand = "create" | "update" | "delete";

export interface TradePendingOp {
  command: TradeCommand;
  retry: () => Promise<void>;
}

export interface TradeOperationFailure {
  /** Stable, unique id for UI keying/dismiss (distinct from operationId). */
  failureId: string;
  operationId: string | null;
  command: TradeCommand | null;
  message: string;
  retry: (() => Promise<void>) | null;
  error: unknown;
}

export class TradeOperationController {
  track(operationId: string, op: TradePendingOp): void;
  has(operationId: string): boolean;
  resolve(operationId: string): boolean;
  fail(
    operationId: string,
    message: string,
    error: unknown,
  ): string | null;
  failUntracked(
    operationId: string | null,
    message: string,
    error: unknown,
    options?: { command?: TradeCommand | null; retry?: (() => Promise<void>) | null },
  ): string;
  readonly failures: TradeOperationFailure[];
  readonly failure: TradeOperationFailure | null;
  hasPending(): boolean;
  dismissFailure(failureId: string): void;
  dismissAllFailures(): void;
  clearPending(): void;
  subscribe(
    listener: (failures: TradeOperationFailure[]) => void,
  ): () => void;
}
