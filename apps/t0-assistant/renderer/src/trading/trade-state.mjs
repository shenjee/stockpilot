/**
 * Pure reducer helpers for the real-trade list state.
 *
 * The authoritative trade list arrives through the frozen `trades_changed`
 * event (event_type "trades_changed", `session_id: null` for real trades,
 * payload `real_trades_changed_payload` = `{ trade_revision, trades }`).
 * `list_trades` is only the initial hydration; these helpers drive all
 * event-driven updates. Revision gating ignores stale events so an older
 * delivery never overwrites a newer confirmed list. All functions are pure and
 * side-effect free so they can be unit-tested without React.
 */

/**
 * True when an app event is a repository-scoped real-trades change
 * (`trades_changed` with `session_id: null`). Simulated trades
 * (`session_id` non-null) belong to the Replay Session and are ignored here.
 */
export function isRealTradesChangedEvent(event) {
  return Boolean(
    event &&
      typeof event === "object" &&
      event.event_type === "trades_changed" &&
      event.session_id === null,
  );
}

function tradeDateOf(executedAt) {
  return typeof executedAt === "string" && executedAt.length >= 10
    ? executedAt.slice(0, 10)
    : null;
}

/**
 * Apply a `trades_changed` event to the current state.
 *
 * Returns the unchanged `currentState` when the event is stale
 * (`trade_revision <= currentState.tradeRevision`) or malformed, so an older
 * event never overwrites a newer list. Otherwise returns the event's trades
 * filtered to the drawer's current symbol and trading date (the event carries
 * the full repository list with no scope filter).
 *
 * @param {{trades: TradeRecord[], tradeRevision: number} | null} currentState
 * @param {object} event - the `trades_changed` app event
 * @param {{symbol: string, tradeDate: string}} scope
 */
export function applyTradesChanged(currentState, event, scope) {
  if (!isRealTradesChangedEvent(event)) return currentState;
  const payload = event.payload ?? {};
  const revision = Number.isInteger(payload.trade_revision)
    ? payload.trade_revision
    : null;
  if (revision === null) return currentState;
  const currentRevision = currentState?.tradeRevision ?? -1;
  if (revision <= currentRevision) return currentState;
  const allTrades = Array.isArray(payload.trades) ? payload.trades : [];
  const trades = allTrades.filter(
    (trade) =>
      trade &&
      trade.symbol === scope.symbol &&
      tradeDateOf(trade.executed_at) === scope.tradeDate,
  );
  return { trades, tradeRevision: revision };
}

/**
 * True when an `operation_failed` event matches one of the pending trade
 * operation ids. Returns the matched `{ operationId, error }` or `null`.
 */
export function matchTradeOperationFailed(event, pendingOperationIds) {
  if (
    !event ||
    typeof event !== "object" ||
    event.event_type !== "operation_failed"
  ) {
    return null;
  }
  const operationId =
    typeof event.operation_id === "string" ? event.operation_id : null;
  if (operationId === null || !pendingOperationIds.has(operationId)) {
    return null;
  }
  const error = event.payload;
  return { operationId, error };
}
