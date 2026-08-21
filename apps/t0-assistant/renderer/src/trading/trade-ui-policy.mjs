/**
 * Pure Issue #163 trade UI / list policy for the visible workbench projection.
 *
 * Keeps App.tsx mounting and list_trades decisions testable without mounting
 * React: trade scope follows the visible session, indexes never list or mount
 * the trade drawer, and Replay / historical day charts stay read-only.
 */

/**
 * @param {object} args
 * @param {{ symbol?: string, trade_date?: string, session_type?: string } | null | undefined} args.session
 * @param {{ symbol: string, instrument_type: string } | null | undefined} args.workbenchSecurity
 * @param {"live" | "replay"} args.mode
 * @param {string} args.today
 */
export function resolveTradeUiPolicy({
  session,
  workbenchSecurity,
  mode,
  today,
}) {
  const visibleSymbol =
    typeof session?.symbol === "string" ? session.symbol : null;
  const visibleTradeDate =
    typeof session?.trade_date === "string" ? session.trade_date : today;
  const sessionType =
    typeof session?.session_type === "string" ? session.session_type : null;
  const visibleSecurity =
    workbenchSecurity != null &&
    workbenchSecurity.symbol === visibleSymbol
      ? workbenchSecurity
      : null;
  const isTradableSecurity =
    visibleSecurity != null && visibleSecurity.instrument_type !== "index";
  const isKnownNonTradableVisible =
    visibleSecurity != null && visibleSecurity.instrument_type === "index";
  const historicalChartVisible = sessionType === "historical";
  const tradeDrawerReadOnly = mode === "replay" || historicalChartVisible;
  const shouldMountTradeDrawer = isTradableSecurity;
  const shouldListTrades = Boolean(
    visibleSymbol && isTradableSecurity && !isKnownNonTradableVisible,
  );

  return {
    visibleSymbol,
    visibleTradeDate,
    sessionType,
    visibleSecurity,
    isTradableSecurity,
    isKnownNonTradableVisible,
    historicalChartVisible,
    tradeDrawerReadOnly,
    shouldMountTradeDrawer,
    shouldListTrades,
  };
}

/**
 * True when a Replay cursor change alone must not re-issue list_trades.
 * Listing deps are symbol + trade_date + tradability + service readiness;
 * play / step / seek only filter locally.
 *
 * @param {{ visibleSymbol: string | null, visibleTradeDate: string }} previous
 * @param {{ visibleSymbol: string | null, visibleTradeDate: string }} next
 */
export function replayCursorChangeRequiresRelist(previous, next) {
  return (
    previous.visibleSymbol !== next.visibleSymbol ||
    previous.visibleTradeDate !== next.visibleTradeDate
  );
}
