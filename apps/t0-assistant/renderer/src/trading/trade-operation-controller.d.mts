export type TradeCommand = "create" | "update" | "delete";

export interface TradePendingOp {
  command: TradeCommand;
  retry: () => Promise<void>;
}

export interface TradeOperationFailure {
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
  ): boolean;
  failUntracked(
    operationId: string | null,
    message: string,
    error: unknown,
  ): void;
  readonly failures: TradeOperationFailure[];
  readonly failure: TradeOperationFailure | null;
  hasPending(): boolean;
  dismissFailure(operationId: string): void;
  dismissAllFailures(): void;
  clearPending(): void;
  subscribe(
    listener: (failures: TradeOperationFailure[]) => void,
  ): () => void;
}
