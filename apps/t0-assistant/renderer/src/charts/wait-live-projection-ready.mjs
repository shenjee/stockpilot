/**
 * Wait until a Live projection becomes ready for a target symbol/session,
 * or until the navigation sequence is superseded / timeout elapses.
 *
 * Cold select_security often leaves the projection not-ready; the authoritative
 * baseline arrives later via workbench_snapshot / live_session_status events
 * that notify the LiveProjectionController subscribers.
 */

/**
 * @param {object} args
 * @param {() => { serviceGeneration?: number | null, sessionId?: string | null, snapshot?: { session?: { symbol?: string, state?: string } } } | null | undefined} args.getProjection
 * @param {(listener: (projection: unknown) => void) => () => void} args.subscribe
 * @param {string} args.symbol
 * @param {string | null | undefined} args.sessionId
 * @param {number | null | undefined} args.serviceGeneration
 * @param {() => boolean} args.isCurrent
 * @param {number} [args.timeoutMs]
 * @returns {Promise<boolean>}
 */
export function waitForLiveProjectionReady({
  getProjection,
  subscribe,
  symbol,
  sessionId = null,
  serviceGeneration = null,
  isCurrent,
  timeoutMs = 20_000,
}) {
  function matches(projection) {
    if (!projection) return false;
    if (
      serviceGeneration != null &&
      projection.serviceGeneration !== serviceGeneration
    ) {
      return false;
    }
    if (projection.snapshot?.session?.symbol !== symbol) return false;
    if (projection.snapshot?.session?.state !== "ready") return false;
    if (sessionId != null && projection.sessionId !== sessionId) return false;
    return true;
  }

  if (!isCurrent()) return Promise.resolve(false);
  if (matches(getProjection())) return Promise.resolve(true);

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      unsubscribe();
      resolve(value);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    const unsubscribe = subscribe(() => {
      if (!isCurrent()) {
        finish(false);
        return;
      }
      if (matches(getProjection())) finish(true);
    });
    if (!isCurrent()) {
      finish(false);
      return;
    }
    if (matches(getProjection())) finish(true);
  });
}
