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
 *   - a trade operation_started in Live, then failing after the user switched
 *     to Replay, is still surfaced with the correct create/update/delete retry;
 *   - the App never silently drops an `operation_failed` whose
 *     `affected_capability` is `"trades"` (it routes it here instead of the
 *     generic `retryLive`/`retryService` path);
 *   - switching Live/Replay never destroys an in-flight real-trade operation.
 *
 * The registry is plain JS (no React) so it is unit-testable without a DOM.
 * A small subscribe/notify lets an App-level React wrapper render a persistent
 * banner without coupling the controller to React.
 */

export class TradeOperationController {
  constructor() {
    /** @type {Map<string, {command: string, retry: () => Promise<void>}>} */
    this._pending = new Map();
    /** @type {{message: string, retry: (() => Promise<void>) | null, command: string | null} | null} */
    this._failed = null;
    /** @type {Set<(failed: unknown) => void>} */
    this._listeners = new Set();
  }

  /**
   * Track a pending async trade operation. The `retry` closure must re-run the
   * original create/update/delete and is invoked if the operation later fails.
   */
  track(operationId, { command, retry }) {
    if (typeof operationId !== "string" || operationId.length === 0) return;
    if (typeof retry !== "function") return;
    this._pending.set(operationId, {
      command: command,
      retry,
    });
  }

  has(operationId) {
    return (
      typeof operationId === "string" && this._pending.has(operationId)
    );
  }

  /**
   * Resolve (success) a pending operation by id - e.g. when `trades_changed`
   * carries the originating `operation_id`. Only the matched op is cleared;
   * other in-flight ops keep their tracking. Clears any stale failure for the
   * same op. Returns true if an op was resolved.
   */
  resolve(operationId) {
    if (!this.has(operationId)) return false;
    this._pending.delete(operationId);
    if (
      this._failed &&
      this._failed.operationId === operationId
    ) {
      this._clearFailed();
    }
    return true;
  }

  /**
   * Record a trade-operation failure. Resolves the pending op (it is no longer
   * in flight) and publishes the failure with the captured retry so the App
   * banner can re-run the original operation. Returns true if the failure was
   * claimed (the op was tracked), false if it was untracked.
   */
  fail(operationId, message, error) {
    const op = this._pending.get(operationId);
    if (!op) return false;
    this._pending.delete(operationId);
    this._failed = {
      operationId,
      command: op.command,
      message: typeof message === "string" ? message : "成交操作未完成",
      retry: op.retry,
      error,
    };
    this._notify();
    return true;
  }

  /** Surface a trade failure with no tracked pending op (e.g. an operation_failed
   *  whose id the controller never saw, due to cross-channel timing). The
   *  failure is still shown so it is not silently dropped; retry is null. */
  failUntracked(message, error) {
    this._failed = {
      operationId: null,
      command: null,
      message: typeof message === "string" ? message : "成交操作未完成",
      retry: null,
      error,
    };
    this._notify();
  }

  /** The current failure (for the App banner), or null. */
  get failure() {
    return this._failed;
  }

  /** Whether the controller currently tracks any pending operation. */
  hasPending() {
    return this._pending.size > 0;
  }

  /** Clear the current failure (dismiss). Pending ops are untouched. */
  dismissFailure() {
    this._clearFailed();
  }

  /** Drop all pending operations (e.g. on a service_generation change). */
  clearPending() {
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

  _clearFailed() {
    if (this._failed === null) return;
    this._failed = null;
    this._notify();
  }

  _notify() {
    for (const listener of this._listeners) {
      try {
        listener(this._failed);
      } catch {
        // A listener error must not break the registry.
      }
    }
  }
}
