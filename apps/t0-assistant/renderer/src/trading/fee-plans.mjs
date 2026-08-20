/**
 * Renderer-side fee-plan values, validation and an in-memory client port.
 *
 * App-v1 owns the persistent fee-plan command surface. Production uses the
 * bridge-backed client below; the in-memory client remains a fixture/test
 * stand-in seeded with the same editable "申万宏源（示例）" default plan.
 *
 * This module owns the fee-plan *data model* (value + validation) only. The
 * fee *calculation rule* is NOT reimplemented here - it belongs to
 * `packages/t0assistant/trading/fee_policy.py`. The renderer obtains suggested
 * fees through the `FeeAdvisor` port in `fee-advisor.mjs`.
 *
 * Fee-plan values mirror `FeePlanRecord` in
 * `packages/t0assistant/repositories/trading.py`.
 */

export const DEFAULT_FEE_PLAN_ID = "shenwan-hongyuan";

export const TransferFeeSide = Object.freeze({
  BUY: "buy",
  SELL: "sell",
  BOTH: "both",
});

export class FeePlanValidationError extends Error {
  constructor(field, message) {
    super(`${field}: ${message}`);
    this.name = "FeePlanValidationError";
    this.field = field;
    this.message = message;
  }
}

export class FeePlanClientError extends Error {
  constructor(error) {
    super(error?.message ?? "收费方案操作未完成");
    this.name = "FeePlanClientError";
    this.error = error;
    this.retryable = Boolean(error?.retryable);
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

/** Persistent production client over the App-v1 Safe Bridge. */
export function createFeePlanClient(bridge, options = {}) {
  if (!bridge || typeof bridge.listFeePlans !== "function") {
    throw new TypeError("FeePlanClient requires a fee-plan capable bridge");
  }
  const makeRequestId =
    options.makeRequestId ??
    ((command) =>
      `${command}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`);
  const request = (command, payload) => ({
    schema_version: "t0_app_v2",
    request_id: makeRequestId(command),
    command,
    session_id: null,
    payload,
  });
  async function invoke(method, command, payload) {
    const response = await bridge[method](request(command, payload));
    if (!response?.accepted) {
      throw new FeePlanClientError(response?.error);
    }
    return response.data;
  }
  return Object.freeze({
    async listPlans() {
      const data = await invoke("listFeePlans", "list_fee_plans", {});
      return (data?.fee_plans ?? []).map(createFeePlan);
    },
    async getPlan(feePlanId) {
      const plans = await this.listPlans();
      return plans.find((plan) => plan.fee_plan_id === feePlanId) ?? null;
    },
    async createPlan(input) {
      const plan = createFeePlan(input);
      const data = await invoke("createFeePlan", "create_fee_plan", {
        fee_plan: plan,
      });
      return createFeePlan(data.fee_plan);
    },
    async updatePlan(input) {
      const plan = createFeePlan(input);
      const data = await invoke("updateFeePlan", "update_fee_plan", {
        fee_plan: plan,
      });
      return createFeePlan(data.fee_plan);
    },
    async deletePlan(feePlanId) {
      const data = await invoke("deleteFeePlan", "delete_fee_plan", {
        fee_plan_id: nonBlankString(feePlanId, "fee_plan_id"),
      });
      return data?.deleted === true;
    },
  });
}
