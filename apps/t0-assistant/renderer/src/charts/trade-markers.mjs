/**
 * Pure projection layer: trade_record[] -> TradeMarkerModel[]
 *
 * Trade records are rendered as markers on the 5m price chart only, via an
 * independent SynchronizedChartGroup overlay (Issue #163) — not via
 * ChartGroupModel. Markers are sorted into a stable order so that multiple
 * trades in the same 5m bucket are rendered predictably.
 */

/** @typedef {"real" | "simulated"} TradeScope */
/** @typedef {"buy" | "sell"} TradeSide */

/**
 * Frozen trade_record shape from app-v2.schema.json. This file is the single
 * source of truth for the renderer-side trade type.
 *
 * @typedef {Object} TradeRecord
 * @property {string} trade_id
 * @property {string} bucket_start - "YYYY-MM-DD HH:MM:SS"
 * @property {TradeScope} trade_scope
 * @property {string} symbol
 * @property {TradeSide} side
 * @property {string} executed_at - "YYYY-MM-DD HH:MM:SS"
 * @property {number} price
 * @property {number} quantity
 * @property {number | null} fee
 * @property {string} note
 * @property {string | null} fee_plan_id
 */

/**
 * @typedef {Object} TradeMarkerModel
 * @property {string} trade_id
 * @property {TradeScope} trade_scope
 * @property {number} time - chart time (Unix timestamp of the 5m bucket)
 * @property {number} price - actual trade price used as y-coordinate
 * @property {TradeSide} side
 * @property {number} quantity - shares
 * @property {string} label - e.g. "B2" / "S2" / "B0.5"
 * @property {string} color
 * @property {"circle" | "square"} shape
 */

/** @type {Record<TradeSide, {color: string, shape: "circle" | "square"}>} */
const SIDE_STYLE = {
  buy: { color: "#22d3ee", shape: "circle" },
  sell: { color: "#f0abfc", shape: "square" },
};

const SHARES_PER_LOT = 100;

const MARKET_TIMESTAMP_RE =
  /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/;

const THIRTY_MINUTE_CLOSES = Object.freeze([
  "10:00:00",
  "10:30:00",
  "11:00:00",
  "11:30:00",
  "13:30:00",
  "14:00:00",
  "14:30:00",
  "15:00:00",
]);

/**
 * Parse a market timestamp string into a Unix timestamp in seconds.
 *
 * @param {string} timestamp
 * @returns {number}
 */
export function parseMarketTimestampSeconds(timestamp) {
  const match = MARKET_TIMESTAMP_RE.exec(timestamp);
  if (!match) {
    throw new TypeError(`Unsupported market timestamp: ${timestamp}`);
  }
  const [, year, month, day, hour, minute, second] = match.map(Number);
  return Date.UTC(year, month - 1, day, hour, minute, second) / 1000;
}

/**
 * Map an execution time onto the 30-minute bar close that contains it.
 * Lunch (after 11:30 and before 13:00) and pre-open times yield null.
 *
 * @param {string} executedAt
 * @returns {string | null}
 */
export function thirtyMinuteCloseTimestamp(executedAt) {
  if (typeof executedAt !== "string" || !MARKET_TIMESTAMP_RE.test(executedAt)) {
    return null;
  }
  const date = executedAt.slice(0, 10);
  let executed;
  try {
    executed = parseMarketTimestampSeconds(executedAt);
  } catch {
    return null;
  }
  const open = parseMarketTimestampSeconds(`${date} 09:30:00`);
  const lunchStart = parseMarketTimestampSeconds(`${date} 11:30:00`);
  const afternoonStart = parseMarketTimestampSeconds(`${date} 13:00:00`);
  if (executed < open) {
    return null;
  }
  if (executed > lunchStart && executed < afternoonStart) {
    return null;
  }
  for (const close of THIRTY_MINUTE_CLOSES) {
    const closeTime = parseMarketTimestampSeconds(`${date} ${close}`);
    if (executed <= closeTime) {
      return `${date} ${close}`;
    }
  }
  return null;
}

/**
 * Format a share quantity as a lot label. A-share/ETF lots are normalised to
 * 100 shares per lot. Fractional lots are preserved so that 1-99 shares are
 * still displayed.
 *
 * @param {number} quantity
 * @returns {string}
 */
export function formatLotLabel(quantity) {
  const lots = quantity / SHARES_PER_LOT;
  if (Number.isInteger(lots)) {
    return String(lots);
  }
  // Remove trailing zeros while keeping meaningful precision.
  return String(parseFloat(lots.toFixed(2)));
}

/**
 * Convert a raw trade record into the chart marker model.
 *
 * Invalid records are dropped rather than rendered at incorrect coordinates.
 *
 * @param {TradeRecord} trade
 * @returns {TradeMarkerModel | null}
 */
export function projectTradeMarker(trade) {
  if (!trade || typeof trade !== "object") {
    return null;
  }

  const side = trade.side;
  if (side !== "buy" && side !== "sell") {
    return null;
  }

  const trade_scope = trade.trade_scope;
  if (trade_scope !== "real" && trade_scope !== "simulated") {
    return null;
  }

  let time;
  try {
    time = parseMarketTimestampSeconds(trade.bucket_start);
  } catch {
    return null;
  }

  const price = Number(trade.price);
  const quantity = Number(trade.quantity);

  if (!Number.isFinite(time) || !Number.isFinite(price) || !Number.isFinite(quantity)) {
    return null;
  }
  if (time <= 0 || price <= 0 || quantity <= 0) {
    return null;
  }

  const style = SIDE_STYLE[side];
  const label = `${side === "buy" ? "B" : "S"}${formatLotLabel(quantity)}`;

  return {
    trade_id: String(trade.trade_id),
    trade_scope,
    time,
    price,
    side,
    quantity,
    label,
    color: style.color,
    shape: style.shape,
  };
}

/**
 * Project trade records to marker models.
 *
 * @param {TradeRecord[]} trades
 * @param {Object} [options]
 * @param {Set<number> | number[]} [options.allowedTimes] - if provided, only keep markers whose chart time matches an existing K-line time.
 * @param {(trade: TradeRecord) => string | null} [options.resolveBucketStart] - override bucket_start, used to place the same trades onto 30m bars via executed_at.
 * @returns {TradeMarkerModel[]}
 */
export function projectTradeMarkers(trades, { allowedTimes, resolveBucketStart } = {}) {
  if (!Array.isArray(trades)) {
    return [];
  }

  const allowed = allowedTimes ? new Set(allowedTimes) : null;

  const markers = trades
    .map((trade) => {
      if (!resolveBucketStart) {
        return projectTradeMarker(trade);
      }
      const bucketStart = resolveBucketStart(trade);
      if (!bucketStart) {
        return null;
      }
      return projectTradeMarker({ ...trade, bucket_start: bucketStart });
    })
    .filter(
      /** @type {(m: TradeMarkerModel | null) => m is TradeMarkerModel} */
        ((m) => m !== null),
    )
    .filter((m) => (allowed ? allowed.has(m.time) : true));

  return sortTradeMarkers(markers);
}

/**
 * Return a stable ordering for trade markers.
 *
 * Order: bucket time ascending, then buy before sell, then price ascending,
 * then trade_id ascending. This makes multiple trades in the same 5m bucket
 * predictable and testable.
 *
 * @param {TradeMarkerModel[]} markers
 * @returns {TradeMarkerModel[]}
 */
export function sortTradeMarkers(markers) {
  return [...markers].sort((a, b) => {
    if (a.time !== b.time) return a.time - b.time;
    if (a.side !== b.side) return a.side === "buy" ? -1 : 1;
    if (a.price !== b.price) return a.price - b.price;
    return a.trade_id.localeCompare(b.trade_id, "en");
  });
}
