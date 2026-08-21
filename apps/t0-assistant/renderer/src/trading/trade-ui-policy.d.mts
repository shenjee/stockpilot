export function resolveTradeUiPolicy(args: {
  session?: {
    symbol?: string;
    trade_date?: string;
    session_type?: string;
  } | null;
  workbenchSecurity?: {
    symbol: string;
    instrument_type: string;
  } | null;
  mode: "live" | "replay";
  today: string;
}): {
  visibleSymbol: string | null;
  visibleTradeDate: string;
  sessionType: string | null;
  visibleSecurity: { symbol: string; instrument_type: string } | null;
  isTradableSecurity: boolean;
  isKnownNonTradableVisible: boolean;
  historicalChartVisible: boolean;
  tradeDrawerReadOnly: boolean;
  shouldMountTradeDrawer: boolean;
  shouldListTrades: boolean;
};

export function replayCursorChangeRequiresRelist(
  previous: { visibleSymbol: string | null; visibleTradeDate: string },
  next: { visibleSymbol: string | null; visibleTradeDate: string },
): boolean;
