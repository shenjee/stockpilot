/**
 * Pure reducer helpers for the *historical* (full-repository) trade list.
 *
 * The authoritative trade list arrives through the frozen `trades_changed`
 * event (`session_id: null` for real trades, payload
 * `real_trades_changed_payload` = `{ trade_revision, trades }`). Unlike
 * `trade-state.mjs` - which filters the snapshot to the drawer's current
 * symbol/date - the history list shows EVERY persisted real trade across all
 * symbols and trading dates, so this reducer applies the same revision gate
 * but keeps the full (unfiltered) snapshot and sorts it for display.
 *
 * Display order (a renderer-side choice; the wire payload is unordered by
 * contract): `executed_at` descending (most recent first), then `trade_id`
 * ascending as a stable tiebreak. No sort selector is exposed to the user.
 *
 * Revision validity is scoped to a service generation: a revision is only
 * comparable within the same `service_generation`. When the Python service
 * restarts (new generation), the gate resets.
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
 * Apply a `trades_changed` event to the history-list state (no scope filter).
 *
 * The gate compares the `(service_generation, trade_revision)` pair: a stale
 * event (older generation, or same generation with revision <= current) is
 * ignored and the unchanged `currentState` is returned. An event from a newer
 * generation is accepted and resets the revision context. Malformed events
 * (no integer `trade_revision`) leave the state unchanged.
 *
 * @param {{trades: unknown[], tradeRevision: number, serviceGeneration: number | null} | null} currentState
 * @param {object} event - the `trades_changed` app event
 */
export function applyHistoryTradesChanged(currentState, event) {
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
    return currentState; // no generation tracking yet: revision gate only
  }

  const allTrades = Array.isArray(payload.trades) ? payload.trades : [];
  return {
    trades: sortHistoryTrades(allTrades),
    tradeRevision: revision,
    serviceGeneration: eventGen ?? stateGen,
  };
}
