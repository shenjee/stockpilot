export function standardSecurityFromResponse(response) {
  const security = response?.security ?? response?.data?.security;
  return security &&
    /^(sh|sz)\.[0-9]{6}$/.test(security.symbol) &&
    /^[0-9]{6}$/.test(security.code) &&
    typeof security.name === "string" &&
    security.name.length > 0
    ? security
    : null;
}

export function securitiesFromSearchResponse(response) {
  const securities = response?.data?.securities;
  return Array.isArray(securities)
    ? securities.filter((security) => standardSecurity(security))
    : [];
}

export function isCompleteWorkbenchSnapshot(candidate) {
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
    ["最新价", formatNumber(quote?.latest_price)],
    ["涨跌幅", formatPercent(quote?.change_percent)],
    ["今日开盘", formatNumber(quote?.open)],
    ["最高", formatNumber(quote?.high)],
    ["最低", formatNumber(quote?.low)],
    ["昨收", formatNumber(quote?.previous_close)],
    ["成交量", formatCompact(quote?.volume)],
    ["成交额", formatCurrency(quote?.amount)],
    ["量比", formatNumber(quote?.volume_ratio)],
    ["实时换手率", formatPercent(quote?.turnover_rate)],
    ["委比", formatPercent(quote?.order_imbalance)],
    ["行情时间", formatTimestamp(quote?.timestamp)],
  ];
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
      security.name.length > 0,
  );
}

function formatNumber(value) {
  return finite(value)
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: 3 })
    : "--";
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
  const formatted = formatCompact(value);
  return formatted === "--" ? formatted : `¥${formatted}`;
}

function formatTimestamp(value) {
  return typeof value === "string" && value.length >= 19
    ? value.slice(5, 19)
    : "--";
}
