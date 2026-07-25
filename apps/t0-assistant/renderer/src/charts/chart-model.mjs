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

export function createChartGroupModel(snapshot, kind) {
  if (kind !== FIVE_MINUTE && kind !== ONE_MINUTE) {
    throw new TypeError(`Unsupported chart group: ${kind}`);
  }

  const bars =
    kind === FIVE_MINUTE
      ? snapshot.market.bars_5m
      : snapshot.market.bars_1m;
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

  const volumePoints = normalizePoints(
    indicator.volume.values,
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
        ? normalizePoints(indicator.vwap, timestampSet, "one_minute vwap")
        : [],
    volume: volumePoints,
    volumeMa5:
      kind === FIVE_MINUTE
        ? normalizePoints(
            indicator.volume.ma5,
            timestampSet,
            "five_minute volume ma5",
          )
        : [],
    volumeMa10:
      kind === FIVE_MINUTE
        ? normalizePoints(
            indicator.volume.ma10,
            timestampSet,
            "five_minute volume ma10",
          )
        : [],
    macd: {
      dif: normalizePoints(indicator.macd.dif, timestampSet, `${kind} macd dif`),
      dea: normalizePoints(indicator.macd.dea, timestampSet, `${kind} macd dea`),
      histogram: normalizePoints(
        indicator.macd.histogram,
        timestampSet,
        `${kind} macd histogram`,
      ),
    },
  };
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
