/**
 * Renderer-side fee-plan values, validation and an in-memory client port.
 *
 * There is no frozen transport contract for fee plans yet (the frozen
 * `StockPilotBridge` / `app-v1.schema.json` cover trades and preferences
 * only). Per T0-041's non-goals, this issue does not add a public contract or
 * backend CRUD. The UI therefore talks to a `FeePlanClient` port backed by an
 * in-memory store seeded with the editable "申万宏源（示例）" default plan
 * (matching T0-039's `FeePlanService.DEFAULT_PLAN_ID`). When a fee-plan
 * transport contract lands, a bridge-backed client replaces this one without
 * touching the UI. Fee-plan values mirror `FeePlanRecord` in
 * `packages/t0assistant/repositories/trading.py`.
 */

import { TransferFeeSide } from "./fee-policy.mjs";

export const DEFAULT_FEE_PLAN_ID = "shenwan-hongyuan";

export class FeePlanValidationError extends Error {
  constructor(field, message) {
    super(`${field}: ${message}`);
    this.name = "FeePlanValidationError";
    this.field = field;
    this.message = message;
  }
}

const RATE_FIELDS = [
  "a_share_commission_rate",
  "a_share_min_commission",
  "etf_commission_rate",
  "etf_min_commission",
  "stamp_duty_rate",
  "transfer_fee_rate",
];

function nonBlankString(value, field) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new FeePlanValidationError(field, "must not be blank");
  }
  return value.trim();
}

function nonNegativeDecimalString(value, field) {
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value < 0) {
      throw new FeePlanValidationError(field, "must be a finite non-negative number");
    }
    value = String(value);
  }
  if (typeof value !== "string" || !/^\d+(\.\d+)?$/.test(value.trim())) {
    throw new FeePlanValidationError(field, "must be a finite non-negative number");
  }
  return value.trim();
}

function booleanValue(value, field) {
  if (typeof value !== "boolean") {
    throw new FeePlanValidationError(field, "must be a boolean");
  }
  return value;
}

function transferFeeSideValue(value) {
  if (
    value === TransferFeeSide.BUY ||
    value === TransferFeeSide.SELL ||
    value === TransferFeeSide.BOTH
  ) {
    return value;
  }
  throw new FeePlanValidationError(
    "transfer_fee_side",
    "must be one of: buy, sell, both",
  );
}

/**
 * Validate and normalize a fee-plan input into an immutable fee-plan object.
 * Rate fields are coerced to trimmed decimal strings for exact downstream math.
 */
export function createFeePlan(input) {
  if (!input || typeof input !== "object") {
    throw new FeePlanValidationError("fee_plan", "must be a fee plan object");
  }
  const plan = {
    fee_plan_id: nonBlankString(input.fee_plan_id, "fee_plan_id"),
    name: nonBlankString(input.name, "name"),
    a_share_commission_rate: nonNegativeDecimalString(
      input.a_share_commission_rate,
      "a_share_commission_rate",
    ),
    a_share_min_commission: nonNegativeDecimalString(
      input.a_share_min_commission,
      "a_share_min_commission",
    ),
    etf_commission_rate: nonNegativeDecimalString(
      input.etf_commission_rate,
      "etf_commission_rate",
    ),
    etf_min_commission: nonNegativeDecimalString(
      input.etf_min_commission,
      "etf_min_commission",
    ),
    stamp_duty_rate: nonNegativeDecimalString(
      input.stamp_duty_rate,
      "stamp_duty_rate",
    ),
    stamp_duty_sell_only: booleanValue(
      input.stamp_duty_sell_only,
      "stamp_duty_sell_only",
    ),
    transfer_fee_rate: nonNegativeDecimalString(
      input.transfer_fee_rate,
      "transfer_fee_rate",
    ),
    transfer_fee_side: transferFeeSideValue(input.transfer_fee_side),
    transfer_fee_enabled: booleanValue(
      input.transfer_fee_enabled,
      "transfer_fee_enabled",
    ),
  };
  return Object.freeze(plan);
}

export function defaultFeePlan() {
  return createFeePlan({
    fee_plan_id: DEFAULT_FEE_PLAN_ID,
    name: "申万宏源（示例）",
    a_share_commission_rate: "0.0003",
    a_share_min_commission: "5",
    etf_commission_rate: "0.0002",
    etf_min_commission: "5",
    stamp_duty_rate: "0.0005",
    stamp_duty_sell_only: true,
    transfer_fee_rate: "0.00001",
    transfer_fee_side: TransferFeeSide.BOTH,
    transfer_fee_enabled: true,
  });
}

/**
 * In-memory `FeePlanClient` for T0-041. Seeded once with the default plan so
 * the UI is immediately usable. Edits live in renderer memory; persistence is
 * deferred to the future fee-plan transport contract issue. All mutations
 * return validated, immutable plan snapshots.
 */
export function createInMemoryFeePlanClient({ seed = true } = {}) {
  const store = new Map();

  function listPlans() {
    return Array.from(store.values()).sort((a, b) =>
      a.name === b.name
        ? a.fee_plan_id.localeCompare(b.fee_plan_id)
        : a.name.localeCompare(b.name, "zh-Hans-CN"),
    );
  }

  function getPlan(feePlanId) {
    return store.get(feePlanId) ?? null;
  }

  function createPlan(input) {
    const plan = createFeePlan(input);
    if (store.has(plan.fee_plan_id)) {
      throw new FeePlanValidationError(
        "fee_plan_id",
        `收费方案已存在：${plan.fee_plan_id}`,
      );
    }
    store.set(plan.fee_plan_id, plan);
    return plan;
  }

  function updatePlan(input) {
    const plan = createFeePlan(input);
    if (!store.has(plan.fee_plan_id)) {
      throw new FeePlanValidationError(
        "fee_plan_id",
        `收费方案不存在：${plan.fee_plan_id}`,
      );
    }
    store.set(plan.fee_plan_id, plan);
    return plan;
  }

  function deletePlan(feePlanId) {
    if (typeof feePlanId !== "string" || feePlanId.trim() === "") {
      throw new FeePlanValidationError("fee_plan_id", "must not be blank");
    }
    return store.delete(feePlanId);
  }

  if (seed) {
    const seeded = defaultFeePlan();
    store.set(seeded.fee_plan_id, seeded);
  }

  return Object.freeze({
    listPlans,
    getPlan,
    createPlan,
    updatePlan,
    deletePlan,
  });
}
