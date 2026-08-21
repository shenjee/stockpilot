/**
 * Pure helpers for the historical (cross-symbol / cross-date) trade list.
 *
 * Issue #163: history is hydrated from the synchronous `list_trade_history`
 * response (`data: { trade_revision, trades }`). Scoped `trades_changed`
 * events only invalidate the open dialog so it re-fetches; they must NOT be
 * merged into the history list (their `trades` array is day-scoped, not a
 * full repository snapshot).
 *
 * Display order (a renderer-side choice; the wire payload is unordered by
 * contract): `executed_at` descending (most recent first), then `trade_id`
 * ascending as a stable tiebreak. No sort selector is exposed to the user.
 */

import { isRealTradesChangedEvent } from "./trade-state.mjs";

function integerOrNull(value) {
  return Number.isInteger(value) ? value : null;
}

/**
 * Sort trade records for history display: executed_at desc, trade_id asc.
 *
 * @param {Array<Record<string, unknown>>} trades
 * @returns {Array<Record<string, unknown>>}
 */
export function sortHistoryTrades(trades) {
  if (!Array.isArray(trades)) return [];
  return [...trades].sort((a, b) => {
    const at = typeof a?.executed_at === "string" ? a.executed_at : "";
    const bt = typeof b?.executed_at === "string" ? b.executed_at : "";
    if (at !== bt) return bt < at ? -1 : 1; // descending by time
    const aid = typeof a?.trade_id === "string" ? a.trade_id : "";
    const bid = typeof b?.trade_id === "string" ? b.trade_id : "";
    return aid < bid ? -1 : aid > bid ? 1 : 0; // stable tiebreak
  });
}

/**
 * True when a real `trades_changed` event should invalidate an open history
 * dialog (trigger a coalesced `list_trade_history` refresh). Does not apply
 * the scoped payload to history state.
 *
 * @param {unknown} event
 * @returns {boolean}
 */
export function historyInvalidatedByTradesChanged(event) {
  return isRealTradesChangedEvent(event);
}

/**
 * Apply a synchronous `list_trade_history` response to history-list state.
 *
 * Accepts `{ trade_revision, trades }` when the revision is newer than the
 * current gate (within the same `serviceGeneration`, or any revision when the
 * generation advanced). Older revisions are discarded. Malformed data leaves
 * state unchanged.
 *
 * @param {{trades: unknown[], tradeRevision: number, serviceGeneration: number | null} | null} currentState
 * @param {{ trade_revision?: unknown, trades?: unknown } | null | undefined} data
 * @param {number | null | undefined} serviceGeneration
 */
export function applyHistoryListResponse(currentState, data, serviceGeneration) {
  if (!data || typeof data !== "object") return currentState;
  const revision = integerOrNull(data.trade_revision);
  if (revision === null) return currentState;

  const stateGen = integerOrNull(currentState?.serviceGeneration);
  const nextGen = integerOrNull(serviceGeneration);
  const currentRevision = currentState?.tradeRevision ?? -1;

  if (stateGen !== null && nextGen !== null) {
    if (nextGen < stateGen) return currentState;
    if (nextGen === stateGen && revision <= currentRevision) {
      return currentState;
    }
  } else if (revision <= currentRevision && nextGen === stateGen) {
    return currentState;
  } else if (
    nextGen === null &&
    stateGen === null &&
    revision <= currentRevision
  ) {
    return currentState;
  }

  const allTrades = Array.isArray(data.trades) ? data.trades : [];
  return {
    trades: sortHistoryTrades(allTrades),
    tradeRevision: revision,
    serviceGeneration: nextGen ?? stateGen,
  };
}

/**
 * @deprecated Issue #163: scoped trades_changed must not replace history.
 * Prefer {@link historyInvalidatedByTradesChanged} + {@link applyHistoryListResponse}.
 * Kept as a thin invalidation signal for tests that only need to assert that
 * real trades_changed events are recognized (returns currentState unchanged).
 *
 * @param {{trades: unknown[], tradeRevision: number, serviceGeneration: number | null} | null} currentState
 * @param {object} event
 */
export function applyHistoryTradesChanged(currentState, event) {
  if (!historyInvalidatedByTradesChanged(event)) return currentState;
  // Do not merge scoped payload.trades into the history list.
  return currentState;
}
