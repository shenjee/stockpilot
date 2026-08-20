/**
 * Replay-scoped trade client.
 *
 * It intentionally reuses the frozen App v1 trade commands. The non-null
 * session_id and simulated trade_scope route the command to Replay memory;
 * no persistence or fee-plan repository is involved.
 */

function requestId(prefix) {
  const suffix =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random()}`;
  return `${prefix}-${suffix}`;
}

function ensureAccepted(response) {
  if (response?.error) {
    const error = new Error(response.error.message ?? "模拟成交操作失败");
    error.cause = response.error;
    throw error;
  }
  if (!response || response.accepted === false) {
    throw new Error("模拟成交操作未被接受");
  }
  return {
    accepted: true,
    operationId:
      typeof response.operation_id === "string"
        ? response.operation_id
        : null,
  };
}

export function createSimulatedTradeClient(bridge, sessionId) {
  if (!bridge || typeof bridge !== "object") {
    throw new TypeError("bridge is required");
  }
  if (typeof sessionId !== "string" || sessionId.length === 0) {
    throw new TypeError("sessionId is required");
  }

  function commandRequest(command, payload) {
    return {
      schema_version: "t0_app_v2",
      request_id: requestId(`replay-${command}`),
      command,
      session_id: sessionId,
      payload,
    };
  }

  return Object.freeze({
    listTrades({ symbol, tradeDate }) {
      return bridge
        .listTrades(
          commandRequest("list_trades", {
            trade_scope: "simulated",
            symbol,
            trade_date: tradeDate,
          }),
        )
        .then(ensureAccepted);
    },
    createTrade(draft) {
      return bridge
        .createTrade(
          commandRequest("create_trade", {
            trade: { ...draft, trade_scope: "simulated" },
          }),
        )
        .then(ensureAccepted);
    },
    updateTrade(tradeId, draft) {
      return bridge
        .updateTrade(
          commandRequest("update_trade", {
            trade_id: tradeId,
            trade: { ...draft, trade_scope: "simulated" },
          }),
        )
        .then(ensureAccepted);
    },
    deleteTrade(tradeId) {
      return bridge
        .deleteTrade(
          commandRequest("delete_trade", {
            trade_id: tradeId,
            trade_scope: "simulated",
          }),
        )
        .then(ensureAccepted);
    },
  });
}
