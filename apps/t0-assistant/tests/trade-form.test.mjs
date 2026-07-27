import test from "node:test";
import assert from "node:assert/strict";
import {
  buildTradeDraft,
  normalizeExecutedAt,
  TradeFormValidationError,
} from "../renderer/src/trading/trade-form.mjs";

function fields(overrides = {}) {
  return {
    symbol: "sh.600584",
    side: "buy",
    executedAt: "2026-07-24 10:03:47",
    price: "38.25",
    quantity: "200",
    fee: "5.01",
    note: "manual fill",
    feePlanId: "shenwan-hongyuan",
    ...overrides,
  };
}

test("normalizeExecutedAt appends :00 to a minute-only input", () => {
  assert.equal(
    normalizeExecutedAt("2026-07-24 10:03"),
    "2026-07-24 10:03:00",
  );
  assert.equal(
    normalizeExecutedAt("2026-07-24 10:03:47"),
    "2026-07-24 10:03:47",
  );
});

test("normalizeExecutedAt rejects malformed timestamps", () => {
  assert.throws(
    () => normalizeExecutedAt("2026-7-24 10:03"),
    (e) => e instanceof TradeFormValidationError && e.field === "executed_at",
  );
  assert.throws(
    () => normalizeExecutedAt("not a time"),
    (e) => e instanceof TradeFormValidationError && e.field === "executed_at",
  );
});

test("buildTradeDraft builds a real-scope trade_draft", () => {
  const draft = buildTradeDraft(fields());
  assert.deepEqual(draft, {
    trade_scope: "real",
    symbol: "sh.600584",
    side: "buy",
    executed_at: "2026-07-24 10:03:47",
    price: 38.25,
    quantity: 200,
    fee: 5.01,
    note: "manual fill",
    fee_plan_id: "shenwan-hongyuan",
  });
  assert.equal(Object.isFrozen(draft), true);
});

test("buildTradeDraft normalizes minute-only executed_at", () => {
  const draft = buildTradeDraft(fields({ executedAt: "2026-07-24 10:03" }));
  assert.equal(draft.executed_at, "2026-07-24 10:03:00");
});

test("buildTradeDraft accepts null fee and empty fee_plan_id (不计算)", () => {
  const draft = buildTradeDraft(fields({ fee: "", feePlanId: "" }));
  assert.equal(draft.fee, null);
  assert.equal(draft.fee_plan_id, null);
});

test("buildTradeDraft defaults note to empty string", () => {
  const draft = buildTradeDraft(fields({ note: undefined }));
  assert.equal(draft.note, "");
});

test("buildTradeDraft rejects bad symbol, side, price, quantity", () => {
  assert.throws(
    () => buildTradeDraft(fields({ symbol: "600584" })),
    (e) => e.field === "symbol",
  );
  assert.throws(
    () => buildTradeDraft(fields({ side: "hold" })),
    (e) => e.field === "side",
  );
  assert.throws(
    () => buildTradeDraft(fields({ price: 0 })),
    (e) => e.field === "price",
  );
  assert.throws(
    () => buildTradeDraft(fields({ quantity: "1.5" })),
    (e) => e.field === "quantity",
  );
  assert.throws(
    () => buildTradeDraft(fields({ quantity: 0 })),
    (e) => e.field === "quantity",
  );
});

test("buildTradeDraft rejects negative fee", () => {
  assert.throws(
    () => buildTradeDraft(fields({ fee: "-1" })),
    (e) => e.field === "fee",
  );
});
