export type CrosshairTarget =
  | { action: "clear" }
  | { action: "position"; value: number };

export function resolveCrosshairTarget(
  values: Map<number, number>,
  time: number,
): CrosshairTarget;

export function buildCrosshairFallbackIndex(
  seriesPoints: Array<
    Array<{ timestamp: string; value: number | null }>
  >,
  timeByTimestamp: Record<string, number>,
): {
  values: Map<number, number>;
  seriesIndexes: Map<number, number>;
};
