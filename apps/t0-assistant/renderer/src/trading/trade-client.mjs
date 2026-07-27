/**
 * Trade client port over the frozen Safe Bridge trade commands.
 *
 * Wraps `listTrades` / `createTrade` / `updateTrade` / `deleteTrade` so the UI
 * depends on a small, injectable port instead of the bridge directly. Builds
 * `command_request` envelopes (`session_id: null` - real trades are
 * repository-scoped, not Session-scoped) and maps `command_response` into
 * either a result or a `TradeClientError` carrying the stable
 * `application_error`. The bridge methods themselves are frozen and are not
 * modified here.
 *
 * Response-shape contract note: the frozen `command_response.data` is only
 * `object | null` - the per-command success `data` shapes are NOT frozen.
 * create/update/delete therefore return only an acceptance signal
 * (`{ accepted, operationId }`); the authoritative trade list arrives through
 * the frozen `trades_changed` event (see `trade-state.mjs`), which the UI
 * consumes separately. `listTrades` is retained for initial hydration; its
 * `data.trades` / `data.trade_revision` shape is a provisional assumption
 * pending a future `list_trades` response-shape freeze (out of scope here).
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
      schema_version: "t0_app_v1",
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
    // Provisional: command_response.data is only `object | null` in the frozen
    // contract. The {trades, trade_revision} shape is assumed pending a
    // list_trades response freeze. The authoritative list is the
    // trades_changed event; this is only the initial hydration.
    const data = response.data ?? {};
    return {
      trades: Array.isArray(data.trades) ? data.trades : [],
      tradeRevision: Number.isInteger(data.trade_revision)
        ? data.trade_revision
        : 0,
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

  return Object.freeze({ listTrades, createTrade, updateTrade, deleteTrade });
}

function extractOperationId(response) {
  const operationId = response?.operation_id;
  return typeof operationId === "string" && operationId.length > 0
    ? operationId
    : null;
}
