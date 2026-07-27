export const DEFAULT_FEE_PLAN_ID: "shenwan-hongyuan";

export const TransferFeeSide: Readonly<{
  BUY: "buy";
  SELL: "sell";
  BOTH: "both";
}>;
export type TransferFeeSideValue = "buy" | "sell" | "both";

export class FeePlanValidationError extends Error {
  field: string;
  message: string;
}

export interface FeePlan {
  fee_plan_id: string;
  name: string;
  a_share_commission_rate: string;
  a_share_min_commission: string;
  etf_commission_rate: string;
  etf_min_commission: string;
  stamp_duty_rate: string;
  stamp_duty_sell_only: boolean;
  transfer_fee_rate: string;
  transfer_fee_side: TransferFeeSideValue;
  transfer_fee_enabled: boolean;
}

export interface FeePlanInput {
  fee_plan_id: string;
  name: string;
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

export interface FeePlanClient {
  listPlans(): FeePlan[];
  getPlan(feePlanId: string): FeePlan | null;
  createPlan(input: FeePlanInput): FeePlan;
  updatePlan(input: FeePlanInput): FeePlan;
  deletePlan(feePlanId: string): boolean;
}

export function createFeePlan(input: FeePlanInput): FeePlan;
export function defaultFeePlan(): FeePlan;
export function createInMemoryFeePlanClient(options?: {
  seed?: boolean;
}): FeePlanClient;
