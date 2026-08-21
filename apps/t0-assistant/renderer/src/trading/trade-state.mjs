/**
 * Pure reducer helpers for the real-trade list state.
 *
 * The authoritative day list arrives through the frozen `trades_changed`
 * event (event_type "trades_changed", `session_id: null` for real trades).
 * Issue #163: the payload is already scoped to `{ symbol, trade_date,
 * trade_revision, trades }` — `trades` is the authoritative list for that
 * scope only. `list_trades` is only a refresh trigger; its sync
 * `command_response.data` is null and is NOT consumed for state.
 *
 * Revision validity is scoped to a service generation: a revision is only
 * comparable within the same `service_generation`. When the Python service
 * restarts (new generation), revision numbering restarts, so the gate compares
 * the `(service_generation, trade_revision)` pair and the component resets the
 * gate on a generation change.
 */

/**
 * True when an app event is a real-trades change
 * (`trades_changed` with `session_id: null`). Session-scoped events
 * (`session_id` non-null) are ignored here.
 */
export function isRealTradesChangedEvent(event) {
  return Boolean(
    event &&
      typeof event === "object" &&
      event.event_type === "trades_changed" &&
      event.session_id === null,
  );
}

function integerOrNull(value) {
  return Number.isInteger(value) ? value : null;
}

/**
 * Keep trades whose `executed_at` is at or before the Replay cursor.
 * Used so play/step/seek only filter locally and never re-list.
 *
 * @param {Array<Record<string, unknown>>} trades
 * @param {string | null | undefined} currentTime
 * @returns {Array<Record<string, unknown>>}
 */
export function filterTradesByReplayCursor(trades, currentTime) {
  if (!Array.isArray(trades)) return [];
  if (typeof currentTime !== "string" || currentTime.length === 0) {
    return [...trades];
  }
  return trades.filter(
    (trade) =>
      trade &&
      typeof trade.executed_at === "string" &&
      trade.executed_at <= currentTime,
  );
}

/**
 * Apply a scoped `trades_changed` event to the current day-list state.
 *
 * After the generation/revision gate:
 * - Matching `payload.symbol` + `payload.trade_date` replaces `trades` with
 *   the authoritative scoped list (do NOT re-filter individual records).
 * - A non-matching scope still advances `tradeRevision` / `serviceGeneration`
 *   but keeps the existing trades array — a revision bump alone must not
 *   clear the day list.
 *
 * Returns `currentState` unchanged for malformed events (no integer
 * `trade_revision`).
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

  const nextGeneration = eventGen ?? stateGen;
  const scopeMatches =
    typeof payload.symbol === "string" &&
    typeof payload.trade_date === "string" &&
    payload.symbol === scope.symbol &&
    payload.trade_date === scope.tradeDate;

  if (scopeMatches) {
    const trades = Array.isArray(payload.trades) ? payload.trades : [];
    return {
      trades,
      tradeRevision: revision,
      serviceGeneration: nextGeneration,
    };
  }

  // Non-matching scope: advance the gate, keep the existing day list.
  return {
    trades: currentState?.trades ?? [],
    tradeRevision: revision,
    serviceGeneration: nextGeneration,
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
