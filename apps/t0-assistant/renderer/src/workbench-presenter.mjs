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

/**
 * Map a standard security identity to a market classification label.
 *
 * Uses the authoritative `market` and `security_type` fields rather than
 * code-prefix inference, per issue #131:
 *   - security_type = etf           -> 基金 (covers SH/SZ listed ETFs only)
 *   - a_share + market = sh         -> 沪市
 *   - a_share + market = sz         -> 深市
 */
export function securityCategoryLabel(security) {
  if (!security) return "";
  if (security.security_type === "etf") return "基金";
  return security.market === "sh" ? "沪市" : "深市";
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

const MARKET_PHASE_LABELS = {
  unknown: "市场阶段未知",
  pre_open: "盘前",
  morning: "上午交易",
  lunch_break: "午间休市",
  afternoon: "下午交易",
  closed: "已收盘",
  market_closed: "休市",
};

const DATA_QUALITY_LABELS = {
  full: "数据完整",
  degraded: "无完整 1 分钟",
  partial: "数据部分",
};

const POLLING_PROFILE_LABELS = {
  active: "轮询中",
  reduced: "低频轮询",
  idle: "暂停轮询",
};

const SYMBOL_AVAILABILITY_LABELS = {
  available: "当日行情可用",
  no_current_data: "暂无当日行情",
  suspended: "停牌",
};

export function liveMarketViewLines(view, { replayMode = false } = {}) {
  if (!view || typeof view !== "object" || replayMode) {
    return [];
  }
  const lines = [];
  const tradeDate = view.effective_trade_date;
  if (typeof tradeDate === "string" && tradeDate.length >= 10) {
    lines.push(["展示交易日", tradeDate]);
  }
  const phase = MARKET_PHASE_LABELS[view.market_phase];
  if (phase) {
    lines.push(["市场阶段", phase]);
  }
  if (view.calendar_status === "unavailable") {
    lines.push(["交易日历", "覆盖不足"]);
  }
  const availability = SYMBOL_AVAILABILITY_LABELS[view.symbol_availability];
  if (availability && view.symbol_availability !== "available") {
    lines.push(["证券状态", availability]);
  }
  const quality = DATA_QUALITY_LABELS[view.data_quality];
  if (quality && view.data_quality !== "full") {
    lines.push(["缓存质量", quality]);
  }
  const polling = POLLING_PROFILE_LABELS[view.polling_profile];
  if (polling) {
    lines.push(["刷新状态", polling]);
  }
  const snapshotAsOf = latestBranchAsOf(view);
  if (snapshotAsOf !== "--") {
    lines.push(["快照截止", snapshotAsOf]);
  }
  return lines;
}

function latestBranchAsOf(view) {
  const candidates = [
    view.quote_as_of,
    view.bars_1m_as_of,
    view.bars_5m_as_of,
    view.daily_as_of,
  ].filter((value) => typeof value === "string" && value.length >= 19);
  if (candidates.length === 0) {
    return "--";
  }
  candidates.sort();
  return formatBranchAsOf(candidates[candidates.length - 1]);
}

function formatBranchAsOf(value) {
  return typeof value === "string" && value.length >= 19
    ? value.slice(5, 19)
    : "--";
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
