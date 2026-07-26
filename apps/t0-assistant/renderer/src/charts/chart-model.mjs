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

export function createChartGroupModel(snapshot, kind, layers = {}) {
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
  const indicator =
    kind === FIVE_MINUTE
      ? snapshot.indicators.five_minute
      : snapshot.indicators.one_minute;

  assertOrderedUnique(bars, `${kind} bars`);
  const timestamps = bars.map((bar) => bar.timestamp);
  const timestampSet = new Set(timestamps);
  const timeByTimestamp = Object.fromEntries(
    timestamps.map((timestamp) => [timestamp, parseMarketTimestamp(timestamp)]),
  );
  const enabled = (layer) => layers[layer] !== false;
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
  // CZSC 买卖点只映射 1B/1S/2B/2S/3B/3S；开关只控制显示，不影响 CZSC 数据。
  const czscMarkers =
    kind === FIVE_MINUTE
      ? normalizeCzscMarkers(
          clipRows(snapshot.chan_analysis?.candidate_buy_points),
          clipRows(snapshot.chan_analysis?.candidate_sell_points),
          timestampSet,
        )
      : [];

  const volumePoints = normalizePoints(
    clipRows(indicator.volume.values),
    timestampSet,
    `${kind} volume`,
  );
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
        : bars.map((bar) => ({
            timestamp: bar.timestamp,
            value: bar.close,
          })),
    vwap:
      kind === ONE_MINUTE
        ? normalizePoints(
            clipRows(indicator.vwap),
            timestampSet,
            "one_minute vwap",
          )
        : [],
    movingAverages,
    boll,
    strokes,
    pivotZones,
    czscMarkers,
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
      dif: normalizePoints(
        clipRows(indicator.macd.dif),
        timestampSet,
        `${kind} macd dif`,
      ),
      dea: normalizePoints(
        clipRows(indicator.macd.dea),
        timestampSet,
        `${kind} macd dea`,
      ),
      histogram: normalizePoints(
        clipRows(indicator.macd.histogram),
        timestampSet,
        `${kind} macd histogram`,
      ),
    },
  };
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

// 按 chantheory point_type 映射 1B/1S/2B/2S/3B/3S（与 packages/chantheory/plotting.py 一致）。
// structure_*_candidate 等非标准类型不渲染，避免产生歧义或建议性标记。
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
    default:
      return null;
  }
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
