import type { TradeRecord } from "./trade-client.d.mts";
import type { ApplicationError } from "./trade-client.d.mts";

export interface TradeListState {
  trades: TradeRecord[];
  tradeRevision: number;
  serviceGeneration: number | null;
  /** Scope of the last accepted matching trades_changed fact; null until one arrives. */
  loadedScope: { symbol: string; tradeDate: string } | null;
}

export function isRealTradesChangedEvent(event: unknown): event is {
  event_type: "trades_changed";
  session_id: null;
  payload: unknown;
  operation_id?: string;
  service_generation?: number;
};

export function filterTradesByReplayCursor(
  trades: ReadonlyArray<TradeRecord> | null | undefined,
  currentTime: string | null | undefined,
): TradeRecord[];

export function applyTradesChanged(
  currentState: TradeListState | null,
  event: unknown,
  scope: { symbol: string; tradeDate: string },
): TradeListState;

export function pendingOpResolvedByTradesChanged(
  event: unknown,
  pendingOperations: { has(id: string): boolean },
): string | null;

export function matchTradeOperationFailed(
  event: unknown,
  pendingOperations: { has(id: string): boolean },
): { operationId: string; error: ApplicationError | unknown } | null;
