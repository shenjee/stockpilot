export const SecurityType: Readonly<{ A_SHARE: "a_share"; ETF: "etf" }>;
export const TransferFeeSide: Readonly<{
  BUY: "buy";
  SELL: "sell";
  BOTH: "both";
}>;

export type SecurityTypeValue = "a_share" | "etf";
export type TransferFeeSideValue = "buy" | "sell" | "both";

export class FeePolicyValidationError extends Error {
  field: string;
  message: string;
}

export interface FeePlanRates {
  a_share_commission_rate: string | number;
  a_share_min_commission: string | number;
  etf_commission_rate: string | number;
  etf_min_commission: string | number;
  stamp_duty_rate: string | number;
  stamp_duty_sell_only: boolean;
  transfer_fee_rate: string | number;
  transfer_fee_side: TransferFeeSideValue;
  transfer_fee_enabled: boolean;
}

export interface FeeCalculationInput {
  securityType: SecurityTypeValue;
  side: "buy" | "sell";
  price: number | string;
  quantity: number | string;
}

export interface FeeCalculation {
  trade_amount: number;
  commission: number;
  stamp_duty: number;
  transfer_fee: number;
  total_fee: number;
}

export function calculateFee(
  plan: FeePlanRates,
  input: FeeCalculationInput,
): FeeCalculation;
