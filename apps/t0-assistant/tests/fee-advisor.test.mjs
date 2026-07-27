import test from "node:test";
import assert from "node:assert/strict";
import {
  createNullFeeAdvisor,
  createFakeFeeAdvisor,
} from "../renderer/src/trading/fee-advisor.mjs";
import { defaultFeePlan } from "../renderer/src/trading/fee-plans.mjs";

const input = {
  securityType: "a_share",
  side: "buy",
  price: "10.00",
  quantity: 100,
};

test("null advisor returns no suggestion (backend fee rule not reimplemented)", () => {
  const advisor = createNullFeeAdvisor();
  assert.equal(advisor.suggestFee(defaultFeePlan(), input), null);
  assert.equal(advisor.suggestFee(null, input), null);
});

test("fake advisor delegates to the injected suggester", () => {
  const advisor = createFakeFeeAdvisor(() => 5.01);
  assert.equal(advisor.suggestFee(defaultFeePlan(), input), 5.01);
});

test("fake advisor can return null like the null advisor", () => {
  const advisor = createFakeFeeAdvisor(() => null);
  assert.equal(advisor.suggestFee(defaultFeePlan(), input), null);
});

test("createFakeFeeAdvisor requires a function", () => {
  assert.throws(
    () => createFakeFeeAdvisor("not a fn"),
    (err) => err instanceof TypeError,
  );
});

test("advisors are frozen", () => {
  assert.ok(Object.isFrozen(createNullFeeAdvisor()));
  assert.ok(Object.isFrozen(createFakeFeeAdvisor(() => 1)));
});
