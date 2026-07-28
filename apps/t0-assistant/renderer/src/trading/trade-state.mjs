/**
 * Pure reducer helpers for the real-trade list state.
 *
 * The authoritative trade list arrives through the frozen `trades_changed`
 * event (event_type "trades_changed", `session_id: null` for real trades,
 * payload `real_trades_changed_payload` = `{ trade_revision, trades }`).
 * `list_trades` is only a refresh trigger; its sync `command_response.data`
 * shape is unfrozen and is NOT consumed for state. All functions are pure and
 * side-effect free so they can be unit-tested without React.
 *
 * Revision validity is scoped to a service generation: a revision is only
 * comparable within the same `service_generation`. When the Python service
 * restarts (new generation), revision numbering restarts, so the gate compares
 * the `(service_generation, trade_revision)` pair and the component resets the
 * gate on a generation change.
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

function integerOrNull(value) {
  return Number.isInteger(value) ? value : null;
}

/**
 * Apply a `trades_changed` event to the current state.
 *
 * The gate compares the `(service_generation, trade_revision)` pair: an event
 * is stale (ignored, returning the unchanged `currentState`) when it is from
 * an older generation, or from the same generation with a revision not greater
 * than the current one. An event from a newer generation is accepted and
 * resets the revision context (so a fresh generation's low revision is not
 * rejected by a stale high revision from the previous generation).
 *
 * Returns `currentState` unchanged for malformed events (no integer
 * `trade_revision`). Otherwise returns the event's trades filtered to the
 * drawer's current symbol and trading date (the event carries the full
 * repository list with no scope filter).
 *
 * @param {{trades: TradeRecord[], tradeRevision: number, serviceGeneration: number | null} | null} currentState
 * @param {object} event - the `trades_changed` app event
 * @param {{symbol: string, tradeDate: string}} scope
 */
export function applyTradesChanged(currentState, event, scope) {
  if (!isRealTradesChangedEvent(event)) return currentState;
  const payload = event.payload ?? {};
  const revision = integerOrNull(payload.trade_revision);
  if (revision === null) return currentState;

  const stateGen = integerOrNull(currentState?.serviceGeneration);
  const eventGen = integerOrNull(event.service_generation);
  const currentRevision = currentState?.tradeRevision ?? -1;

  if (stateGen !== null && eventGen !== null) {
    if (eventGen < stateGen) return currentState; // stale generation
    if (eventGen === stateGen && revision <= currentRevision) {
      return currentState; // stale revision within the same generation
    }
  } else if (revision <= currentRevision) {
    // No generation tracking yet: fall back to the revision gate.
    return currentState;
  }

  const allTrades = Array.isArray(payload.trades) ? payload.trades : [];
  const trades = allTrades.filter(
    (trade) =>
      trade &&
      trade.symbol === scope.symbol &&
      tradeDateOf(trade.executed_at) === scope.tradeDate,
  );
  return {
    trades,
    tradeRevision: revision,
    serviceGeneration: eventGen ?? stateGen,
  };
}

/**
 * The pending operation id a `trades_changed` event resolves (success).
 *
 * `trades_changed` may carry the originating `operation_id` (optional on the
 * frozen envelope). When it does and the id is tracked, that pending operation
 * has succeeded and should be cleared from the pending map. Returns the id, or
 * `null` when the event carries no tracked `operation_id` (so unrelated
 * pending operations are never cleared by a blanket event).
 *
 * @param {object} event
 * @param {{has(id: string): boolean}} pendingOperations - a Set or Map of pending op ids
 */
export function pendingOpResolvedByTradesChanged(event, pendingOperations) {
  if (!isRealTradesChangedEvent(event)) return null;
  const operationId =
    typeof event.operation_id === "string" ? event.operation_id : null;
  if (operationId === null || !pendingOperations.has(operationId)) return null;
  return operationId;
}

/**
 * True when an `operation_failed` event matches one of the pending trade
 * operation ids. Returns the matched `{ operationId, error }` or `null`.
 *
 * @param {object} event
 * @param {{has(id: string): boolean}} pendingOperations - a Set or Map of pending op ids
 */
export function matchTradeOperationFailed(event, pendingOperations) {
  if (
    !event ||
    typeof event !== "object" ||
    event.event_type !== "operation_failed"
  ) {
    return null;
  }
  const operationId =
    typeof event.operation_id === "string" ? event.operation_id : null;
  if (operationId === null || !pendingOperations.has(operationId)) {
    return null;
  }
  const error = event.payload;
  return { operationId, error };
}
