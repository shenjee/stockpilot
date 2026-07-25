export function resolveCrosshairTarget(values, time) {
  const value = values.get(time);
  return value === undefined
    ? { action: "clear" }
    : { action: "position", value };
}

export function buildCrosshairFallbackIndex(seriesPoints, timeByTimestamp) {
  const values = new Map();
  const seriesIndexes = new Map();
  seriesPoints.forEach((points, seriesIndex) => {
    for (const point of points) {
      const time = timeByTimestamp[point.timestamp];
      if (
        Number.isFinite(time) &&
        point.value !== null &&
        !values.has(time)
      ) {
        values.set(time, point.value);
        seriesIndexes.set(time, seriesIndex);
      }
    }
  });
  return { values, seriesIndexes };
}
