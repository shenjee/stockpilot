export interface TradePendingOp {
  command: "create" | "update" | "delete";
  retry: () => Promise<void>;
}

export interface TradeOperationFailure {
  operationId: string | null;
  command: "create" | "update" | "delete" | null;
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
  ): boolean;
  failUntracked(message: string, error: unknown): void;
  readonly failure: TradeOperationFailure | null;
  hasPending(): boolean;
  dismissFailure(): void;
  clearPending(): void;
  subscribe(listener: (failure: TradeOperationFailure | null) => void): () => void;
}
