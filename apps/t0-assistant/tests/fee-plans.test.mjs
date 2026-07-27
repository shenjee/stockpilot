import test from "node:test";
import assert from "node:assert/strict";
import {
  createInMemoryFeePlanClient,
  createFeePlan,
  defaultFeePlan,
  DEFAULT_FEE_PLAN_ID,
  FeePlanValidationError,
  TransferFeeSide,
} from "../renderer/src/trading/fee-plans.mjs";

function planInput(overrides = {}) {
  return {
    fee_plan_id: "plan-1",
    name: "Test Plan",
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

test("default plan matches the shenwan-hongyuan seed", () => {
  const plan = defaultFeePlan();
  assert.equal(plan.fee_plan_id, DEFAULT_FEE_PLAN_ID);
  assert.equal(plan.name, "申万宏源（示例）");
  assert.equal(plan.a_share_commission_rate, "0.0003");
  assert.equal(plan.stamp_duty_sell_only, true);
  assert.equal(plan.transfer_fee_side, "both");
  assert.equal(plan.transfer_fee_enabled, true);
});

test("createFeePlan normalizes rates to trimmed decimal strings", () => {
  const plan = createFeePlan({
    ...planInput(),
    a_share_commission_rate: 0.0003,
    transfer_fee_rate: " 0.00001 ",
  });
  assert.equal(plan.a_share_commission_rate, "0.0003");
  assert.equal(plan.transfer_fee_rate, "0.00001");
  assert.equal(Object.isFrozen(plan), true);
});

test("createFeePlan rejects blank id and name", () => {
  assert.throws(
    () => createFeePlan(planInput({ fee_plan_id: "  " })),
    (e) => e instanceof FeePlanValidationError && e.field === "fee_plan_id",
  );
  assert.throws(
    () => createFeePlan(planInput({ name: "" })),
    (e) => e instanceof FeePlanValidationError && e.field === "name",
  );
});

test("createFeePlan rejects negative rates and bad side", () => {
  assert.throws(
    () => createFeePlan(planInput({ stamp_duty_rate: "-0.001" })),
    (e) => e instanceof FeePlanValidationError && e.field === "stamp_duty_rate",
  );
  assert.throws(
    () => createFeePlan(planInput({ transfer_fee_side: "none" })),
    (e) => e instanceof FeePlanValidationError && e.field === "transfer_fee_side",
  );
  assert.throws(
    () => createFeePlan(planInput({ stamp_duty_sell_only: "yes" })),
    (e) => e instanceof FeePlanValidationError && e.field === "stamp_duty_sell_only",
  );
});

test("in-memory client seeds the default plan and lists it", () => {
  const client = createInMemoryFeePlanClient();
  const plans = client.listPlans();
  assert.equal(plans.length, 1);
  assert.equal(plans[0].fee_plan_id, DEFAULT_FEE_PLAN_ID);
  assert.equal(client.getPlan(DEFAULT_FEE_PLAN_ID).name, "申万宏源（示例）");
});

test("in-memory client can create, update and delete a custom plan", () => {
  const client = createInMemoryFeePlanClient({ seed: false });
  const created = client.createPlan(planInput({ fee_plan_id: "custom-1" }));
  assert.equal(created.fee_plan_id, "custom-1");
  assert.equal(client.getPlan("custom-1"), created);

  const updated = client.updatePlan(
    planInput({ fee_plan_id: "custom-1", name: "Renamed" }),
  );
  assert.equal(updated.name, "Renamed");
  assert.equal(client.getPlan("custom-1").name, "Renamed");

  assert.equal(client.deletePlan("custom-1"), true);
  assert.equal(client.getPlan("custom-1"), null);
  assert.equal(client.deletePlan("custom-1"), false);
});

test("create rejects duplicate id and update rejects missing id", () => {
  const client = createInMemoryFeePlanClient({ seed: false });
  client.createPlan(planInput({ fee_plan_id: "dup" }));
  assert.throws(
    () => client.createPlan(planInput({ fee_plan_id: "dup" })),
    (e) => e instanceof FeePlanValidationError && e.field === "fee_plan_id",
  );
  assert.throws(
    () => client.updatePlan(planInput({ fee_plan_id: "missing" })),
    (e) => e instanceof FeePlanValidationError && e.field === "fee_plan_id",
  );
});

test("the default plan can be edited and deleted (no resurrection)", () => {
  const client = createInMemoryFeePlanClient();
  const edited = client.updatePlan({
    ...defaultFeePlan(),
    name: "申万宏源（已编辑）",
  });
  assert.equal(edited.name, "申万宏源（已编辑）");

  assert.equal(client.deletePlan(DEFAULT_FEE_PLAN_ID), true);
  assert.equal(client.listPlans().length, 0);
  // A fresh client re-seeds; this one does not resurrect.
  assert.equal(client.listPlans().length, 0);
});
