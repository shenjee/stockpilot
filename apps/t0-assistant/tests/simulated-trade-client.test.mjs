import assert from "node:assert/strict";
import test from "node:test";

import { createSimulatedTradeClient } from "../renderer/src/trading/simulated-trade-client.mjs";
import { buildTradeDraft } from "../renderer/src/trading/trade-form.mjs";

function bridgeRecorder() {
  const calls = [];
  const bridge = {};
  for (const method of ["listTrades", "createTrade", "updateTrade", "deleteTrade"]) {
    bridge[method] = async (request) => {
      calls.push([method, request]);
      return { accepted: true, operation_id: null };
    };
  }
  return { bridge, calls };
}

test("Replay client scopes every command to its Session and simulated trades", async () => {
  const { bridge, calls } = bridgeRecorder();
  const client = createSimulatedTradeClient(bridge, "replay-1");
  const draft = buildTradeDraft(
    {
      symbol: "sh.600000",
      side: "buy",
      executedAt: "2026-07-01 10:23",
      price: 10.25,
      quantity: 200,
      fee: null,
      note: "",
      feePlanId: null,
    },
    { tradeScope: "simulated" },
  );

  await client.listTrades({ symbol: "sh.600000", tradeDate: "2026-07-01" });
  await client.createTrade(draft);
  await client.updateTrade("sim-1", draft);
  await client.deleteTrade("sim-1");

  assert.equal(calls.length, 4);
  for (const [, request] of calls) {
    assert.equal(request.session_id, "replay-1");
  }
  assert.equal(calls[0][1].payload.trade_scope, "simulated");
  assert.equal(calls[1][1].payload.trade.trade_scope, "simulated");
  assert.equal(calls[2][1].payload.trade.trade_scope, "simulated");
  assert.equal(calls[3][1].payload.trade_scope, "simulated");
});

test("simulated draft reuses shared validation and differs only by scope", () => {
  const draft = buildTradeDraft(
    {
      symbol: "sz.159915",
      side: "sell",
      executedAt: "2026-07-01 14:55",
      price: "2.123",
      quantity: "100",
    },
    { tradeScope: "simulated" },
  );
  assert.equal(draft.trade_scope, "simulated");
  assert.equal(draft.executed_at, "2026-07-01 14:55:00");
});
