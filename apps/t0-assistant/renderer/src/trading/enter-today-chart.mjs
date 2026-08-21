/**
 * Production orchestration for "进入当天图形" when the target date is today.
 *
 * Extracted from App.handleEnterDayChart so navigation-sequence sharing and
 * Replay retention on failure can be regression-tested without remounting React.
 *
 * @param {object} args
 * @param {string} args.symbol
 * @param {() => number} args.beginNavigation
 * @param {(sequence: number) => boolean} args.isCurrent
 * @param {(symbol: string) => Promise<object | null>} args.resolveSecurity
 * @param {(
 *   identity: object,
 *   restoring: boolean,
 *   options: { navigationSequence: number },
 * ) => Promise<boolean>} args.performSecuritySelection
 * @param {() => boolean} args.isReplayMode
 * @param {() => void} args.selectLiveMode
 * @returns {Promise<{ ok: true } | { ok: false, reason: "identity" | "stale" | "live_not_ready" }>}
 */
export async function enterTodayChart({
  symbol,
  beginNavigation,
  isCurrent,
  resolveSecurity,
  performSecuritySelection,
  isReplayMode,
  selectLiveMode,
}) {
  const requestSequence = beginNavigation();
  const identity = await resolveSecurity(symbol);
  if (!identity || !isCurrent(requestSequence)) {
    return { ok: false, reason: "identity" };
  }
  const liveReady = await performSecuritySelection(identity, false, {
    navigationSequence: requestSequence,
  });
  if (!isCurrent(requestSequence)) {
    return { ok: false, reason: "stale" };
  }
  if (!liveReady) {
    return { ok: false, reason: "live_not_ready" };
  }
  if (isReplayMode()) {
    selectLiveMode();
  }
  return { ok: true };
}
