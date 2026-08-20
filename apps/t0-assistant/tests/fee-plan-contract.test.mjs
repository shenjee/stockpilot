import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildSafeBridge } from "../electron/safe-bridge.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(
  await readFile(resolve(testDir, "../contracts/app-v1.schema.json"), "utf8"),
);

const FEE_COMMANDS = [
  "list_fee_plans",
  "create_fee_plan",
  "update_fee_plan",
  "delete_fee_plan",
  "calculate_trade_fee",
];

test("App v1 owns the complete persistent fee-plan command surface", () => {
  const commands = schema.$defs.command_request.properties.command.enum;
  for (const command of FEE_COMMANDS) {
    assert.equal(commands.includes(command), true, `${command} must be frozen`);
  }
  assert.equal(schema.$defs.fee_plan.additionalProperties, false);
  assert.deepEqual(
    schema.$defs.fee_plan.properties.transfer_fee_side.enum,
    ["buy", "sell", "both"],
  );
  assert.deepEqual(
    schema.$defs.calculate_trade_fee_payload.properties.security_type.enum,
    ["a_share", "etf"],
  );
});

test("Safe Bridge exposes fee-plan persistence and calculation without transport details", async () => {
  const calls = [];
  const bridge = buildSafeBridge({
    invoke(command, request) {
      calls.push({ command, request });
      return Promise.resolve({ accepted: true, data: {} });
    },
    subscribe() {
      return () => {};
    },
  });
  const request = {
    schema_version: "t0_app_v2",
    request_id: "fee-contract-1",
    command: "list_fee_plans",
    session_id: null,
    payload: {},
  };
  await bridge.listFeePlans(request);
  assert.deepEqual(calls, [{ command: "list_fee_plans", request }]);
  for (const method of [
    "createFeePlan",
    "updateFeePlan",
    "deleteFeePlan",
    "calculateTradeFee",
  ]) {
    assert.equal(typeof bridge[method], "function");
  }
});
