import test from "node:test";
import assert from "node:assert/strict";
import {
  calculateFee,
  FeePolicyValidationError,
  SecurityType,
  TransferFeeSide,
} from "../renderer/src/trading/fee-policy.mjs";

function plan(overrides = {}) {
  return {
    a_share_commission_rate: "0.0003",
    a_share_min_commission: "5",
    etf_commission_rate: "0.0002",
    etf_min_commission: "5",
    stamp_duty_rate: "0.0005",
    stamp_duty_sell_only: true,
    transfer_fee_rate: "0.00001",
    transfer_fee_side: TransferFeeSide.BOTH,
    transfer_fee_enabled: true,
    ...overrides,
  };
}

function approx(actual, expected, message) {
  assert.ok(
    Math.abs(actual - expected) < 1e-9,
    `${message ?? ""} expected ${expected}, got ${actual}`,
  );
}

test("A-share small buy hits minimum commission and no stamp duty", () => {
  const r = calculateFee(plan(), {
    securityType: SecurityType.A_SHARE,
    side: "buy",
    price: "10.00",
    quantity: 100,
  });
  approx(r.trade_amount, 1000.0, "trade_amount");
  approx(r.commission, 5.0, "commission");
  approx(r.stamp_duty, 0, "stamp_duty");
  approx(r.transfer_fee, 0.01, "transfer_fee");
  approx(r.total_fee, 5.01, "total_fee");
});

test("A-share sell includes stamp duty and transfer fee", () => {
  const r = calculateFee(plan(), {
    securityType: SecurityType.A_SHARE,
    side: "sell",
    price: "10.00",
    quantity: 1000,
  });
  approx(r.trade_amount, 10000.0, "trade_amount");
  approx(r.commission, 5.0, "commission");
  approx(r.stamp_duty, 5.0, "stamp_duty");
  approx(r.transfer_fee, 0.1, "transfer_fee");
  approx(r.total_fee, 10.1, "total_fee");
});

test("ETF buy and sell use ETF commission config", () => {
  const buy = calculateFee(plan(), {
    securityType: SecurityType.ETF,
    side: "buy",
    price: "2.50",
    quantity: 1000,
  });
  approx(buy.trade_amount, 2500.0, "buy amount");
  approx(buy.commission, 5.0, "buy commission");
  approx(buy.stamp_duty, 0, "buy stamp");
  approx(buy.transfer_fee, 0.025, "buy transfer");

  const sell = calculateFee(plan(), {
    securityType: SecurityType.ETF,
    side: "sell",
    price: "2.50",
    quantity: 1000,
  });
  approx(sell.stamp_duty, 1.25, "sell stamp");
  approx(sell.transfer_fee, 0.025, "sell transfer");
});

test("transfer fee disabled is zero", () => {
  const r = calculateFee(plan({ transfer_fee_enabled: false }), {
    securityType: SecurityType.A_SHARE,
    side: "buy",
    price: "10000.00",
    quantity: 1,
  });
  approx(r.transfer_fee, 0, "transfer_fee");
});

test("transfer fee buy-only charges buy only", () => {
  const p = plan({ transfer_fee_side: TransferFeeSide.BUY });
  const buy = calculateFee(p, {
    securityType: SecurityType.A_SHARE,
    side: "buy",
    price: "10000.00",
    quantity: 1,
  });
  const sell = calculateFee(p, {
    securityType: SecurityType.A_SHARE,
    side: "sell",
    price: "10000.00",
    quantity: 1,
  });
  approx(buy.transfer_fee, 0.1, "buy transfer");
  approx(sell.transfer_fee, 0, "sell transfer");
});

test("transfer fee sell-only charges sell only", () => {
  const p = plan({ transfer_fee_side: TransferFeeSide.SELL });
  const buy = calculateFee(p, {
    securityType: SecurityType.A_SHARE,
    side: "buy",
    price: "10000.00",
    quantity: 1,
  });
  const sell = calculateFee(p, {
    securityType: SecurityType.A_SHARE,
    side: "sell",
    price: "10000.00",
    quantity: 1,
  });
  approx(buy.transfer_fee, 0, "buy transfer");
  approx(sell.transfer_fee, 0.1, "sell transfer");
});

test("transfer fee both charges both sides", () => {
  const p = plan({ transfer_fee_side: TransferFeeSide.BOTH });
  for (const side of ["buy", "sell"]) {
    const r = calculateFee(p, {
      securityType: SecurityType.A_SHARE,
      side,
      price: "10000.00",
      quantity: 1,
    });
    approx(r.transfer_fee, 0.1, `${side} transfer`);
  }
});

test("zero minimum commission plan uses rate commission", () => {
  const r = calculateFee(plan({ a_share_min_commission: "0" }), {
    securityType: SecurityType.A_SHARE,
    side: "buy",
    price: "10.00",
    quantity: 100,
  });
  approx(r.commission, 0.3, "commission");
});

test("stamp duty is bidirectional when sell-only is false", () => {
  const r = calculateFee(plan({ stamp_duty_sell_only: false }), {
    securityType: SecurityType.A_SHARE,
    side: "buy",
    price: "10000.00",
    quantity: 1,
  });
  approx(r.stamp_duty, 5.0, "buy stamp");
});

test("numeric inputs are accepted alongside strings", () => {
  const r = calculateFee(plan(), {
    securityType: "a_share",
    side: "buy",
    price: 10,
    quantity: "100",
  });
  approx(r.trade_amount, 1000, "amount");
});

test("invalid security type is rejected", () => {
  assert.throws(
    () =>
      calculateFee(plan(), {
        securityType: "future",
        side: "buy",
        price: "10.00",
        quantity: 100,
      }),
    (err) => err instanceof FeePolicyValidationError && err.field === "security_type",
  );
});

test("invalid side is rejected", () => {
  assert.throws(
    () =>
      calculateFee(plan(), {
        securityType: SecurityType.A_SHARE,
        side: "hold",
        price: "10.00",
        quantity: 100,
      }),
    (err) => err instanceof FeePolicyValidationError && err.field === "side",
  );
});

test("non-positive price is rejected", () => {
  for (const price of [0, -1, "abc", NaN, null, true]) {
    assert.throws(
      () =>
        calculateFee(plan(), {
          securityType: SecurityType.A_SHARE,
          side: "buy",
          price,
          quantity: 100,
        }),
      (err) => err instanceof FeePolicyValidationError && err.field === "price",
      `price=${String(price)}`,
    );
  }
});

test("non-positive quantity is rejected", () => {
  for (const quantity of [0, -1, "abc", null, true, 1.5]) {
    assert.throws(
      () =>
        calculateFee(plan(), {
          securityType: SecurityType.A_SHARE,
          side: "buy",
          price: "10.00",
          quantity,
        }),
      (err) => err instanceof FeePolicyValidationError && err.field === "quantity",
      `quantity=${String(quantity)}`,
    );
  }
});
