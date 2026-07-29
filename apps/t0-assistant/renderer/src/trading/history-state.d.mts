import type { TradeRecord } from "./trade-client.d.mts";

export interface HistoryListState {
  trades: TradeRecord[];
  tradeRevision: number;
  serviceGeneration: number | null;
}

export function sortHistoryTrades(
  trades: ReadonlyArray<Record<string, unknown>>,
): TradeRecord[];

export function applyHistoryTradesChanged(
  currentState: HistoryListState | null,
  event: unknown,
): HistoryListState;
