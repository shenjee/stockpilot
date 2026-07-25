export type ChartGroupKindValue = "five_minute" | "one_minute";

export interface MarketBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  closed: boolean;
}

export interface IndicatorPoint {
  timestamp: string;
  value: number | null;
}

export interface MacdContract {
  fast_period: 12;
  slow_period: 26;
  signal_period: 9;
  dif: IndicatorPoint[];
  dea: IndicatorPoint[];
  histogram: IndicatorPoint[];
}

export interface WorkbenchChartSnapshot {
  timezone: "Asia/Shanghai";
  session?: { session_id: string; revision?: number };
  market: {
    bars_1m: MarketBar[];
    bars_5m: MarketBar[];
  };
  indicators: {
    five_minute: {
      volume: {
        values: IndicatorPoint[];
        ma5: IndicatorPoint[];
        ma10: IndicatorPoint[];
      };
      macd: MacdContract;
    };
    one_minute: {
      vwap: IndicatorPoint[];
      volume: { values: IndicatorPoint[] };
      macd: MacdContract;
    };
  };
}

export interface ChartGroupModel {
  kind: ChartGroupKindValue;
  timestamps: string[];
  timeByTimestamp: Record<string, number>;
  bars: MarketBar[];
  price: Array<
    | {
        timestamp: string;
        open: number;
        high: number;
        low: number;
        close: number;
        closed: boolean;
      }
    | { timestamp: string; value: number }
  >;
  vwap: IndicatorPoint[];
  volume: IndicatorPoint[];
  volumeMa5: IndicatorPoint[];
  volumeMa10: IndicatorPoint[];
  macd: {
    dif: IndicatorPoint[];
    dea: IndicatorPoint[];
    histogram: IndicatorPoint[];
  };
}

export const ChartGroupKind: Readonly<{
  FIVE_MINUTE: "five_minute";
  ONE_MINUTE: "one_minute";
}>;

export function parseMarketTimestamp(timestamp: string): number;
export function formatMarketTick(
  time: number,
  previousTime?: number | null,
): string;
export function createChartGroupModel(
  snapshot: WorkbenchChartSnapshot,
  kind: ChartGroupKindValue,
): ChartGroupModel;
