/**
 * Live workbench projection owner (#155 PR2).
 *
 * Holds the Live ChartProjection, applies incremental events and full
 * snapshots through the existing chart-projection reducers, and tracks the
 * Live rebaseline request key. React-free: subscribe() notifies adapters.
 */

import {
  applyLiveChartEvent,
  applyWorkbenchSnapshot,
  beginChartSession,
  createChartProjection,
} from "./chart-projection.mjs";

/**
 * @typedef {import("./chart-projection.mjs").ChartProjection} ChartProjection
 * @typedef {import("./chart-projection.mjs").ChartAppEvent} ChartAppEvent
 * @typedef {import("./chart-projection.mjs").ChartProjectionIdentity} ChartProjectionIdentity
 * @typedef {import("./chart-model.mjs").WorkbenchChartSnapshot} WorkbenchChartSnapshot
 */

export class LiveProjectionController {
  /**
   * @param {ChartProjection} initialProjection
   */
  constructor(initialProjection) {
    /** @type {ChartProjection} */
    this._projection = initialProjection;
    /** @type {string | null} */
    this._rebaselineRequest = null;
    /** @type {Set<() => void>} */
    this._listeners = new Set();
  }

  /** @returns {ChartProjection} */
  get projection() {
    return this._projection;
  }

  /** @returns {string | null} */
  get rebaselineRequestKey() {
    return this._rebaselineRequest;
  }

  /**
   * Replace the Live projection unconditionally (generation reset, historical
   * day chart, fixture bootstrap).
   * @param {ChartProjection} projection
   * @returns {ChartProjection}
   */
  replace(projection) {
    if (projection === this._projection) return this._projection;
    this._projection = projection;
    this._rebaselineRequest = null;
    this._notify();
    return this._projection;
  }

  /**
   * @param {WorkbenchChartSnapshot} snapshot
   * @param {number | null | undefined} serviceGeneration
   * @param {string | null | undefined} [sessionId]
   * @returns {ChartProjection}
   */
  beginSession(snapshot, serviceGeneration, sessionId = null) {
    this._projection = beginChartSession(
      snapshot,
      serviceGeneration,
      sessionId,
    );
    this._rebaselineRequest = null;
    this._notify();
    return this._projection;
  }

  /**
   * @param {WorkbenchChartSnapshot} snapshot
   * @param {ChartProjectionIdentity} identity
   * @returns {ChartProjection}
   */
  applySnapshot(snapshot, identity) {
    const next = applyWorkbenchSnapshot(
      this._projection,
      snapshot,
      identity,
    );
    if (next === this._projection) return this._projection;
    this._projection = next;
    this._rebaselineRequest = null;
    this._notify();
    return this._projection;
  }

  /**
   * @param {ChartAppEvent} event
   * @returns {ChartProjection}
   */
  applyEvent(event) {
    const next = applyLiveChartEvent(this._projection, event);
    if (next === this._projection) return this._projection;
    this._projection = next;
    this._notify();
    return this._projection;
  }

  /**
   * Reset to an empty chart for a new service generation.
   * @param {WorkbenchChartSnapshot} emptySnapshot
   * @param {number} serviceGeneration
   * @returns {ChartProjection}
   */
  resetForGeneration(emptySnapshot, serviceGeneration) {
    return this.replace(
      createChartProjection(emptySnapshot, {
        service_generation: serviceGeneration,
        session_id: null,
        revision: null,
      }),
    );
  }

  /**
   * Mark a Live rebaseline HTTP request as in-flight for this identity.
   * @param {string} requestKey
   * @returns {boolean} true when this is a new request key
   */
  beginRebaselineRequest(requestKey) {
    if (this._rebaselineRequest === requestKey) return false;
    this._rebaselineRequest = requestKey;
    return true;
  }

  clearRebaselineRequest() {
    this._rebaselineRequest = null;
  }

  /**
   * Mark the current Live projection as needing a full snapshot rebaseline
   * without mutating its visible snapshot. Used when a payload passes the
   * envelope check but fails chart-model contract probing (#155).
   * @returns {boolean} true when rebaselineRequired newly became true
   */
  requestRebaseline() {
    if (!this._projection || this._projection.rebaselineRequired) {
      return false;
    }
    this._projection = {
      ...this._projection,
      rebaselineRequired: true,
    };
    this._notify();
    return true;
  }

  /**
   * @param {(projection: ChartProjection) => void} listener
   * @returns {() => void}
   */
  subscribe(listener) {
    if (typeof listener !== "function") {
      throw new TypeError("subscribe requires a listener function");
    }
    this._listeners.add(listener);
    return () => this._listeners.delete(listener);
  }

  _notify() {
    const snapshot = this._projection;
    for (const listener of this._listeners) {
      try {
        listener(snapshot);
      } catch {
        // Listener errors must not break projection ownership.
      }
    }
  }
}
