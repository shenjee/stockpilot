/**
 * Domain fee-calculation rules for A-share and ETF trades (renderer side).
 *
 * Mirrors `packages/t0assistant/trading/fee_policy.py` so the renderer can
 * suggest a default fee for a trade draft. The suggested fee is never
 * authoritative: the user may override it, and the persisted fee is never
 * recomputed when a fee plan later changes. All monetary math uses exact
 * BigInt-based decimal arithmetic so rates like 0.0003 and 0.00001 do not
 * accumulate binary-float error.
 */

export const SecurityType = Object.freeze({
  A_SHARE: "a_share",
  ETF: "etf",
});

export const TransferFeeSide = Object.freeze({
  BUY: "buy",
  SELL: "sell",
  BOTH: "both",
});

export class FeePolicyValidationError extends Error {
  constructor(field, message) {
    super(`${field}: ${message}`);
    this.name = "FeePolicyValidationError";
    this.field = field;
    this.message = message;
  }
}

const ZERO = Object.freeze({ coeff: 0n, scale: 0 });

function asNonNegativeDecimal(value, field) {
  if (value === null || value === undefined || typeof value === "boolean") {
    throw new FeePolicyValidationError(
      field,
      "must be a finite non-negative number",
    );
  }
  let text;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value < 0) {
      throw new FeePolicyValidationError(
        field,
        "must be a finite non-negative number",
      );
    }
    text = String(value);
  } else if (typeof value === "string") {
    text = value.trim();
  } else {
    throw new FeePolicyValidationError(
      field,
      "must be a finite non-negative number",
    );
  }
  if (!/^\d+(\.\d+)?$/.test(text)) {
    throw new FeePolicyValidationError(
      field,
      "must be a finite non-negative number",
    );
  }
  const [intPart, fracPart = ""] = text.split(".");
  let coeff = BigInt(intPart + fracPart || "0");
  let scale = fracPart.length;
  while (scale > 0 && coeff % 10n === 0n) {
    coeff /= 10n;
    scale -= 1;
  }
  return { coeff, scale };
}

function normalize(value) {
  let { coeff, scale } = value;
  while (scale > 0 && coeff % 10n === 0n) {
    coeff /= 10n;
    scale -= 1;
  }
  return { coeff, scale };
}

function mul(a, b) {
  return normalize({ coeff: a.coeff * b.coeff, scale: a.scale + b.scale });
}

function raiseTo(d, scale) {
  let { coeff, scale: current } = d;
  while (current < scale) {
    coeff *= 10n;
    current += 1;
  }
  return { coeff, scale: current };
}

function align(a, b) {
  const scale = Math.max(a.scale, b.scale);
  return [raiseTo(a, scale), raiseTo(b, scale)];
}

function add(a, b) {
  const [x, y] = align(a, b);
  return { coeff: x.coeff + y.coeff, scale: x.scale };
}

function compare(a, b) {
  const [x, y] = align(a, b);
  if (x.coeff < y.coeff) return -1;
  if (x.coeff > y.coeff) return 1;
  return 0;
}

function maxOf(a, b) {
  return compare(a, b) >= 0 ? a : b;
}

function decimalToString(d) {
  let text = d.coeff.toString();
  if (d.scale > 0) {
    if (text.length <= d.scale) {
      text = "0".repeat(d.scale - text.length + 1) + text;
    }
    text = `${text.slice(0, -d.scale)}.${text.slice(-d.scale)}`;
  }
  return text;
}

function toNumber(d) {
  return Number(decimalToString(d));
}

function normalizeSecurityType(value) {
  if (value === SecurityType.A_SHARE || value === "a_share") return "a_share";
  if (value === SecurityType.ETF || value === "etf") return "etf";
  throw new FeePolicyValidationError(
    "security_type",
    "must be one of: a_share, etf",
  );
}

function normalizeSide(value) {
  if (value === "buy" || value === "sell") return value;
  throw new FeePolicyValidationError("side", "must be one of: buy, sell");
}

function normalizeQuantity(value) {
  if (typeof value === "boolean" || value === null || value === undefined) {
    throw new FeePolicyValidationError("quantity", "must be a positive integer");
  }
  let n;
  if (typeof value === "number") {
    if (!Number.isInteger(value)) {
      throw new FeePolicyValidationError("quantity", "must be a positive integer");
    }
    n = value;
  } else if (typeof value === "string") {
    if (!/^\d+$/.test(value)) {
      throw new FeePolicyValidationError("quantity", "must be a positive integer");
    }
    n = Number(value);
  } else {
    throw new FeePolicyValidationError("quantity", "must be a positive integer");
  }
  if (n < 1) {
    throw new FeePolicyValidationError("quantity", "must be a positive integer");
  }
  return n;
}

function planRate(plan, field) {
  const raw = plan?.[field];
  return asNonNegativeDecimal(raw, field);
}

/**
 * Return the default fee breakdown for a trade.
 *
 * @param {object} plan - structured fee plan (string rate fields, booleans)
 * @param {{securityType: string, side: string, price: number|string, quantity: number|string}} input
 * @returns {{trade_amount: number, commission: number, stamp_duty: number, transfer_fee: number, total_fee: number}}
 */
export function calculateFee(plan, input) {
  if (!plan || typeof plan !== "object") {
    throw new FeePolicyValidationError("plan", "must be a fee plan");
  }
  const securityType = normalizeSecurityType(input?.securityType);
  const side = normalizeSide(input?.side);
  const price = asNonNegativeDecimal(input?.price, "price");
  if (price.coeff === 0n) {
    throw new FeePolicyValidationError("price", "must be greater than zero");
  }
  const quantity = normalizeQuantity(input?.quantity);
  const amount = mul(price, { coeff: BigInt(quantity), scale: 0 });

  const isEtf = securityType === "etf";
  const commissionRate = planRate(
    plan,
    isEtf ? "etf_commission_rate" : "a_share_commission_rate",
  );
  const minCommission = planRate(
    plan,
    isEtf ? "etf_min_commission" : "a_share_min_commission",
  );
  const commission = maxOf(mul(amount, commissionRate), minCommission);

  const stampDutyRate = planRate(plan, "stamp_duty_rate");
  let stampDuty;
  if (side === "sell") {
    stampDuty = mul(amount, stampDutyRate);
  } else if (plan.stamp_duty_sell_only) {
    stampDuty = ZERO;
  } else {
    stampDuty = mul(amount, stampDutyRate);
  }

  let transferFee;
  if (!plan.transfer_fee_enabled) {
    transferFee = ZERO;
  } else {
    const sideFlag = plan.transfer_fee_side;
    if (sideFlag === TransferFeeSide.BOTH || sideFlag === side) {
      transferFee = mul(amount, planRate(plan, "transfer_fee_rate"));
    } else {
      transferFee = ZERO;
    }
  }

  const total = add(add(commission, stampDuty), transferFee);
  return {
    trade_amount: toNumber(amount),
    commission: toNumber(commission),
    stamp_duty: toNumber(stampDuty),
    transfer_fee: toNumber(transferFee),
    total_fee: toNumber(total),
  };
}
