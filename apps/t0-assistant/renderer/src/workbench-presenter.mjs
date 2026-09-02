export function standardSecurityFromResponse(response) {
  const security = response?.security ?? response?.data?.security;
  return security &&
    /^(sh|sz)\.[0-9]{6}$/.test(security.symbol) &&
    /^[0-9]{6}$/.test(security.code) &&
    typeof security.name === "string" &&
    security.name.length > 0 &&
    (security.instrument_type === "stock" ||
      security.instrument_type === "etf" ||
      security.instrument_type === "index")
    ? security
    : null;
}

export function restoredSecurityFromResponse(response) {
  const data = response?.data ?? response;
  const security = data?.restored_security;
  return standardSecurity(security)
    ? security
    : null;
}

export function startupRestoreFromResponse(response) {
  const data = response?.data ?? response;
  const startup = data?.startup_restore;
  return startup && typeof startup === "object" ? startup : null;
}

export function clearLiveScopedBackgroundError(error) {
  return error?.affected_capability === "live" ? null : error;
}

export function startupRestoreOperationId(sessionId) {
  return `live-load-${sessionId}`;
}

export function cancelStartupRestoreTracking(restoreInFlight, activeOperations) {
  if (!restoreInFlight?.sessionId) return;
  activeOperations.delete(startupRestoreOperationId(restoreInFlight.sessionId));
}

export function securitiesFromSearchResponse(response) {
  const securities = response?.data?.securities;
  return Array.isArray(securities)
    ? securities.filter((security) => standardSecurity(security))
    : [];
}

/**
 * Map a standard security identity to a market classification label.
 *
 * Uses the authoritative `market` and `instrument_type` fields rather than
 * code-prefix inference, per issue #151:
 *   - instrument_type = etf          -> 基金 (covers SH/SZ listed ETFs only)
 *   - instrument_type = index        -> 指数
 *   - stock + market = sh           -> 沪市
 *   - stock + market = sz           -> 深市
 */
export function securityCategoryLabel(security) {
  if (!security) return "";
  if (security.instrument_type === "etf") return "基金";
  if (security.instrument_type === "index") return "指数";
  return security.market === "sh" ? "沪市" : "深市";
}

/**
 * Initial state for the security search box interaction reducer.
 */
export const initialSecuritySearchState = Object.freeze({
  activeIndex: -1,
  dismissed: false,
});

/**
 * Pure reducer for security search box keyboard/mouse interaction.
 *
 * The "select" action always closes the dropdown immediately (dismissed =
 * true, activeIndex = -1) so that slow or failed async callbacks in the
 * parent component do not leave the results list visible.  The component
 * dispatches "select" *before* calling the parent's onSelect handler.
 */
export function securitySearchReducer(state, action) {
  switch (action.type) {
    case "arrow-down":
      if (action.count === 0) return state;
      return {
        dismissed: false,
        activeIndex: nextActiveIndexDown(state.activeIndex, action.count),
      };
    case "arrow-up":
      if (action.count === 0) return state;
      return {
        dismissed: false,
        activeIndex: nextActiveIndexUp(state.activeIndex, action.count),
      };
    case "escape":
      if (!action.visible) return state;
      return { activeIndex: -1, dismissed: true };
    case "mouse-enter":
      return { ...state, activeIndex: action.index };
    case "query-change":
      return { activeIndex: -1, dismissed: false };
    case "reset-cursor":
      return { ...state, activeIndex: -1 };
    case "select":
      return { activeIndex: -1, dismissed: true };
    default:
      return state;
  }
}

function nextActiveIndexDown(current, count) {
  if (current < 0) return 0;
  return current >= count - 1 ? 0 : current + 1;
}

function nextActiveIndexUp(current, count) {
  if (current < 0) return count - 1;
  return current <= 0 ? count - 1 : current - 1;
}

/**
 * Return the suggestion index that Enter would select, or null if there
 * are no suggestions to select.
 */
export function securitySearchEnterTarget(state, count) {
  if (count === 0) return null;
  return state.activeIndex >= 0 ? state.activeIndex : 0;
}

/**
 * Shallow envelope-shape check for Renderer ingress.
 *
 * This is NOT a full workbench_snapshot contract validator: it only confirms
 * the fields the Renderer needs to accept a payload into projection plumbing.
 * Deep bar/indicator/timeline invariants remain owned by the Python-side JSON
 * Schema, and chart renderability is probed separately via
 * ``inspectWorkbenchSnapshotCandidate`` / ``createChartGroupModel``.
 */
export function hasWorkbenchSnapshotEnvelope(candidate) {
  if (!candidate || typeof candidate !== "object") return false;
  const session = candidate.session;
  const market = candidate.market;
  const indicators = candidate.indicators;
  return (
    candidate.timezone === "Asia/Shanghai" &&
    session !== null &&
    typeof session === "object" &&
    typeof session.session_id === "string" &&
    session.session_id.length > 0 &&
    typeof session.symbol === "string" &&
    Number.isInteger(session.revision) &&
    market !== null &&
    typeof market === "object" &&
    Array.isArray(market.bars_1m) &&
    Array.isArray(market.bars_5m) &&
    Array.isArray(market.daily_bars) &&
    (market.quote === null ||
      (typeof market.quote === "object" && market.quote !== null)) &&
    indicators !== null &&
    typeof indicators === "object" &&
    typeof indicators.five_minute === "object" &&
    indicators.five_minute !== null &&
    typeof indicators.one_minute === "object" &&
    indicators.one_minute !== null &&
    typeof candidate.chan_analysis === "object" &&
    candidate.chan_analysis !== null &&
    Array.isArray(candidate.warnings)
  );
}

export function operationMatchesEnvelope(operation, envelope) {
  return Boolean(
    operation &&
      envelope &&
      operation.serviceGeneration === envelope.service_generation &&
      (operation.sessionId === null ||
        operation.sessionId === envelope.session_id),
  );
}

export function createLatestRequestTracker() {
  let sequence = 0;
  return Object.freeze({
    begin() {
      sequence += 1;
      return sequence;
    },
    isCurrent(candidate) {
      return candidate === sequence;
    },
  });
}

export function canHydratePreferences(status, hydrated) {
  return status?.state === "connected" && hydrated === false;
}

export function liveOperationFailurePresentation(mode, error) {
  if (error?.affected_capability === "market_calendar") {
    return { blocking: false, error };
  }
  if (mode !== "replay") {
    return { blocking: true, error };
  }
  return {
    blocking: false,
    error: {
      ...error,
      message: `后台 ${error.message}；当前回放不受影响`,
    },
  };
}

export function applicationErrorFrom(candidate) {
  const error = candidate?.error ?? candidate?.payload ?? candidate;
  return error &&
    typeof error.error_code === "string" &&
    typeof error.message === "string" &&
    typeof error.retryable === "boolean"
    ? error
    : null;
}

export function quoteRows(quote) {
  return [
    ["今开", formatPrice(quote?.open)],
    ["最高", formatPrice(quote?.high)],
    ["最低", formatPrice(quote?.low)],
    ["昨收", formatPrice(quote?.previous_close)],
    ["成交量", formatCompact(quote?.volume)],
    ["成交额", formatCurrency(quote?.amount)],
    ["量比", formatPrice(quote?.volume_ratio)],
    ["实时换手率", formatPercent(quote?.turnover_rate)],
    ["委比", formatPercent(quote?.order_imbalance)],
  ];
}

export function quoteSummary(quote) {
  const latestPrice = finite(quote?.latest_price);
  const previousClose = finite(quote?.previous_close);
  const changePercent = finite(quote?.change_percent);
  const changePercentValue = quote?.change_percent;
  const difference = latestPrice && previousClose
    ? quote.latest_price - quote.previous_close
    : null;
  const direction = difference === null || difference === 0
    ? "flat"
    : difference > 0
      ? "rise"
      : "fall";

  return {
    price: latestPrice ? formatPrice(quote.latest_price) : "--",
    difference: difference === null ? "--" : formatSignedNumber(difference),
    changePercent: changePercent
      ? formatPercent(quote.change_percent)
      : changePercentValue === 0
        ? "0.00%"
        : "--",
    direction,
  };
}

/** Quote-panel as-of line; not a quote field row. */
export function quoteDataCutoffText(quote) {
  return `数据截止  ${formatTimestamp(quote?.timestamp)}`;
}

export function latestDailyBars(snapshot, limit = 60) {
  const bars = snapshot?.market?.daily_bars;
  return Array.isArray(bars) ? bars.slice(-limit) : [];
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function standardSecurity(security) {
  return Boolean(
    security &&
      /^(sh|sz)\.[0-9]{6}$/.test(security.symbol) &&
      /^[0-9]{6}$/.test(security.code) &&
      typeof security.name === "string" &&
      security.name.length > 0 &&
      (security.instrument_type === "stock" ||
        security.instrument_type === "etf" ||
        security.instrument_type === "index"),
  );
}

function formatPrice(value) {
  return finite(value) ? value.toFixed(2) : "--";
}

function formatSignedNumber(value) {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

function formatPercent(value) {
  if (!finite(value)) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatCompact(value) {
  if (!finite(value)) return "--";
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(2)} 万`;
  return value.toLocaleString("zh-CN");
}

function formatCurrency(value) {
  return formatCompact(value);
}

function formatTimestamp(value) {
  return typeof value === "string" && value.length >= 19
    ? value.slice(5, 19)
    : "--";
}
