const FIVE_MINUTE = "five_minute";
const ONE_MINUTE = "one_minute";

export const ChartGroupKind = Object.freeze({
  FIVE_MINUTE,
  ONE_MINUTE,
});

export function parseMarketTimestamp(timestamp) {
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/.exec(
    timestamp,
  );
  if (!match) {
    throw new TypeError(`Unsupported market timestamp: ${timestamp}`);
  }
  const [, year, month, day, hour, minute, second] = match.map(Number);
  return Date.UTC(year, month - 1, day, hour, minute, second) / 1000;
}

/**
 * Multiply `value` by 10**power via exponential mantissa/exponent adjustment.
 * Avoids both binary `value * 10**n` drift and illegal strings like `1e-7e2`.
 */
function scaleByPowerOf10(value, power) {
  if (value === 0) {
    return 0;
  }
  if (!Number.isFinite(value)) {
    return value;
  }
  const exponential = value.toExponential();
  const eIndex = exponential.indexOf("e");
  const mantissa = exponential.slice(0, eIndex);
  const exponent = Number(exponential.slice(eIndex + 1)) + power;
  return Number(`${mantissa}e${exponent}`);
}

/**
 * Half-away-from-zero decimal rounding that does not rely on `value * 10**n`
 * binary products (those fail cases like 1.005 → 1.00) and remains valid for
 * values whose default string form is scientific notation (e.g. 1e-7).
 */
export function roundHalfAwayFromZero(value, decimalPlaces) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return Number.NaN;
  }
  if (!Number.isInteger(decimalPlaces) || decimalPlaces < 0) {
    return Number.NaN;
  }
  const sign = value < 0 ? -1 : 1;
  const absolute = Math.abs(value);
  const shifted = scaleByPowerOf10(absolute, decimalPlaces);
  if (!Number.isFinite(shifted)) {
    return Number.NaN;
  }
  const rounded = Math.round(shifted);
  const result = sign * scaleByPowerOf10(rounded, -decimalPlaces);
  // Avoid signed zero from `sign * 0` so labels/tests see a plain 0.
  return result === 0 ? 0 : result;
}

export function formatPriceAxisTickLabel(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  const rounded = roundHalfAwayFromZero(value, 2);
  if (Math.abs(rounded) < 100) {
    return rounded.toFixed(2);
  }
  return String(roundHalfAwayFromZero(rounded, 0));
}

export function formatPriceExactLabel(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  return roundHalfAwayFromZero(value, 2).toFixed(2);
}

export function formatPriceAxisTickLabels(prices) {
  if (!Array.isArray(prices)) {
    return [];
  }
  return prices.map((price) => formatPriceAxisTickLabel(price));
}

/** Integer tick generation threshold used with LC `minMove` (see ui_layout_spec §4.3). */
export const PRICE_AXIS_INTEGER_TICK_ABS = 100;
export const PRICE_AXIS_FINE_MIN_MOVE = 0.01;
export const PRICE_AXIS_INTEGER_MIN_MOVE = 1;

/**
 * LC uses series `minMove` as the tick-generation base. When the visible scale
 * can contain marks with abs >= 100, force minMove=1 so marks are integers and
 * labels stay aligned with grid lines after integer formatting.
 */
export function resolvePriceAxisMinMove(rangeMin, rangeMax) {
  const candidates = [rangeMin, rangeMax].filter(
    (value) => typeof value === "number" && Number.isFinite(value),
  );
  if (candidates.length === 0) {
    return PRICE_AXIS_FINE_MIN_MOVE;
  }
  const maxAbs = Math.max(...candidates.map((value) => Math.abs(value)));
  return maxAbs >= PRICE_AXIS_INTEGER_TICK_ABS
    ? PRICE_AXIS_INTEGER_MIN_MOVE
    : PRICE_AXIS_FINE_MIN_MOVE;
}

/**
 * Compute a valid LC tick-generation `base` from an arbitrary `minMove`.
 *
 * TODO: 这是 lightweight-charts 的设计缺陷，以后换图表引擎时可以删掉。
 *
 * lightweight-charts 的 PriceTickSpanCalculator 生成 Y 轴刻度的算法只允许
 * 基数的质因子是 2 或 5（或基数本身是 10 的幂）。原因是：我们用十进制，
 * 10 = 2 × 5，只有 2 和 5 作为因子的数才能被 10 整除，刻度才会落在
 * "整"的位置上（0, 1, 2, 5, 10, 20, 50, 100 ...）。
 * 一旦出现其他质因子（比如 3），刻度就永远落不到整齐的小数位上。
 * 但 lightweight-charts 的处理方式不是优雅降级，而是直接抛
 * `Error: unexpected base`，把整个图表搞崩。
 *
 * 典型案例：300133（华宇电子，P0=7.61）算出 tickStep≈0.08，
 * LC 内部 base = Math.round(1/0.08) = Math.round(12.5) = 13（质数），
 * 因为含非 2/5 因子，直接崩。而 600584 算出 tickStep=1.725，
 * base = Math.round(1/1.725) = 1，恰好合法所以不崩。
 *
 * 修复方式：我们自己算 base，如果只含 2 和 5 就用原值，
 * 否则取最近的 10 的幂。这只影响内部刻度间距，
 * 实际显示的价格标签由 formatter / tickmarksFormatter 控制。
 *
 * @param {number} minMove
 * @returns {number} a positive integer factorable into 2s and 5s
 */
export function computeValidPriceBase(minMove) {
  if (!Number.isFinite(minMove) || minMove <= 0) return 100;
  let base = Math.round(1 / minMove);
  if (base < 1) base = 1;
  let rest = base;
  while (rest > 1) {
    if (rest % 2 === 0) {
      rest = Math.floor(rest / 2);
    } else if (rest % 5 === 0) {
      rest = Math.floor(rest / 5);
    } else {
      // Not factorable – snap to nearest power of 10.
      return 10 ** Math.max(0, Math.round(Math.log10(base)));
    }
  }
  return base;
}

export function createPriceExactPriceFormat(minMove = PRICE_AXIS_FINE_MIN_MOVE) {
  return Object.freeze({
    type: "custom",
    formatter: formatPriceExactLabel,
    tickmarksFormatter: formatPriceAxisTickLabels,
    minMove,
    base: computeValidPriceBase(minMove),
  });
}

// Default dual format for series construction; minMove is refined after data/layout.
export const PRICE_EXACT_PRICE_FORMAT = createPriceExactPriceFormat(
  PRICE_AXIS_FINE_MIN_MOVE,
);

export function formatVolumeAxisLabels(prices) {
  if (!Array.isArray(prices)) {
    return [];
  }
  return prices.map((price) => formatVolumeAxisLabel(price));
}

export function formatVolumeAxisLabel(value, locale = "zh-CN") {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  if (value < 0) {
    return "";
  }
  if (value === 0) {
    return "0";
  }
  // LC 5.x may call formatter via prices.map(formatter), which passes the array
  // index as the second argument; only treat real locale strings as locales.
  if (typeof locale !== "string") {
    locale = "zh-CN";
  }
  // chart-level priceFormatter 只接收 price；locale 默认 zh-CN，国际化需包闭包注入。
  if (locale.startsWith("zh")) {
    if (value >= 1e8) {
      return `${formatCompactScaled(value, 1e8)}亿`;
    }
    if (value >= 1e4) {
      if (value / 1e4 >= 9999.995) {
        return `${formatCompactScaled(value, 1e8)}亿`;
      }
      return `${formatCompactScaled(value, 1e4)}万`;
    }
    return String(Math.round(value));
  }
  if (value >= 1e9) {
    return `${formatCompactScaled(value, 1e9)}B`;
  }
  if (value >= 1e6) {
    if (value / 1e6 >= 999.995) {
      return `${formatCompactScaled(value, 1e9)}B`;
    }
    return `${formatCompactScaled(value, 1e6)}M`;
  }
  if (value >= 1e3) {
    return `${formatCompactScaled(value, 1e3)}K`;
  }
  return String(Math.round(value));
}

function formatCompactScaled(value, divisor) {
  const scaled = value / divisor;
  if (Math.abs(scaled - Math.round(scaled)) < 1e-6) {
    return String(Math.round(scaled));
  }
  return scaled.toFixed(2).replace(/\.?0+$/, "");
}

export function formatMarketTick(time, previousTime = null) {
  const current = new Date(Number(time) * 1000);
  const previous =
    previousTime === null ? null : new Date(Number(previousTime) * 1000);
  const changedDay =
    previous === null ||
    current.getUTCFullYear() !== previous.getUTCFullYear() ||
    current.getUTCMonth() !== previous.getUTCMonth() ||
    current.getUTCDate() !== previous.getUTCDate();

  if (changedDay) {
    return `${String(current.getUTCMonth() + 1).padStart(2, "0")}-${String(
      current.getUTCDate(),
    ).padStart(2, "0")}`;
  }
  return `${String(current.getUTCHours()).padStart(2, "0")}:${String(
    current.getUTCMinutes(),
  ).padStart(2, "0")}`;
}

/**
 * Intraday price-chart vertical axis range centred on the previous close (P0).
 *
 * Returns `null` when `previousClose` is not a positive finite number — callers
 * should fall back to the chart library's default auto-scale in that case.
 *
 * The half-axis range `R` is derived purely from the supplied bar prefix (open
 * to cursor), so live mode (bars only append) naturally only expands, and replay
 * backward/forward movement deterministically recomputes from the current prefix.
 *
 * @see docs/t0assistant/ui_layout_spec.md §6.2.1
 * @see https://github.com/shenjee/stockpilot/issues/143
 */
export function calculateIntradayPriceRange(previousClose, bars) {
  const P0 = previousClose;
  if (typeof P0 !== "number" || !Number.isFinite(P0) || P0 <= 0) {
    return null;
  }

  const INITIAL_RANGE_FLOOR = 0.01;
  const validBars = Array.isArray(bars)
    ? bars.filter(
        (bar) =>
          Number.isFinite(bar?.high) && Number.isFinite(bar?.low),
      )
    : [];

  if (validBars.length === 0) {
    const R = INITIAL_RANGE_FLOOR;
    const tickStep = (R * P0) / 4;
    return { P0, R, tickStep, yMin: P0 * (1 - R), yMax: P0 * (1 + R) };
  }

  const O = validBars[0].open;
  let H = -Infinity;
  let L = Infinity;
  for (const bar of validBars) {
    H = Math.max(H, bar.high);
    L = Math.min(L, bar.low);
  }

  const upRatio = Math.max(0, H / P0 - 1);
  const downRatio = Math.max(0, 1 - L / P0);
  const observedRange = Math.max(upRatio, downRatio);
  const initialRange =
    Number.isFinite(O) && O > 0
      ? Math.max(INITIAL_RANGE_FLOOR, Math.abs(O / P0 - 1))
      : INITIAL_RANGE_FLOOR;
  const R = Math.max(initialRange, observedRange);
  const tickStep = (R * P0) / 4;
  return { P0, R, tickStep, yMin: P0 * (1 - R), yMax: P0 * (1 + R) };
}

/**
 * Spec §6.2.1: 中轴到上下沿各四等分，刻度比例为
 * +R, +3R/4, +R/2, +R/4, 0, -R/4, -R/2, -3R/4, -R.
 * 价格刻度按 price = P0 * (1 + ratio) 计算，共 9 个刻度。
 */
export function calculateIntradayPriceTicks(P0, R) {
  if (
    typeof P0 !== "number" ||
    !Number.isFinite(P0) ||
    P0 <= 0 ||
    typeof R !== "number" ||
    !Number.isFinite(R) ||
    R < 0
  ) {
    return null;
  }
  const ticks = [];
  for (let k = 4; k >= -4; k--) {
    ticks.push(P0 * (1 + (k * R) / 4));
  }
  return ticks;
}

import { projectTradeMarkers } from "./trade-markers.mjs";

export function createChartGroupModel(snapshot, kind, layers = {}, trades = []) {
  if (kind !== FIVE_MINUTE && kind !== ONE_MINUTE) {
    throw new TypeError(`Unsupported chart group: ${kind}`);
  }

  // 回放截断（前端防线）：后端快照已按时点截断；此处再丢弃 current_time 之后的数据，
  // 防止视口或图层越过当前模拟时点。时间戳为定长字符串，字典序与时间序一致。
  const asOf = snapshot.replay?.current_time ?? null;
  const clipRows = (rows) => {
    const list = rows ?? [];
    return asOf === null
      ? list
      : list.filter((row) => row.timestamp <= asOf);
  };
  const clipChanByEnd = (rows) => {
    const list = rows ?? [];
    return asOf === null
      ? list
      : list.filter((row) => row.end_timestamp <= asOf);
  };

  const bars = clipRows(
    kind === FIVE_MINUTE
      ? snapshot.market.bars_5m
      : snapshot.market.bars_1m,
  );
  const previousClose =
    typeof snapshot.market.quote?.previous_close === "number" &&
    Number.isFinite(snapshot.market.quote.previous_close)
      ? snapshot.market.quote.previous_close
      : null;
  const indicator =
    kind === FIVE_MINUTE
      ? snapshot.indicators.five_minute
      : snapshot.indicators.one_minute;

  assertOrderedUnique(bars, `${kind} bars`);
  const barTimestamps = bars.map((bar) => bar.timestamp);
  const timestampSet = new Set(barTimestamps);
  const tradeDate =
    snapshot.session?.trade_date ?? barTimestamps[0]?.slice(0, 10) ?? null;
  const timestamps =
    kind === ONE_MINUTE && tradeDate !== null
      ? buildIntradayTradingTimeline(tradeDate)
      : barTimestamps;
  const timeByTimestamp = Object.fromEntries(
    timestamps.map((timestamp) => [timestamp, parseMarketTimestamp(timestamp)]),
  );
  const enabled = (layer) => layers[layer] !== false;

  const allowedTimes = kind === FIVE_MINUTE ? new Set(Object.values(timeByTimestamp)) : null;
  const tradeMarkers =
    kind === FIVE_MINUTE
      ? projectTradeMarkers(trades, { allowedTimes })
      : [];
  const movingAverages = {};
  for (const period of ["ma5", "ma10", "ma20", "ma30", "ma60"]) {
    movingAverages[period] =
      kind === FIVE_MINUTE && enabled(period)
        ? normalizePoints(
            clipRows(indicator.ma?.[period]),
            timestampSet,
            `five_minute ${period}`,
          )
        : [];
  }
  // BOLL 默认显示，不提供独立开关；只消费契约数据，不在 Renderer 计算。
  // 保留 null 预热值的空白语义（toLineData 转为 whitespace）。
  const boll =
    kind === FIVE_MINUTE
      ? {
          upper: normalizePoints(
            clipRows(indicator.boll?.upper),
            timestampSet,
            "five_minute boll upper",
          ),
          middle: normalizePoints(
            clipRows(indicator.boll?.middle),
            timestampSet,
            "five_minute boll middle",
          ),
          lower: normalizePoints(
            clipRows(indicator.boll?.lower),
            timestampSet,
            "five_minute boll lower",
          ),
        }
      : { upper: [], middle: [], lower: [] };
  const strokes =
    kind === FIVE_MINUTE && enabled("strokes")
      ? normalizeStrokes(
          clipChanByEnd(snapshot.chan_analysis?.strokes),
          timestampSet,
        )
      : [];
  const pivotZones =
    kind === FIVE_MINUTE && enabled("pivot_zones")
      ? normalizePivotZones(
          clipChanByEnd(snapshot.chan_analysis?.pivot_zones),
          timestampSet,
        )
      : [];
  // CZSC 买卖点映射标准 1B/1S/2B/2S/3B/3S 和结构候选 Buy?/Sell?；
  // 显示投影不修改后端 CZSC 数据。
  const czscMarkers =
    kind === FIVE_MINUTE
      ? normalizeCzscMarkers(
          clipRows(snapshot.chan_analysis?.candidate_buy_points),
          clipRows(snapshot.chan_analysis?.candidate_sell_points),
          timestampSet,
        )
      : [];
  // 背驰标注与 chan-viewer / chantheory plotting 一致：Bull Div 在下、Bear Div 在上。
  const divergenceMarkers =
    kind === FIVE_MINUTE
      ? normalizeDivergenceMarkers(
          clipRows(snapshot.chan_analysis?.divergences),
          timestampSet,
        )
      : [];

  const normalizedVolumePoints = normalizePoints(
    clipRows(indicator.volume.values),
    timestampSet,
    `${kind} volume`,
  );
  const volumePoints =
    kind === ONE_MINUTE
      ? padPointsToTimeline(normalizedVolumePoints, timestamps)
      : normalizedVolumePoints;
  if (kind === FIVE_MINUTE) {
    for (const bar of bars) {
      if (
        !bar.closed &&
        !volumePoints.some((point) => point.timestamp === bar.timestamp)
      ) {
        volumePoints.push({ timestamp: bar.timestamp, value: bar.volume });
      }
    }
  }

  return {
    kind,
    timestamps,
    timeByTimestamp,
    bars: bars.map((bar) => ({ ...bar })),
    previousClose,
    price:
      kind === FIVE_MINUTE
        ? bars.map((bar) => ({
            timestamp: bar.timestamp,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            closed: bar.closed,
          }))
        : padPointsToTimeline(
            bars.map((bar) => ({
              timestamp: bar.timestamp,
              value: bar.close,
            })),
            timestamps,
          ),
    vwap:
      kind === ONE_MINUTE
        ? padPointsToTimeline(
            normalizePoints(
              clipRows(indicator.vwap),
              timestampSet,
              "one_minute vwap",
            ),
            timestamps,
          )
        : [],
    movingAverages,
    boll,
    strokes,
    pivotZones,
    czscMarkers,
    divergenceMarkers,
    volume: volumePoints,
    volumeMa5:
      kind === FIVE_MINUTE
        ? normalizePoints(
            clipRows(indicator.volume.ma5),
            timestampSet,
            "five_minute volume ma5",
          )
        : [],
    volumeMa10:
      kind === FIVE_MINUTE
        ? normalizePoints(
            clipRows(indicator.volume.ma10),
            timestampSet,
            "five_minute volume ma10",
          )
        : [],
    macd: {
      dif: padIntradayIndicator(
        normalizePoints(
          clipRows(indicator.macd.dif),
          timestampSet,
          `${kind} macd dif`,
        ),
        kind,
        timestamps,
      ),
      dea: padIntradayIndicator(
        normalizePoints(
          clipRows(indicator.macd.dea),
          timestampSet,
          `${kind} macd dea`,
        ),
        kind,
        timestamps,
      ),
      histogram: padIntradayIndicator(
        normalizePoints(
          clipRows(indicator.macd.histogram),
          timestampSet,
          `${kind} macd histogram`,
        ),
        kind,
        timestamps,
      ),
    },
    tradeMarkers,
  };
}

function buildIntradayTradingTimeline(tradeDate) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(tradeDate)) {
    throw new TypeError(`Unsupported trade date: ${tradeDate}`);
  }
  const timestamps = [];
  for (const [start, end] of [
    [9 * 60 + 30, 11 * 60 + 30],
    [13 * 60, 15 * 60],
  ]) {
    for (let minuteOfDay = start; minuteOfDay <= end; minuteOfDay += 1) {
      const hour = String(Math.floor(minuteOfDay / 60)).padStart(2, "0");
      const minute = String(minuteOfDay % 60).padStart(2, "0");
      timestamps.push(`${tradeDate} ${hour}:${minute}:00`);
    }
  }
  return timestamps;
}

function padPointsToTimeline(points, timestamps) {
  const values = new Map(points.map((point) => [point.timestamp, point.value]));
  return timestamps.map((timestamp) => ({
    timestamp,
    value: values.get(timestamp) ?? null,
  }));
}

function padIntradayIndicator(points, kind, timestamps) {
  return kind === ONE_MINUTE
    ? padPointsToTimeline(points, timestamps)
    : points;
}

function normalizeStrokes(strokes, timestampSet) {
  return (strokes ?? []).flatMap((stroke) => {
    if (
      !timestampSet.has(stroke?.start_timestamp) ||
      !timestampSet.has(stroke?.end_timestamp) ||
      !Number.isFinite(stroke?.start_price) ||
      !Number.isFinite(stroke?.end_price)
    ) {
      return [];
    }
    return [
      {
        start: {
          timestamp: stroke.start_timestamp,
          value: stroke.start_price,
        },
        end: {
          timestamp: stroke.end_timestamp,
          value: stroke.end_price,
        },
        color:
          stroke.end_price >= stroke.start_price ? "#2563eb" : "#f97316",
        dashed: stroke.confirmed === false,
      },
    ];
  });
}

function normalizePivotZones(zones, timestampSet) {
  return (zones ?? [])
    .filter(
      (zone) =>
        timestampSet.has(zone?.start_timestamp) &&
        timestampSet.has(zone?.end_timestamp) &&
        Number.isFinite(zone?.high) &&
        Number.isFinite(zone?.low) &&
        zone.high >= zone.low,
    )
    .map((zone) => ({
      start_timestamp: zone.start_timestamp,
      end_timestamp: zone.end_timestamp,
      high: zone.high,
      low: zone.low,
      active: zone.active === true,
    }));
}

// 按 chantheory point_type 映射 1B/1S/2B/2S/3B/3S，并与 Chan Viewer 一致
// 将纯结构候选点显示为 Buy?/Sell?。问号明确表达“候选、未确认”，不是交易建议。
function czscPointLabel(pointType) {
  switch (pointType) {
    case "first_buy":
      return "1B";
    case "second_buy":
      return "2B";
    case "third_buy":
      return "3B";
    case "first_sell":
      return "1S";
    case "second_sell":
      return "2S";
    case "third_sell":
      return "3S";
    case "structure_buy_candidate":
      return "Buy?";
    case "structure_sell_candidate":
      return "Sell?";
    default:
      return null;
  }
}

function normalizeDivergenceMarkers(divergences, timestampSet) {
  // 只消费契约中的已确认背驰；价格取 meta.price（与 chantheory plotting 一致）。
  // bullish → 下方 Bull Div；bearish → 上方 Bear Div。非法类型或缺失价格跳过。
  const markers = [];
  for (const item of divergences ?? []) {
    const timestamp = item?.timestamp;
    if (!timestampSet.has(timestamp)) {
      continue;
    }
    const divergenceType = item?.divergence_type;
    if (divergenceType !== "bullish" && divergenceType !== "bearish") {
      continue;
    }
    const price = item?.meta?.price;
    if (!Number.isFinite(price)) {
      continue;
    }
    markers.push({
      timestamp,
      side: divergenceType === "bullish" ? "buy" : "sell",
      price,
      label: divergenceType === "bullish" ? "Bull Div" : "Bear Div",
      divergenceType,
    });
  }
  markers.sort((left, right) => {
    if (left.timestamp !== right.timestamp) {
      return left.timestamp < right.timestamp ? -1 : 1;
    }
    return left.price - right.price;
  });
  return markers;
}

function normalizeCzscMarkers(buyPoints, sellPoints, timestampSet) {
  // 按 timestamp + side + price 聚合：同一时刻、同侧、同价的多点合并标签（如 "1B, 2B"）；
  // 同时刻同侧但不同价的信号保留为独立标记，避免丢失结构价位。非法/缺失价格不产生标记。
  const byKey = new Map();
  const collect = (points, side) => {
    for (const point of points ?? []) {
      const timestamp = point?.timestamp;
      if (!timestampSet.has(timestamp)) {
        continue;
      }
      const price = point?.price;
      if (!Number.isFinite(price)) {
        continue;
      }
      const label = czscPointLabel(point?.point_type);
      if (!label) {
        continue;
      }
      const key = `${timestamp}|${side}|${price}`;
      let entry = byKey.get(key);
      if (!entry) {
        entry = { timestamp, side, price, labels: [] };
        byKey.set(key, entry);
      }
      entry.labels.push(label);
    }
  };
  collect(buyPoints, "buy");
  collect(sellPoints, "sell");

  const markers = [...byKey.values()].map((entry) => ({
    timestamp: entry.timestamp,
    side: entry.side,
    price: entry.price,
    label: [...new Set(entry.labels)].join(", "),
  }));
  markers.sort((left, right) => {
    if (left.timestamp !== right.timestamp) {
      return left.timestamp < right.timestamp ? -1 : 1;
    }
    return left.price - right.price;
  });
  return markers;
}

function normalizePoints(points, timestampSet, label) {
  assertOrderedUnique(points, label);
  return points.map((point) => {
    if (!timestampSet.has(point.timestamp)) {
      throw new RangeError(`${label} contains timestamp without a matching bar`);
    }
    return { timestamp: point.timestamp, value: point.value };
  });
}

function assertOrderedUnique(rows, label) {
  let previous = null;
  for (const row of rows) {
    parseMarketTimestamp(row.timestamp);
    if (previous !== null && row.timestamp <= previous) {
      throw new RangeError(`${label} must be strictly ordered and unique`);
    }
    previous = row.timestamp;
  }
}
