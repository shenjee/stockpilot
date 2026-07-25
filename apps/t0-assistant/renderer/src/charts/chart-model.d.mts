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
    daily_bars?: MarketBar[];
    quote?: {
      timestamp: string;
      latest_price: number;
      change_percent: number;
      open: number;
      high: number;
      low: number;
      previous_close: number;
      volume: number;
      amount: number;
      volume_ratio: number | null;
      order_imbalance: number | null;
      turnover_rate: number | null;
    } | null;
  };
  indicators: {
    five_minute: {
      ma?: {
        ma5: IndicatorPoint[];
        ma10: IndicatorPoint[];
        ma20: IndicatorPoint[];
        ma30: IndicatorPoint[];
        ma60: IndicatorPoint[];
      };
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
  chan_analysis?: {
    strokes?: Array<{
      start_timestamp: string;
      end_timestamp: string;
      start_price: number;
      end_price: number;
      confirmed?: boolean;
    }>;
    pivot_zones?: Array<{
      start_timestamp: string;
      end_timestamp: string;
      high: number;
      low: number;
      active?: boolean;
    }>;
  };
}

export interface PriceLineOverlay {
  start: { timestamp: string; value: number };
  end: { timestamp: string; value: number };
  color?: string;
  dashed?: boolean;
}

export interface PriceBoxOverlay {
  start_timestamp: string;
  end_timestamp: string;
  high: number;
  low: number;
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
  movingAverages: {
    ma5: IndicatorPoint[];
    ma10: IndicatorPoint[];
    ma20: IndicatorPoint[];
    ma30: IndicatorPoint[];
    ma60: IndicatorPoint[];
  };
  strokes: PriceLineOverlay[];
  pivotZones: PriceBoxOverlay[];
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
  layers?: {
    ma5?: boolean;
    ma10?: boolean;
    ma20?: boolean;
    ma30?: boolean;
    ma60?: boolean;
    strokes?: boolean;
    pivot_zones?: boolean;
  },
): ChartGroupModel;
