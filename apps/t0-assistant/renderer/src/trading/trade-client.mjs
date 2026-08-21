/**
 * Trade client port over the frozen Safe Bridge trade commands.
 *
 * Wraps `listTrades` / `listTradeHistory` / `createTrade` / `updateTrade` /
 * `deleteTrade` so the UI depends on a small, injectable port instead of the
 * bridge directly. Builds `command_request` envelopes (`session_id: null` —
 * real trades are not Session-scoped) and maps `command_response` into either
 * a result or a `TradeClientError` carrying the stable `application_error`.
 *
 * Issue #163:
 * - `list_trades` is fact-via-changed-event: accepted response has `data: null`;
 *   the authoritative scoped day list arrives through `trades_changed`.
 * - `list_trade_history` is synchronous: accepted response `data` is
 *   `{ trade_revision, trades }` and is returned to the caller for history UI.
 * - create/update/delete return only `{ accepted, operationId }`; the day list
 *   is refreshed via scoped `trades_changed`.
 */

export class TradeClientError extends Error {
  constructor(error) {
    super(error?.message ?? "成交请求未完成");
    this.name = "TradeClientError";
    this.error = error;
  }

  get error_code() {
    return this.error?.error_code ?? "trade_request_failed";
  }

  get retryable() {
    return Boolean(this.error?.retryable);
  }

  get affected_capability() {
    return this.error?.affected_capability ?? "trades";
  }
}

function defaultRequestId(prefix) {
  const rand =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${rand}`;
}

function extractError(response) {
  const error = response?.error;
  if (
    error &&
    typeof error.error_code === "string" &&
    typeof error.message === "string" &&
    typeof error.retryable === "boolean"
  ) {
    return error;
  }
  return null;
}

function ensureAccepted(response) {
  const error = extractError(response);
  if (error) throw new TradeClientError(error);
  if (!response || response.accepted === false) {
    throw new TradeClientError({
      error_code: "trade_request_failed",
      message: "成交请求未被接受，请稍后重试",
      retryable: true,
      affected_capability: "trades",
    });
  }
}

export function createTradeClient(bridge, options = {}) {
  if (!bridge || typeof bridge !== "object") {
    throw new TypeError("TradeClient requires a bridge");
  }
  const makeRequestId =
    typeof options.makeRequestId === "function"
      ? options.makeRequestId
      : defaultRequestId;

  function appRequest(command, payload) {
    return {
      schema_version: "t0_app_v2",
      request_id: makeRequestId(command),
      command,
      session_id: null,
      payload,
    };
  }

  async function listTrades({ symbol, tradeDate, tradeScope = "real" }) {
    const response = await bridge.listTrades(
      appRequest("list_trades", {
        trade_scope: tradeScope,
        symbol,
        trade_date: tradeDate,
      }),
    );
    ensureAccepted(response);
    // list_trades is a refresh trigger; accepted data is null. The authoritative
    // scoped day list arrives through trades_changed (see trade-state.mjs).
    return { accepted: true, operationId: extractOperationId(response) };
  }

  async function listTradeHistory({ tradeScope = "real" } = {}) {
    if (typeof bridge.listTradeHistory !== "function") {
      throw new TradeClientError({
        error_code: "trade_request_failed",
        message: "历史成交查询尚未接入",
        retryable: false,
        affected_capability: "trades",
      });
    }
    const response = await bridge.listTradeHistory(
      appRequest("list_trade_history", {
        trade_scope: tradeScope,
      }),
    );
    ensureAccepted(response);
    const data = response?.data;
    if (
      !data ||
      typeof data !== "object" ||
      !Number.isInteger(data.trade_revision) ||
      !Array.isArray(data.trades)
    ) {
      throw new TradeClientError({
        error_code: "trade_request_failed",
        message: "历史成交响应格式无效",
        retryable: true,
        affected_capability: "trades",
      });
    }
    return {
      trade_revision: data.trade_revision,
      trades: data.trades,
    };
  }

  async function createTrade(draft) {
    const response = await bridge.createTrade(
      appRequest("create_trade", { trade: draft }),
    );
    ensureAccepted(response);
    // The accepted trade record arrives via the frozen trades_changed event;
    // the sync response only signals acceptance (and an optional operation_id
    // for the async failure path). Do not assume data.trade.
    return { accepted: true, operationId: extractOperationId(response) };
  }

  async function updateTrade(tradeId, draft) {
    const response = await bridge.updateTrade(
      appRequest("update_trade", { trade_id: tradeId, trade: draft }),
    );
    ensureAccepted(response);
    return { accepted: true, operationId: extractOperationId(response) };
  }

  async function deleteTrade(tradeId) {
    const response = await bridge.deleteTrade(
      appRequest("delete_trade", { trade_id: tradeId, trade_scope: "real" }),
    );
    ensureAccepted(response);
    return { accepted: true, operationId: extractOperationId(response) };
  }

  return Object.freeze({
    listTrades,
    listTradeHistory,
    createTrade,
    updateTrade,
    deleteTrade,
  });
}

function extractOperationId(response) {
  const operationId = response?.operation_id;
  return typeof operationId === "string" && operationId.length > 0
    ? operationId
    : null;
}
