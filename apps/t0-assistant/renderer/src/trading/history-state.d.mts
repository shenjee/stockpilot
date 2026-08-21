import type { TradeRecord } from "./trade-client.d.mts";

export interface HistoryListState {
  trades: TradeRecord[];
  tradeRevision: number;
  serviceGeneration: number | null;
}

export function sortHistoryTrades(
  trades: ReadonlyArray<Record<string, unknown>>,
): TradeRecord[];

export function historyInvalidatedByTradesChanged(event: unknown): boolean;

export function applyHistoryListResponse(
  currentState: HistoryListState | null,
  data: { trade_revision?: unknown; trades?: unknown } | null | undefined,
  serviceGeneration?: number | null,
): HistoryListState | null;

/** @deprecated Prefer historyInvalidatedByTradesChanged + applyHistoryListResponse. */
export function applyHistoryTradesChanged(
  currentState: HistoryListState | null,
  event: unknown,
): HistoryListState | null;
