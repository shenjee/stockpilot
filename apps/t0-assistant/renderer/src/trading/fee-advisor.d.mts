import type { FeePlan } from "./fee-plans.d.mts";

export interface FeeSuggestionInput {
  securityType: "a_share" | "etf";
  side: "buy" | "sell";
  price: number | string;
  quantity: number | string;
}

export interface FeeAdvisor {
  suggestFee(plan: FeePlan | null, input: FeeSuggestionInput): number | null | Promise<number | null>;
}

export function createNullFeeAdvisor(): FeeAdvisor;
export function createFakeFeeAdvisor(
  suggest: (plan: FeePlan | null, input: FeeSuggestionInput) => number | null,
): FeeAdvisor;
export function createFeeAdvisor(
  bridge: object,
  options?: {makeRequestId?: () => string},
): FeeAdvisor;
