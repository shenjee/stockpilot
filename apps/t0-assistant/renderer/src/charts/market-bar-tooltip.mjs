/** 固定角落行情 Tooltip：格式化、精确 bar 映射与左右角落策略（issue #144）。 */

export const MARKET_BAR_TOOLTIP_MARGIN_PX = 14;
export const MARKET_BAR_TOOLTIP_CLASS = "market-bar-tooltip";

/**
 * @param {string | null | undefined} timestamp
 * @returns {string}
 */
export function formatMarketBarTooltipDate(timestamp) {
  if (typeof timestamp !== "string" || timestamp.length < 10) {
    return "";
  }
  return timestamp.slice(0, 10);
}

/**
 * @param {string | null | undefined} timestamp
 * @returns {string}
 */
export function formatMarketBarTooltipTime(timestamp) {
  if (typeof timestamp !== "string") {
    return "";
  }
  const match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})/.exec(timestamp);
  if (!match) {
    return "";
  }
  return `${match[2]}:${match[3]}`;
}

/**
 * @param {unknown} value
 * @returns {string}
 */
export function formatMarketBarTooltipPrice(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  return value.toFixed(2);
}

/**
 * 成交量千分位分隔，不使用科学计数法 / 万亿缩写。
 * @param {unknown} value
 * @param {string} [locale]
 * @returns {string}
 */
export function formatMarketBarTooltipVolume(value, locale = "en-US") {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return "";
  }
  const rounded = Math.round(value);
  return rounded.toLocaleString(locale, {
    useGrouping: true,
    maximumFractionDigits: 0,
  });
}

/**
 * @param {unknown} open
 * @param {unknown} close
 * @returns {{ arrow: "▲" | "▼"; up: boolean } | null}
 */
export function resolveMarketBarDirection(open, close) {
  if (
    typeof open !== "number" ||
    typeof close !== "number" ||
    !Number.isFinite(open) ||
    !Number.isFinite(close)
  ) {
    return null;
  }
  const up = close >= open;
  return { arrow: up ? "▲" : "▼", up };
}

/**
 * 仅精确匹配 timestamp，不回退邻近 bar。
 * @param {ReadonlyArray<{ timestamp?: string }> | null | undefined} bars
 * @param {string | null | undefined} timestamp
 * @returns {object | null}
 */
export function findMarketBarByTimestamp(bars, timestamp) {
  if (!Array.isArray(bars) || typeof timestamp !== "string") {
    return null;
  }
  for (const bar of bars) {
    if (bar && bar.timestamp === timestamp) {
      return bar;
    }
  }
  return null;
}

/**
 * 将 LC crosshair 的 UTC 秒时间映射回契约 timestamp，再精确取 bar。
 * @param {ReadonlyArray<{ timestamp?: string }> | null | undefined} bars
 * @param {Record<string, number> | null | undefined} timeByTimestamp
 * @param {unknown} utcSeconds
 * @returns {object | null}
 */
export function findMarketBarByUtcSeconds(bars, timeByTimestamp, utcSeconds) {
  if (
    !Array.isArray(bars) ||
    !timeByTimestamp ||
    typeof utcSeconds !== "number" ||
    !Number.isFinite(utcSeconds)
  ) {
    return null;
  }
  let matchedTimestamp = null;
  for (const [timestamp, time] of Object.entries(timeByTimestamp)) {
    if (time === utcSeconds) {
      matchedTimestamp = timestamp;
      break;
    }
  }
  if (matchedTimestamp === null) {
    return null;
  }
  return findMarketBarByTimestamp(bars, matchedTimestamp);
}

/**
 * 激活 K 线在左半区 → 右上角；右半区 → 左上角。中点归左半区（显示右上）。
 * 坐标越出 [0, plotWidth] 时返回 null（可见范围外 / 布局漂移）。
 * @param {{ barCoordinate: number; plotWidth: number }} input
 * @returns {"left" | "right" | null}
 */
export function resolveMarketBarTooltipCorner({ barCoordinate, plotWidth }) {
  if (
    typeof barCoordinate !== "number" ||
    typeof plotWidth !== "number" ||
    !Number.isFinite(barCoordinate) ||
    !Number.isFinite(plotWidth) ||
    plotWidth <= 0 ||
    barCoordinate < 0 ||
    barCoordinate > plotWidth
  ) {
    return null;
  }
  const midpoint = plotWidth / 2;
  return barCoordinate > midpoint ? "left" : "right";
}

/**
 * @param {{
 *   pointerOverPricePlot: boolean;
 *   isDragging: boolean;
 *   bar: object | null | undefined;
 * }} input
 * @returns {boolean}
 */
export function shouldShowMarketBarTooltip({
  pointerOverPricePlot,
  isDragging,
  bar,
}) {
  return Boolean(pointerOverPricePlot && !isDragging && bar);
}

/**
 * @param {object | null | undefined} bar
 * @returns {{
 *   date: string;
 *   time: string;
 *   open: string;
 *   high: string;
 *   low: string;
 *   close: string;
 *   volume: string;
 *   direction: { arrow: "▲" | "▼"; up: boolean } | null;
 * } | null}
 */
export function buildMarketBarTooltipViewModel(bar) {
  if (!bar || typeof bar.timestamp !== "string") {
    return null;
  }
  const date = formatMarketBarTooltipDate(bar.timestamp);
  const time = formatMarketBarTooltipTime(bar.timestamp);
  const open = formatMarketBarTooltipPrice(bar.open);
  const high = formatMarketBarTooltipPrice(bar.high);
  const low = formatMarketBarTooltipPrice(bar.low);
  const close = formatMarketBarTooltipPrice(bar.close);
  const volume = formatMarketBarTooltipVolume(bar.volume);
  if (!date || !time || !open || !high || !low || !close || volume === "") {
    return null;
  }
  return {
    date,
    time,
    open,
    high,
    low,
    close,
    volume,
    direction: resolveMarketBarDirection(bar.open, bar.close),
  };
}

/**
 * 用安全 DOM API 渲染内容（不用 innerHTML 拼接行情字符串）。
 * @param {HTMLElement} root
 * @param {ReturnType<typeof buildMarketBarTooltipViewModel>} viewModel
 */
export function renderMarketBarTooltipContent(root, viewModel) {
  while (root.firstChild) {
    root.removeChild(root.firstChild);
  }
  if (!viewModel) {
    return;
  }

  const dateEl = document.createElement("div");
  dateEl.className = `${MARKET_BAR_TOOLTIP_CLASS}__date`;
  dateEl.textContent = viewModel.date;
  root.appendChild(dateEl);

  const timeEl = document.createElement("div");
  timeEl.className = `${MARKET_BAR_TOOLTIP_CLASS}__time`;
  timeEl.textContent = viewModel.time;
  root.appendChild(timeEl);

  appendRow(root, "开", viewModel.open);
  appendRow(root, "高", viewModel.high);
  appendRow(root, "低", viewModel.low);

  const closeRow = document.createElement("div");
  closeRow.className = `${MARKET_BAR_TOOLTIP_CLASS}__row`;
  const closeKey = document.createElement("span");
  closeKey.className = `${MARKET_BAR_TOOLTIP_CLASS}__key`;
  closeKey.textContent = "收";
  const closeVal = document.createElement("span");
  closeVal.className = `${MARKET_BAR_TOOLTIP_CLASS}__val`;
  const closePrice = document.createElement("span");
  closePrice.textContent = viewModel.close;
  closeVal.appendChild(closePrice);
  if (viewModel.direction) {
    const arrow = document.createElement("span");
    arrow.className = `${MARKET_BAR_TOOLTIP_CLASS}__arrow ${
      viewModel.direction.up
        ? `${MARKET_BAR_TOOLTIP_CLASS}__arrow--up`
        : `${MARKET_BAR_TOOLTIP_CLASS}__arrow--down`
    }`;
    arrow.textContent = ` ${viewModel.direction.arrow}`;
    closeVal.appendChild(arrow);
  }
  closeRow.appendChild(closeKey);
  closeRow.appendChild(closeVal);
  root.appendChild(closeRow);

  appendRow(root, "成交量", viewModel.volume);
}

/**
 * @param {HTMLElement} root
 * @param {string} label
 * @param {string} value
 * @returns {HTMLElement}
 */
function appendRow(root, label, value) {
  const row = document.createElement("div");
  row.className = `${MARKET_BAR_TOOLTIP_CLASS}__row`;
  const key = document.createElement("span");
  key.className = `${MARKET_BAR_TOOLTIP_CLASS}__key`;
  key.textContent = label;
  const val = document.createElement("span");
  val.className = `${MARKET_BAR_TOOLTIP_CLASS}__val`;
  val.textContent = value;
  row.appendChild(key);
  row.appendChild(val);
  root.appendChild(row);
  return row;
}

/**
 * 指针是否落在价格图实际绘图区（排除右侧 Y 轴）。
 * @param {{
 *   clientX: number;
 *   clientY: number;
 *   containerRect: { left: number; top: number; width: number; height: number };
 *   plotWidth: number;
 *   plotHeight: number;
 * }} input
 * @returns {boolean}
 */
export function isPointerInPricePlotArea({
  clientX,
  clientY,
  containerRect,
  plotWidth,
  plotHeight,
}) {
  if (
    !containerRect ||
    typeof plotWidth !== "number" ||
    typeof plotHeight !== "number" ||
    plotWidth <= 0 ||
    plotHeight <= 0
  ) {
    return false;
  }
  const x = clientX - containerRect.left;
  const y = clientY - containerRect.top;
  return x >= 0 && x <= plotWidth && y >= 0 && y <= plotHeight;
}
