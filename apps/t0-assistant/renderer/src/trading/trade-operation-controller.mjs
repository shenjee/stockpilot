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
 *   - Dually, a `trades_changed` (success) may arrive before the accepted
 *     response. `resolve` caches the resolved id; a later `track` for that id
 *     consumes the cached resolution and does NOT add the op to pending (it
 *     already succeeded) - no ghost pending.
 *   - Multiple trade operations may be pending concurrently. Failures are kept
 *     in a `Map<failureId, failure>` so a later failure never overwrites an
 *     earlier one's retry; the UI surfaces all of them.
 *   - Every failure has a stable, unique `failureId` DISTINCT from
 *     `operationId`, so an anonymous failure (no operation_id, e.g. a sync
 *     rejection) can still be dismissed and given a distinct React key.
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
     * Failures keyed by a stable `failureId` (NOT operationId). A Map so
     * concurrent failures all survive.
     * @type {Map<string, TradeOperationFailure>}
     */
    this._failures = new Map();
    /** operation_id -> failureId, for merging a later track() into an early failure. */
    this._failureIdByOp = new Map();
    /** Cached operation ids that resolved (success) before track() was called. */
    this._resolved = new Set();
    /** Monotonic counter for failureId generation. */
    this._failureSeq = 0;
    /** @type {Set<(failures: TradeOperationFailure[]) => void>} */
    this._listeners = new Set();
  }

  _nextFailureId() {
    this._failureSeq += 1;
    return `failure-${this._failureSeq}`;
  }

  /**
   * Track a pending async trade operation. The `retry` closure must re-run the
   * original create/update/delete and is invoked if the operation later fails.
   *
   * Reconciliation with early events:
   *   - If a failure for this `operation_id` is already cached (the
   *     `operation_failed` arrived first), the retry/command are MERGED into
   *     that failure and the op is NOT added to pending (it already failed).
   *   - If this `operation_id` was already resolved (a `trades_changed`
   *     arrived first), the cached resolution is consumed and the op is NOT
   *     added to pending (it already succeeded) - no ghost pending.
   */
  track(operationId, { command, retry }) {
    if (typeof operationId !== "string" || operationId.length === 0) return;
    if (typeof retry !== "function") return;

    if (this._resolved.has(operationId)) {
      // Early success: the op already completed. Drop the cached resolution
      // and do not add to pending.
      this._resolved.delete(operationId);
      return;
    }

    const failureId = this._failureIdByOp.get(operationId);
    if (failureId) {
      // Early-fail merge: the op already failed before it was tracked. Fill in
      // the retry/command so the user can act, and keep it out of pending.
      const cached = this._failures.get(failureId);
      if (cached) {
        cached.command = command;
        cached.retry = retry;
        this._notify();
      }
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
   * cached failure for the same id (a success supersedes a stale failure). If
   * the op is not yet tracked (the event arrived before the accepted
   * response), the id is cached so a later `track` consumes it instead of
   * adding a ghost pending. Returns true if a pending op or cached failure was
   * resolved (or the id was newly cached as resolved).
   */
  resolve(operationId) {
    if (typeof operationId !== "string" || operationId.length === 0) {
      return false;
    }
    const hadPending = this._pending.delete(operationId);
    const failureId = this._failureIdByOp.get(operationId);
    let clearedFailure = false;
    if (failureId) {
      this._failures.delete(failureId);
      this._failureIdByOp.delete(operationId);
      clearedFailure = true;
    }
    if (clearedFailure) {
      this._notify();
    } else if (!hadPending) {
      // Not yet tracked: cache the resolution so a later track() reconciles.
      // Return true to signal the id was newly cached as resolved.
      this._resolved.add(operationId);
      return true;
    }
    return hadPending || clearedFailure;
  }

  /**
   * Record a trade-operation failure for a tracked pending op. The op leaves
   * pending and its failure (with the captured retry) is published. Returns
   * the new failure's `failureId`, or `null` if the op was untracked (use
   * `failUntracked` to surface an untracked failure instead).
   */
  fail(operationId, message, error) {
    if (typeof operationId !== "string" || operationId.length === 0) {
      return null;
    }
    const op = this._pending.get(operationId);
    if (!op) return null;
    this._pending.delete(operationId);
    // A stale cached resolution is superseded by the failure.
    this._resolved.delete(operationId);
    const failureId = this._nextFailureId();
    this._failures.set(failureId, {
      failureId,
      operationId,
      command: op.command,
      message: typeof message === "string" ? message : "成交操作未完成",
      retry: op.retry,
      error,
    });
    this._failureIdByOp.set(operationId, failureId);
    this._notify();
    return failureId;
  }

  /**
   * Surface a trade failure that has no tracked pending op. Covers:
   *   - cross-channel timing: `operation_failed` arrives before the accepted
   *     response. The `operation_id` (when present) is kept so a later `track`
   *     for the same id can merge in the retry; until then the failure is
   *     visible with a null retry.
   *   - a sync rejection (no `operation_id`). `command`/`retry` may be supplied
   *     so the user can retry the failed create/update/delete again.
   * The failure is never silently dropped. Returns the new failure's
   * `failureId`.
   */
  failUntracked(operationId, message, error, { command = null, retry = null } = {}) {
    const failureId = this._nextFailureId();
    const hasOpId =
      typeof operationId === "string" && operationId.length > 0;
    if (hasOpId) {
      // If an op with this id is somehow still pending (defensive), drop it so
      // the failure is the authoritative state for that id.
      this._pending.delete(operationId);
      this._resolved.delete(operationId);
      this._failureIdByOp.set(operationId, failureId);
    }
    this._failures.set(failureId, {
      failureId,
      operationId: hasOpId ? operationId : null,
      command: command,
      message: typeof message === "string" ? message : "成交操作未完成",
      retry: typeof retry === "function" ? retry : null,
      error,
    });
    this._notify();
    return failureId;
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

  /** Dismiss one failure by its stable `failureId`. */
  dismissFailure(failureId) {
    if (typeof failureId !== "string" || failureId.length === 0) return;
    const removed = this._failures.get(failureId);
    if (!removed) return;
    this._failures.delete(failureId);
    if (removed.operationId) {
      this._failureIdByOp.delete(removed.operationId);
    }
    this._notify();
  }

  /** Clear all failures (dismiss all). Pending ops are untouched. */
  dismissAllFailures() {
    if (this._failures.size === 0) return;
    this._failures.clear();
    this._failureIdByOp.clear();
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
 * @property {string} failureId - stable, unique id for UI keying/dismiss
 * @property {string | null} operationId - backend operation_id, or null
 * @property {string | null} command
 * @property {string} message
 * @property {(() => Promise<void>) | null} retry
 * @property {unknown} error
 */
