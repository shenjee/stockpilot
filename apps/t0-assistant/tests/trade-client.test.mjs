import test from "node:test";
import assert from "node:assert/strict";
import {
  createTradeClient,
  TradeClientError,
} from "../renderer/src/trading/trade-client.mjs";

function fakeBridge(handlers = {}) {
  const calls = [];
  const defaults = {
    list_trades: (request) => ({
      schema_version: "t0_app_v1",
      request_id: request.request_id,
      accepted: true,
      operation_id: null,
      data: {
        trade_revision: 3,
        trades: [
          {
            trade_id: "trade-1",
            bucket_start: "2026-07-24 10:00:00",
            trade_scope: "real",
            symbol: "sh.600584",
            side: "buy",
            executed_at: "2026-07-24 10:03:00",
            price: 38.25,
            quantity: 200,
            fee: 5.01,
            note: "",
            fee_plan_id: "shenwan-hongyuan",
          },
        ],
      },
      error: null,
    }),
    create_trade: (request) => ({
      schema_version: "t0_app_v1",
      request_id: request.request_id,
      accepted: true,
      operation_id: "op-create-1",
      data: null,
      error: null,
    }),
    update_trade: (request) => ({
      schema_version: "t0_app_v1",
      request_id: request.request_id,
      accepted: true,
      operation_id: "op-update-1",
      data: null,
      error: null,
    }),
    delete_trade: (request) => ({
      schema_version: "t0_app_v1",
      request_id: request.request_id,
      accepted: true,
      operation_id: null,
      data: null,
      error: null,
    }),
  };
  const all = { ...defaults, ...handlers };
  function invoke(command, request) {
    // The bridge methods are bound to command names; emulate by command key.
    return Promise.resolve(all[command]?.(request));
  }
  return {
    calls,
    listTrades: (r) => { calls.push(["list_trades", r]); return invoke("list_trades", r); },
    createTrade: (r) => { calls.push(["create_trade", r]); return invoke("create_trade", r); },
    updateTrade: (r) => { calls.push(["update_trade", r]); return invoke("update_trade", r); },
    deleteTrade: (r) => { calls.push(["delete_trade", r]); return invoke("delete_trade", r); },
  };
}

function serviceUnavailable(request) {
  return {
    schema_version: "t0_app_v1",
    request_id: request.request_id,
    accepted: false,
    operation_id: null,
    data: null,
    error: {
      error_code: "service_unavailable",
      category: "service",
      severity: "error",
      retryable: true,
      affected_capability: "trades",
      message: "本地业务服务尚未接入",
      request_id: request.request_id,
      details: {},
    },
  };
}

const idFactory = (() => {
  let n = 0;
  return (prefix) => `${prefix}-${++n}`;
})();

function draft() {
  return {
    trade_scope: "real",
    symbol: "sh.600584",
    side: "buy",
    executed_at: "2026-07-24 10:03:00",
    price: 38.25,
    quantity: 200,
    fee: 5.01,
    note: "",
    fee_plan_id: "shenwan-hongyuan",
  };
}

test("listTrades maps an accepted response into trades and revision", async () => {
  const bridge = fakeBridge();
  const client = createTradeClient(bridge, { makeRequestId: idFactory });
  const result = await client.listTrades({
    symbol: "sh.600584",
    tradeDate: "2026-07-24",
  });
  assert.equal(result.tradeRevision, 3);
  assert.equal(result.trades.length, 1);
  assert.equal(result.trades[0].trade_id, "trade-1");

  const [command, request] = bridge.calls[0];
  assert.equal(command, "list_trades");
  assert.equal(request.schema_version, "t0_app_v1");
  assert.equal(request.command, "list_trades");
  assert.equal(request.session_id, null);
  assert.deepEqual(request.payload, {
    trade_scope: "real",
    symbol: "sh.600584",
    trade_date: "2026-07-24",
  });
});

test("createTrade sends create_trade payload and returns an acceptance signal", async () => {
  const bridge = fakeBridge();
  const client = createTradeClient(bridge, { makeRequestId: idFactory });
  const result = await client.createTrade(draft());
  // The accepted trade record arrives via the frozen trades_changed event,
  // not the sync response data (which is unfrozen). The client only signals
  // acceptance + the optional operation_id for the async failure path.
  assert.deepEqual(result, { accepted: true, operationId: "op-create-1" });
  const [, request] = bridge.calls[0];
  assert.equal(request.command, "create_trade");
  assert.deepEqual(request.payload, { trade: draft() });
});

test("updateTrade sends trade_id and trade; deleteTrade sends trade_scope real", async () => {
  const bridge = fakeBridge();
  const client = createTradeClient(bridge, { makeRequestId: idFactory });
  const updateResult = await client.updateTrade("trade-1", draft());
  assert.deepEqual(updateResult, { accepted: true, operationId: "op-update-1" });
  assert.deepEqual(bridge.calls[0][1].payload, {
    trade_id: "trade-1",
    trade: draft(),
  });
  const deleteResult = await client.deleteTrade("trade-1");
  assert.deepEqual(deleteResult, { accepted: true, operationId: null });
  assert.deepEqual(bridge.calls[1][1].payload, {
    trade_id: "trade-1",
    trade_scope: "real",
  });
});

test("an accepted-but-empty list response yields an empty trade list", async () => {
  const bridge = fakeBridge({
    list_trades: (request) => ({
      schema_version: "t0_app_v1",
      request_id: request.request_id,
      accepted: true,
      operation_id: null,
      data: null,
      error: null,
    }),
  });
  const client = createTradeClient(bridge, { makeRequestId: idFactory });
  const result = await client.listTrades({
    symbol: "sh.600584",
    tradeDate: "2026-07-24",
  });
  assert.deepEqual(result.trades, []);
  assert.equal(result.tradeRevision, 0);
});

test("service_unavailable response throws a retryable TradeClientError", async () => {
  const bridge = fakeBridge({
    create_trade: serviceUnavailable,
    list_trades: serviceUnavailable,
    update_trade: serviceUnavailable,
    delete_trade: serviceUnavailable,
  });
  const client = createTradeClient(bridge, { makeRequestId: idFactory });

  for (const op of [
    () => client.createTrade(draft()),
    () => client.updateTrade("trade-1", draft()),
    () => client.deleteTrade("trade-1"),
    () => client.listTrades({ symbol: "sh.600584", tradeDate: "2026-07-24" }),
  ]) {
    await assert.rejects(op, (err) => {
      assert.ok(err instanceof TradeClientError, "should be TradeClientError");
      assert.equal(err.retryable, true);
      assert.equal(err.affected_capability, "trades");
      assert.equal(err.error_code, "service_unavailable");
      return true;
    });
  }
});

test("an accepted:false response without a structured error still throws", async () => {
  const bridge = fakeBridge({
    delete_trade: (request) => ({
      schema_version: "t0_app_v1",
      request_id: request.request_id,
      accepted: false,
      operation_id: null,
      data: null,
      error: null,
    }),
  });
  const client = createTradeClient(bridge, { makeRequestId: idFactory });
  await assert.rejects(
    () => client.deleteTrade("trade-1"),
    (err) => err instanceof TradeClientError && err.retryable === true,
  );
});
