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
  if (
    !payload ||
    !Array.isArray(payload.bars) ||
    (payload.target !== "bars_1m" && payload.target !== "bars_5m")
  ) {
    return snapshot;
  }
  return {
    ...snapshot,
    market: {
      ...snapshot.market,
      [payload.target]: mergeTimestampRows(
        snapshot.market[payload.target],
        payload.bars,
      ),
    },
  };
}

function applyIndicatorUpdate(snapshot, incoming) {
  if (!incoming?.five_minute || !incoming?.one_minute) {
    return snapshot;
  }
  const current = snapshot.indicators;
  return {
    ...snapshot,
    indicators: {
      ...current,
      five_minute: {
        ...current.five_minute,
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
    },
  };
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
