/**
 * Tracks Replay cursor operations across the HTTP accept / WebSocket event race.
 *
 * The backend may publish `workbench_snapshot` or `operation_failed` on the
 * WebSocket channel before the renderer Promise for `stepReplay` /
 * `seekReplay` resolves. Without an early-outcome cache, the completion event
 * is ignored and `replayBusy` stays true forever.
 */

export function createReplayCursorTracker() {
  let activeOperationId = null;
  const earlyOutcomes = new Map();

  return {
    get activeOperationId() {
      return activeOperationId;
    },

    /**
     * Record a completion/failure that arrived before `adopt`.
     * Returns `"settled"` when it matches the active operation, `"cached"`
     * when stored for a later adopt, or `"ignored"` otherwise.
     */
    noteOutcome(operationId, kind) {
      if (typeof operationId !== "string" || operationId.length === 0) {
        return "ignored";
      }
      if (kind !== "completed" && kind !== "failed") {
        return "ignored";
      }
      if (activeOperationId === operationId) {
        activeOperationId = null;
        earlyOutcomes.delete(operationId);
        return "settled";
      }
      earlyOutcomes.set(operationId, kind);
      return "cached";
    },

    /**
     * Register the operation_id from an accepted HTTP response.
     * If an early outcome already exists, the operation is immediately settled.
     */
    adopt(operationId) {
      if (typeof operationId !== "string" || operationId.length === 0) {
        activeOperationId = null;
        return { status: "no_operation", early: null };
      }
      const early = earlyOutcomes.get(operationId) ?? null;
      earlyOutcomes.delete(operationId);
      if (early) {
        activeOperationId = null;
        return { status: "already_settled", early };
      }
      activeOperationId = operationId;
      return { status: "tracking", early: null };
    },

    clear() {
      activeOperationId = null;
      earlyOutcomes.clear();
    },
  };
}
