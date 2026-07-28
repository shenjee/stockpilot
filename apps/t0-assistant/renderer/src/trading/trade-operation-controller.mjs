/**
 * Persistent trade-operation registry + failure surface.
 *
 * A submitted real-trade create/update/delete may complete asynchronously:
 * the backend returns an accepted response with an `operation_id`, and the
 * authoritative success (`trades_changed`) or failure (`operation_failed`)
 * arrives later. The retry context (the captured draft / trade_id) must
 * outlive the TradeDrawer, which unmounts when the user switches to Replay.
 *
 * This controller is owned at the App level (always mounted) so:
 *   - a trade operation started in Live, then failing after the user switched
 *     to Replay, is still surfaced with the correct create/update/delete retry;
 *   - the App never silently drops an `operation_failed` whose
 *     `affected_capability` is `"trades"` (it routes it here instead of the
 *     generic `retryLive`/`retryService` path);
 *   - switching Live/Replay never destroys an in-flight real-trade operation.
 *
 * State-consistency guarantees:
 *   - An `operation_failed` may arrive before the accepted response is
 *     processed (cross-channel timing). `failUntracked` caches the failure
 *     keyed by `operation_id` with a null retry; a later `track` for the same
 *     id MERGES the retry/command into the cached failure instead of re-adding
 *     the op to pending, so the user can retry once the context arrives.
 *   - Multiple trade operations may be pending concurrently. Failures are kept
 *     in a `Map<operationId, failure>` so a later failure never overwrites an
 *     earlier one's retry; the UI surfaces all of them.
 *
 * The registry is plain JS (no React) so it is unit-testable without a DOM.
 * A small subscribe/notify lets an App-level React wrapper render a persistent
 * banner without coupling the controller to React.
 */

export class TradeOperationController {
  constructor() {
    /** @type {Map<string, {command: string, retry: () => Promise<void>}>} */
    this._pending = new Map();
    /**
     * Failures keyed by operation_id (null id for a truly anonymous failure).
     * A Map (not a single slot) so concurrent failures all survive.
     * @type {Map<string, TradeOperationFailure>}
     */
    this._failures = new Map();
    /** @type {Set<(failures: TradeOperationFailure[]) => void>} */
    this._listeners = new Set();
    /** Monotonic counter so each anonymous failure gets a distinct key. */
    this._anonymousSeq = 0;
  }

  /**
   * Track a pending async trade operation. The `retry` closure must re-run the
   * original create/update/delete and is invoked if the operation later fails.
   *
   * If a failure for this `operation_id` is already cached (the
   * `operation_failed` arrived before the accepted response), the retry/command
   * are MERGED into that failure and the op is NOT added to pending - it has
   * already failed, so it must not linger as in-flight.
   */
  track(operationId, { command, retry }) {
    if (typeof operationId !== "string" || operationId.length === 0) return;
    if (typeof retry !== "function") return;

    const cached = this._failures.get(operationId);
    if (cached) {
      // Early-fail merge: the op already failed before it was tracked. Fill in
      // the retry/command so the user can act, and keep it out of pending.
      cached.command = command;
      cached.retry = retry;
      this._notify();
      return;
    }

    this._pending.set(operationId, { command, retry });
  }

  has(operationId) {
    return (
      typeof operationId === "string" && this._pending.has(operationId)
    );
  }

  /**
   * Resolve (success) a pending operation by id - e.g. when `trades_changed`
   * carries the originating `operation_id`. Clears the pending op AND any
   * cached failure for the same id (a success supersedes a stale failure).
   * Returns true if a pending op or cached failure was resolved. Notifies
   * listeners only when a failure was actually cleared (a pending-op success is
   * not a failure event).
   */
  resolve(operationId) {
    if (typeof operationId !== "string" || operationId.length === 0) {
      return false;
    }
    const hadPending = this._pending.delete(operationId);
    const clearedFailure = this._failures.delete(operationId);
    if (clearedFailure) this._notify();
    return hadPending || clearedFailure;
  }

  /**
   * Record a trade-operation failure for a tracked pending op. The op leaves
   * pending and its failure (with the captured retry) is published. Returns
   * true if the op was tracked, false if it was untracked (use `failUntracked`
   * to surface an untracked failure instead).
   */
  fail(operationId, message, error) {
    if (typeof operationId !== "string" || operationId.length === 0) {
      return false;
    }
    const op = this._pending.get(operationId);
    if (!op) return false;
    this._pending.delete(operationId);
    this._failures.set(operationId, {
      operationId,
      command: op.command,
      message: typeof message === "string" ? message : "成交操作未完成",
      retry: op.retry,
      error,
    });
    this._notify();
    return true;
  }

  /**
   * Surface a trade failure that has no tracked pending op. This covers the
   * cross-channel timing case where `operation_failed` arrives before the
   * accepted response is processed. The `operation_id` (when present) is kept
   * so a later `track` for the same id can merge in the retry; until then the
   * failure is visible with a null retry. The failure is never silently
   * dropped.
   */
  failUntracked(operationId, message, error) {
    const id =
      typeof operationId === "string" && operationId.length > 0
        ? operationId
        : `__anonymous_${this._anonymousSeq++}`;
    // If an op with this id is somehow still pending (defensive), drop it so
    // the failure is the authoritative state for that id.
    this._pending.delete(operationId ?? "");
    this._failures.set(id, {
      operationId: typeof operationId === "string" ? operationId : null,
      command: null,
      message: typeof message === "string" ? message : "成交操作未完成",
      retry: null,
      error,
    });
    this._notify();
  }

  /** All current failures (oldest first), for the App banner. */
  get failures() {
    return Array.from(this._failures.values());
  }

  /** The first failure, for single-failure consumers. Null when there are none. */
  get failure() {
    for (const value of this._failures.values()) return value;
    return null;
  }

  /** Whether the controller currently tracks any pending operation. */
  hasPending() {
    return this._pending.size > 0;
  }

  /** Dismiss one failure by operation id (or anonymous key). */
  dismissFailure(operationId) {
    if (this._failures.delete(operationId)) {
      this._notify();
    }
  }

  /** Clear all failures (dismiss all). Pending ops are untouched. */
  dismissAllFailures() {
    if (this._failures.size === 0) return;
    this._failures.clear();
    this._notify();
  }

  /** Drop all pending operations (e.g. on a service_generation change). */
  clearPending() {
    if (this._pending.size === 0) return;
    this._pending.clear();
  }

  /** Subscribe to failure changes. Returns an unsubscribe function. */
  subscribe(listener) {
    if (typeof listener !== "function") {
      throw new TypeError("subscribe requires a listener function");
    }
    this._listeners.add(listener);
    return () => this._listeners.delete(listener);
  }

  _notify() {
    const snapshot = this.failures;
    for (const listener of this._listeners) {
      try {
        listener(snapshot);
      } catch {
        // A listener error must not break the registry.
      }
    }
  }
}

/**
 * @typedef {Object} TradeOperationFailure
 * @property {string | null} operationId
 * @property {string | null} command
 * @property {string} message
 * @property {(() => Promise<void>) | null} retry
 * @property {unknown} error
 */
