/**
 * Pure projection layer: trade_record[] -> TradeMarkerModel[]
 *
 * Trade records are rendered as markers on the 5m price chart only. The
 * projection keeps real and simulated trades in the same pipeline; only the
 * trade_scope field differs. Markers are sorted into a stable order so that
 * multiple trades in the same 5m bucket are rendered predictably.
 */

/** @typedef {"real" | "simulated"} TradeScope */
/** @typedef {"buy" | "sell"} TradeSide */

/**
 * Frozen trade_record shape from app-v1.schema.json. This file is the single
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
 * @param {Set<number> | number[]} [options.allowedTimes] - if provided, only keep markers whose chart time matches an existing 5m K-line time. Markers with no matching K-line are discarded so they cannot create fake candles.
 * @returns {TradeMarkerModel[]}
 */
export function projectTradeMarkers(trades, { allowedTimes } = {}) {
  if (!Array.isArray(trades)) {
    return [];
  }

  const allowed = allowedTimes ? new Set(allowedTimes) : null;

  const markers = trades
    .map(projectTradeMarker)
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
