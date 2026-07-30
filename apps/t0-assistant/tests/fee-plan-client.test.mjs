import test from "node:test";
import assert from "node:assert/strict";
import { createFeePlanClient, defaultFeePlan } from "../renderer/src/trading/fee-plans.mjs";
import { createFeeAdvisor } from "../renderer/src/trading/fee-advisor.mjs";

test("production fee clients persist through the Safe Bridge and use backend fee policy", async () => {
  const calls = [];
  const plan = defaultFeePlan();
  const bridge = {
    async listFeePlans(request) {
      calls.push(request);
      return { accepted: true, data: { fee_plans: [plan] }, error: null };
    },
    async createFeePlan() {
      return { accepted: true, data: { fee_plan: plan }, error: null };
    },
    async updateFeePlan() {
      return { accepted: true, data: { fee_plan: { ...plan, name: "已编辑" } }, error: null };
    },
    async deleteFeePlan() {
      return { accepted: true, data: { deleted: true }, error: null };
    },
    async calculateTradeFee(request) {
      calls.push(request);
      return { accepted: true, data: { total_fee: 8.9025 }, error: null };
    },
  };
  const client = createFeePlanClient(bridge, { makeRequestId: (c) => `id-${c}` });
  assert.equal((await client.listPlans())[0].fee_plan_id, "shenwan-hongyuan");
  assert.equal((await client.updatePlan({ ...plan, name: "已编辑" })).name, "已编辑");
  assert.equal(await client.deletePlan(plan.fee_plan_id), true);

  const advisor = createFeeAdvisor(bridge, { makeRequestId: () => "id-fee" });
  const fee = await advisor.suggestFee(plan, {
    securityType: "a_share",
    side: "sell",
    price: "38.25",
    quantity: "200",
  });
  assert.equal(fee, 8.9025);
  assert.equal(calls.at(-1).payload.quantity, 200);
});
