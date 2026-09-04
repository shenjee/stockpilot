export function createChartProjection(snapshot, identity = {}) {
  return {
    snapshot,
    serviceGeneration: integerOrNull(identity.service_generation),
    sessionId:
      stringOrNull(identity.session_id) ??
      stringOrNull(snapshot.session?.session_id),
    revision:
      integerOrNull(identity.revision) ??
      integerOrNull(snapshot.session?.revision),
    rebaselineRequired: false,
  };
}

export function beginChartSession(snapshot, serviceGeneration, sessionId = null) {
  // Keep the last successful snapshot visible while a replacement Session is
  // loading, but reset its event identity. In particular, do not inherit the
  // previous snapshot's revision: a new Session starts again at revision 1.
  return {
    snapshot,
    serviceGeneration: integerOrNull(serviceGeneration),
    sessionId: stringOrNull(sessionId),
    revision: null,
    rebaselineRequired: false,
  };
}

export function applyWorkbenchSnapshot(
  projection,
  snapshot,
  identity,
) {
  const candidate = createChartProjection(snapshot, identity);
  if (
    candidate.serviceGeneration === null ||
    candidate.sessionId === null ||
    candidate.revision === null
  ) {
    return projection;
  }
  if (
    (projection.serviceGeneration !== null &&
      candidate.serviceGeneration !== projection.serviceGeneration) ||
    (projection.sessionId !== null &&
      candidate.sessionId !== projection.sessionId) ||
    (projection.revision !== null &&
      candidate.revision <= projection.revision)
  ) {
    return projection;
  }
  return candidate;
}

export function applyLiveChartEvent(projection, event) {
  if (!event || typeof event !== "object" || projection.rebaselineRequired) {
    return projection;
  }

  // Full snapshots must replace via applyWorkbenchSnapshot / applySnapshot.
  // Treating them as increments would advance revision while keeping the old
  // snapshot body (#155 review P1).
  if (event.event_type === "workbench_snapshot") {
    return projection;
  }

  const eventGeneration = integerOrNull(event.service_generation);
  const eventSessionId = stringOrNull(event.session_id);
  const eventRevision = integerOrNull(event.revision);
  const serviceGeneration =
    projection.serviceGeneration ?? eventGeneration;
  const sessionId = projection.sessionId ?? eventSessionId;
  const currentRevision = projection.revision;

  if (
    (serviceGeneration !== null &&
      eventGeneration !== serviceGeneration) ||
    (sessionId !== null && eventSessionId !== sessionId) ||
    eventRevision === null
  ) {
    return projection;
  }
  if (currentRevision !== null && eventRevision <= currentRevision) {
    return projection;
  }
  if (currentRevision !== null && eventRevision > currentRevision + 1) {
    return {
      ...projection,
      serviceGeneration,
      sessionId,
      rebaselineRequired: true,
    };
  }

  let snapshot = projection.snapshot;
  if (event.event_type === "market_update") {
    snapshot = applyMarketUpdate(snapshot, event.payload);
  } else if (event.event_type === "indicators_updated") {
    snapshot = applyIndicatorUpdate(snapshot, event.payload);
  } else if (
    event.event_type === "chan_analysis_replaced" &&
    event.payload &&
    typeof event.payload === "object"
  ) {
    snapshot = { ...snapshot, chan_analysis: event.payload };
  } else if (
    event.event_type === "chan_analysis_30m_replaced" &&
    event.payload &&
    typeof event.payload === "object"
  ) {
    snapshot = { ...snapshot, chan_analysis_30m: event.payload };
  } else if (
    event.event_type === "live_market_view_updated" &&
    event.payload &&
    typeof event.payload === "object"
  ) {
    snapshot = { ...snapshot, live_market_view: event.payload };
  }

  return {
    snapshot,
    serviceGeneration,
    sessionId,
    revision: eventRevision,
    rebaselineRequired: false,
  };
}

function integerOrNull(value) {
  return Number.isInteger(value) ? value : null;
}

function stringOrNull(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function applyMarketUpdate(snapshot, payload) {
  if (!payload || typeof payload !== "object") return snapshot;
  if (payload.target === "quote") {
    return {
      ...snapshot,
      market: { ...snapshot.market, quote: payload.quote ?? null },
    };
  }
  if (
    !Array.isArray(payload.bars) ||
    !["bars_1m", "bars_5m", "bars_30m", "daily_bars"].includes(payload.target)
  ) return snapshot;
  const merged =
    payload.target === "bars_5m"
      ? mergeFiveMinuteBars(snapshot.market.bars_5m, payload.bars)
      : payload.target === "bars_30m"
        ? mergeThirtyMinuteBars(snapshot.market.bars_30m, payload.bars)
        : mergeTimestampRows(snapshot.market[payload.target], payload.bars);
  return {
    ...snapshot,
    market: {
      ...snapshot.market,
      [payload.target]: merged,
    },
  };
}

function applyIndicatorUpdate(snapshot, incoming) {
  if (!incoming?.five_minute || !incoming?.one_minute) {
    return snapshot;
  }
  const current = snapshot.indicators;
  const incomingThirty = incoming.thirty_minute;
  const currentThirty = current.thirty_minute ?? {
    ma: {},
    boll: {},
    volume: {},
    macd: {},
  };
  const merged = {
    ...current,
    five_minute: {
      ...current.five_minute,
      ma: mergePointGroup(
        current.five_minute.ma,
        incoming.five_minute.ma,
        ["ma5", "ma10", "ma20", "ma30", "ma60"],
      ),
      boll: {
        ...current.five_minute.boll,
        ...incoming.five_minute.boll,
        upper: mergeTimestampRows(
          current.five_minute.boll?.upper,
          incoming.five_minute.boll?.upper,
        ),
        middle: mergeTimestampRows(
          current.five_minute.boll?.middle,
          incoming.five_minute.boll?.middle,
        ),
        lower: mergeTimestampRows(
          current.five_minute.boll?.lower,
          incoming.five_minute.boll?.lower,
        ),
      },
      volume: {
        ...current.five_minute.volume,
        ...incoming.five_minute.volume,
        values: mergeTimestampRows(
          current.five_minute.volume.values,
          incoming.five_minute.volume.values,
        ),
        ma5: mergeTimestampRows(
          current.five_minute.volume.ma5,
          incoming.five_minute.volume.ma5,
        ),
        ma10: mergeTimestampRows(
          current.five_minute.volume.ma10,
          incoming.five_minute.volume.ma10,
        ),
      },
      macd: mergeMacd(
        current.five_minute.macd,
        incoming.five_minute.macd,
      ),
    },
    thirty_minute: incomingThirty
      ? {
          ...currentThirty,
          ma: mergePointGroup(
            currentThirty.ma,
            incomingThirty.ma,
            ["ma5", "ma10", "ma20", "ma30", "ma60"],
          ),
          boll: {
            ...currentThirty.boll,
            ...incomingThirty.boll,
            upper: mergeTimestampRows(
              currentThirty.boll?.upper,
              incomingThirty.boll?.upper,
            ),
            middle: mergeTimestampRows(
              currentThirty.boll?.middle,
              incomingThirty.boll?.middle,
            ),
            lower: mergeTimestampRows(
              currentThirty.boll?.lower,
              incomingThirty.boll?.lower,
            ),
          },
          volume: {
            ...currentThirty.volume,
            ...incomingThirty.volume,
            values: mergeTimestampRows(
              currentThirty.volume?.values,
              incomingThirty.volume?.values,
            ),
            ma5: mergeTimestampRows(
              currentThirty.volume?.ma5,
              incomingThirty.volume?.ma5,
            ),
            ma10: mergeTimestampRows(
              currentThirty.volume?.ma10,
              incomingThirty.volume?.ma10,
            ),
          },
          macd: mergeMacd(currentThirty.macd ?? {}, incomingThirty.macd ?? {}),
        }
      : current.thirty_minute,
    one_minute: {
      ...current.one_minute,
      vwap: mergeTimestampRows(
        current.one_minute.vwap,
        incoming.one_minute.vwap,
      ),
      volume: {
        ...current.one_minute.volume,
        ...incoming.one_minute.volume,
        values: mergeTimestampRows(
          current.one_minute.volume.values,
          incoming.one_minute.volume.values,
        ),
      },
      macd: mergeMacd(
        current.one_minute.macd,
        incoming.one_minute.macd,
      ),
    },
  };
  const fiveMinuteTimestamps = new Set(
    snapshot.market.bars_5m.map((bar) => bar.timestamp),
  );
  const thirtyMinuteTimestamps = new Set(
    (snapshot.market.bars_30m ?? []).map((bar) => bar.timestamp),
  );
  const oneMinuteTimestamps = new Set(
    snapshot.market.bars_1m.map((bar) => bar.timestamp),
  );
  return {
    ...snapshot,
    indicators: {
      ...merged,
      five_minute: alignIndicatorBranch(
        merged.five_minute,
        fiveMinuteTimestamps,
      ),
      thirty_minute: merged.thirty_minute
        ? alignIndicatorBranch(merged.thirty_minute, thirtyMinuteTimestamps)
        : merged.thirty_minute,
      one_minute: alignIndicatorBranch(
        merged.one_minute,
        oneMinuteTimestamps,
      ),
    },
  };
}

function alignIndicatorBranch(value, allowedTimestamps) {
  if (Array.isArray(value)) {
    return value.filter(
      (row) => row && allowedTimestamps.has(row.timestamp),
    );
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, nested]) => [
      key,
      alignIndicatorBranch(nested, allowedTimestamps),
    ]),
  );
}

function mergePointGroup(current = {}, incoming = {}, keys) {
  return Object.fromEntries(
    keys.map((key) => [
      key,
      mergeTimestampRows(current[key], incoming[key]),
    ]),
  );
}

function mergeMacd(current, incoming) {
  return {
    ...current,
    ...incoming,
    dif: mergeTimestampRows(current.dif, incoming.dif),
    dea: mergeTimestampRows(current.dea, incoming.dea),
    histogram: mergeTimestampRows(current.histogram, incoming.histogram),
  };
}

/**
 * Upsert 5m bars and drop unclosed rows absent from the increment.
 *
 * Mirrors Python `LiveProjectionStore` / `merge_five_minute_bars`. Locked by
 * `contracts/fixtures/live-five-minute-merge-v1.json` (#155).
 */
export function mergeFiveMinuteBars(current, incoming) {
  // Unclosed 5m rows whose timestamps are absent from the increment are the
  // previous bucket's dynamic K and must be dropped. Closed history is never
  // deleted by this path.
  const incomingTimestamps = new Set(
    (incoming ?? []).map((row) => row.timestamp),
  );
  const retained = (current ?? []).filter(
    (row) => row.closed === true || incomingTimestamps.has(row.timestamp),
  );
  return mergeTimestampRows(retained, incoming);
}

/**
 * Upsert 30m bars and drop unclosed rows absent from the increment.
 *
 * Mirrors Python `LiveProjectionStore` / `merge_thirty_minute_bars`. Same
 * semantics as `mergeFiveMinuteBars` applied to the 30m timeframe: one-minute
 * refreshes publish only the current dynamic (unclosed) 30m bar, so a plain
 * timestamp merge cannot delete the previous bucket's dynamic bar once the
 * boundary advances. Closed history is never deleted; official 30m increments
 * carry the full bar list including the next bucket's dynamic bar.
 */
export function mergeThirtyMinuteBars(current, incoming) {
  const incomingTimestamps = new Set(
    (incoming ?? []).map((row) => row.timestamp),
  );
  const retained = (current ?? []).filter(
    (row) => row.closed === true || incomingTimestamps.has(row.timestamp),
  );
  return mergeTimestampRows(retained, incoming);
}

function mergeTimestampRows(current, incoming) {
  const byTimestamp = new Map(
    (current ?? []).map((row) => [row.timestamp, row]),
  );
  for (const row of incoming ?? []) {
    byTimestamp.set(row.timestamp, row);
  }
  return [...byTimestamp.values()].sort((left, right) => {
    if (left.timestamp === right.timestamp) {
      return 0;
    }
    return left.timestamp < right.timestamp ? -1 : 1;
  });
}
