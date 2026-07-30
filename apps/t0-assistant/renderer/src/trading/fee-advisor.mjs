/**
 * Port through which the renderer obtains a *suggested* default fee for a
 * trade draft.
 *
 * The fee-calculation rule itself is owned by
 * `packages/t0assistant/trading/fee_policy.py` (`architecture.md` §5.6); it is
 * NOT reimplemented in the renderer. The renderer calls this port, and a
 * App-v1 `calculate_trade_fee` supplies the production suggestion.
 * `createNullFeeAdvisor` remains the fixture fallback so tests never need to
 * duplicate the domain formula.
 *
 * The suggestion is never authoritative: the user may override it, and the
 * persisted fee is never recomputed when a fee plan later changes.
 */

import { FeePlanClientError } from "./fee-plans.mjs";

export function createNullFeeAdvisor() {
  return Object.freeze({
    suggestFee() {
      return null;
    },
  });
}

/**
 * Test/fixture advisor that delegates to an injected suggester. The suggester
 * must NOT reimplement the domain formula in production paths - tests use it
 * to return fixed values for deterministic assertions.
 */
export function createFakeFeeAdvisor(suggest) {
  if (typeof suggest !== "function") {
    throw new TypeError("createFakeFeeAdvisor requires a suggester function");
  }
  return Object.freeze({
    suggestFee(plan, input) {
      return suggest(plan, input);
    },
  });
}

export function createFeeAdvisor(bridge, options = {}) {
  if (!bridge || typeof bridge.calculateTradeFee !== "function") {
    throw new TypeError("FeeAdvisor requires a fee-capable bridge");
  }
  const makeRequestId =
    options.makeRequestId ??
    (() => `calculate_trade_fee-${globalThis.crypto?.randomUUID?.() ?? Date.now()}`);
  return Object.freeze({
    async suggestFee(plan, input) {
      if (!plan) return null;
      const quantity = Number(input.quantity);
      const response = await bridge.calculateTradeFee({
        schema_version: "t0_app_v1",
        request_id: makeRequestId(),
        command: "calculate_trade_fee",
        session_id: null,
        payload: {
          fee_plan_id: plan.fee_plan_id,
          security_type: input.securityType,
          side: input.side,
          price: input.price,
          quantity,
        },
      });
      if (!response?.accepted) {
        throw new FeePlanClientError(response?.error);
      }
      return response.data?.total_fee ?? null;
    },
  });
}
