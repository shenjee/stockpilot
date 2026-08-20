/**
 * Pure helpers for the real-trade entry/edit form.
 *
 * Builds a `trade_draft` (matching `app-v2.schema.json`) from form fields,
 * validates each field with stable field-level errors, and normalizes a
 * minute-only execution time to seconds. Validation mirrors
 * `TradeDraft.from_mapping` in `packages/t0assistant/trading/models.py`; the
 * renderer keeps its own copy because the Python domain layer is not
 * transport-accessible. Only real trades are created here - Replay-simulated
 * trades belong to the Replay Session and never reach this form.
 *
 * Fee suggestion is NOT part of this module: the fee-calculation rule belongs
 * to `packages/t0assistant/trading/fee_policy.py` and the renderer obtains
 * suggestions through the `FeeAdvisor` port. The form persists whichever fee
 * the user confirms and never recomputes it.
 */

export class TradeFormValidationError extends Error {
  constructor(field, message) {
    super(`${field}: ${message}`);
    this.name = "TradeFormValidationError";
    this.field = field;
    this.message = message;
  }
}

const SYMBOL_PATTERN = /^(sh|sz)\.[0-9]{6}$/;
const TIMESTAMP_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/;

export function normalizeExecutedAt(value) {
  if (typeof value !== "string") {
    throw new TradeFormValidationError(
      "executed_at",
      "must use YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS",
    );
  }
  const match = TIMESTAMP_PATTERN.exec(value.trim());
  if (!match) {
    throw new TradeFormValidationError(
      "executed_at",
      "must use YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS",
    );
  }
  const [, year, month, day, hour, minute, second = "00"] = match;
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
}

function requireSymbol(value) {
  if (typeof value !== "string" || !SYMBOL_PATTERN.test(value.trim())) {
    throw new TradeFormValidationError("symbol", "must use sh.###### or sz.######");
  }
  return value.trim();
}

function requireSide(value) {
  if (value === "buy" || value === "sell") return value;
  throw new TradeFormValidationError("side", "must be buy or sell");
}

function requirePrice(value) {
  const n = typeof value === "number" ? value : Number(value);
  if (typeof value === "boolean" || !Number.isFinite(n) || n <= 0) {
    throw new TradeFormValidationError("price", "must be greater than zero");
  }
  return n;
}

function requireQuantity(value) {
  if (typeof value === "boolean") {
    throw new TradeFormValidationError("quantity", "must be a positive integer");
  }
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < 1) {
    throw new TradeFormValidationError("quantity", "must be a positive integer");
  }
  return n;
}

function optionalFee(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  if (typeof value === "boolean" || !Number.isFinite(n) || n < 0) {
    throw new TradeFormValidationError("fee", "must be zero or greater");
  }
  return n;
}

function optionalNote(value) {
  if (value === null || value === undefined) return "";
  if (typeof value !== "string") {
    throw new TradeFormValidationError("note", "must be a string");
  }
  return value;
}

function optionalFeePlanId(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string" || value.trim() === "") {
    throw new TradeFormValidationError("fee_plan_id", "must not be blank");
  }
  return value.trim();
}

/**
 * Validate form fields and return a `trade_draft` for `create_trade` /
 * `update_trade`. `trade_scope` is always `real` here.
 */
export function buildTradeDraft(fields, options = {}) {
  if (!fields || typeof fields !== "object") {
    throw new TradeFormValidationError("form", "must be a form object");
  }
  return Object.freeze({
    trade_scope:
      options.tradeScope === "simulated" ? "simulated" : "real",
    symbol: requireSymbol(fields.symbol),
    side: requireSide(fields.side),
    executed_at: normalizeExecutedAt(fields.executedAt ?? fields.executed_at),
    price: requirePrice(fields.price),
    quantity: requireQuantity(fields.quantity),
    fee: optionalFee(fields.fee),
    note: optionalNote(fields.note),
    fee_plan_id: optionalFeePlanId(fields.feePlanId ?? fields.fee_plan_id),
  });
}
