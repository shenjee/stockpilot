import type { TradeDraft } from "./trade-form.d.mts";

export interface ApplicationError {
  error_code: string;
  message: string;
  retryable: boolean;
  affected_capability?: string;
  [key: string]: unknown;
}

export interface TradeRecord {
  trade_id: string;
  bucket_start: string;
  trade_scope: "real" | "simulated";
  symbol: string;
  side: "buy" | "sell";
  executed_at: string;
  price: number;
  quantity: number;
  fee: number | null;
  note: string;
  fee_plan_id: string | null;
}

export class TradeClientError extends Error {
  error: ApplicationError;
  readonly error_code: string;
  readonly retryable: boolean;
  readonly affected_capability: string;
}

export interface TradeClient {
  listTrades(args: {
    symbol: string;
    tradeDate: string;
    tradeScope?: "real" | "simulated";
  }): Promise<{ accepted: true; operationId: string | null }>;
  createTrade(
    draft: TradeDraft,
  ): Promise<{ accepted: true; operationId: string | null }>;
  updateTrade(
    tradeId: string,
    draft: TradeDraft,
  ): Promise<{ accepted: true; operationId: string | null }>;
  deleteTrade(
    tradeId: string,
  ): Promise<{ accepted: true; operationId: string | null }>;
}

export interface TradeBridge {
  listTrades(request: unknown): Promise<unknown>;
  createTrade(request: unknown): Promise<unknown>;
  updateTrade(request: unknown): Promise<unknown>;
  deleteTrade(request: unknown): Promise<unknown>;
}

export function createTradeClient(
  bridge: TradeBridge,
  options?: { makeRequestId?: (prefix: string) => string },
): TradeClient;
