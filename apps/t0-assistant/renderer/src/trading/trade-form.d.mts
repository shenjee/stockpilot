export class TradeFormValidationError extends Error {
  field: string;
  message: string;
}

export interface TradeFormFields {
  symbol: string;
  side: "buy" | "sell";
  executedAt?: string;
  executed_at?: string;
  price: number | string;
  quantity: number | string;
  fee?: number | string | null;
  note?: string;
  feePlanId?: string | null;
  fee_plan_id?: string | null;
}

export interface TradeDraft {
  trade_scope: "real";
  symbol: string;
  side: "buy" | "sell";
  executed_at: string;
  price: number;
  quantity: number;
  fee: number | null;
  note: string;
  fee_plan_id: string | null;
}

export function normalizeExecutedAt(value: string): string;
export function buildTradeDraft(fields: TradeFormFields): TradeDraft;
