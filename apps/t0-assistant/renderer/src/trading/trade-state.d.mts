import type { TradeRecord } from "./trade-client.d.mts";
import type { ApplicationError } from "./trade-client.d.mts";

export interface TradeListState {
  trades: TradeRecord[];
  tradeRevision: number;
}

export function isRealTradesChangedEvent(event: unknown): event is {
  event_type: "trades_changed";
  session_id: null;
  payload: unknown;
};

export function applyTradesChanged(
  currentState: TradeListState | null,
  event: unknown,
  scope: { symbol: string; tradeDate: string },
): TradeListState;

export function matchTradeOperationFailed(
  event: unknown,
  pendingOperationIds: ReadonlySet<string>,
): { operationId: string; error: ApplicationError | unknown } | null;
