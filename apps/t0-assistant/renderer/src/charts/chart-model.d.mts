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
  session?: {
    session_id: string;
    session_type?: "live" | "replay" | "historical";
    symbol?: string;
    trade_date?: string;
    state?: string;
    revision?: number;
  };
  replay?: {
    granularity: "one_minute" | "five_minute";
    current_time: string;
    next_bar_time: string | null;
    start_time: string;
    end_time: string;
    playing: boolean;
    playback_speed: 1 | 2 | 5 | 10;
    step_seconds: 60 | 300;
  };
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
      boll?: {
        period: 20;
        stddev: 2.0;
        upper: IndicatorPoint[];
        middle: IndicatorPoint[];
        lower: IndicatorPoint[];
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
    candidate_buy_points?: CandidatePoint[];
    candidate_sell_points?: CandidatePoint[];
    divergences?: Divergence[];
  };
}

export interface Divergence {
  id: string;
  divergence_type: "bullish" | "bearish" | string;
  reference_type: string;
  reference_id: string;
  timestamp: string;
  strength: string;
  confirmed: boolean;
  description: string;
  meta?: {
    price?: number;
    [key: string]: unknown;
  };
}

export interface CandidatePoint {
  id: string;
  point_type: string;
  timestamp: string;
  price: number;
  reference_id: string;
  confirmed: boolean;
  reason: string;
  meta?: Record<string, unknown>;
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
  active?: boolean;
}

export interface BollModel {
  upper: IndicatorPoint[];
  middle: IndicatorPoint[];
  lower: IndicatorPoint[];
}

export interface CzscMarker {
  timestamp: string;
  side: "buy" | "sell";
  /** 候选点契约价格；标记按 (timestamp, side, price) 定位与去重。 */
  price: number;
  label: string;
}

export interface DivergenceMarker {
  timestamp: string;
  /** bullish → buy（K 线下方）；bearish → sell（K 线上方）。 */
  side: "buy" | "sell";
  price: number;
  label: "Bull Div" | "Bear Div";
  divergenceType: "bullish" | "bearish";
}

export interface TradeMarkerModel {
  trade_id: string;
  trade_scope: "real" | "simulated";
  time: number;
  price: number;
  side: "buy" | "sell";
  quantity: number;
  label: string;
  color: string;
  shape: "circle" | "square";
}

export interface ChartGroupModel {
  kind: ChartGroupKindValue;
  timestamps: string[];
  timeByTimestamp: Record<string, number>;
  bars: MarketBar[];
  /** Previous close (P0) for intraday symmetric price-range, or null. */
  previousClose: number | null;
  price: Array<
    | {
        timestamp: string;
        open: number;
        high: number;
        low: number;
        close: number;
        closed: boolean;
      }
    | { timestamp: string; value: number | null }
  >;
  vwap: IndicatorPoint[];
  movingAverages: {
    ma5: IndicatorPoint[];
    ma10: IndicatorPoint[];
    ma20: IndicatorPoint[];
    ma30: IndicatorPoint[];
    ma60: IndicatorPoint[];
  };
  boll: BollModel;
  strokes: PriceLineOverlay[];
  pivotZones: PriceBoxOverlay[];
  czscMarkers: CzscMarker[];
  divergenceMarkers: DivergenceMarker[];
  volume: IndicatorPoint[];
  volumeMa5: IndicatorPoint[];
  volumeMa10: IndicatorPoint[];
  macd: {
    dif: IndicatorPoint[];
    dea: IndicatorPoint[];
    histogram: IndicatorPoint[];
  };
  tradeMarkers: TradeMarkerModel[];
}

export interface IntradayPriceRange {
  P0: number;
  R: number;
  tickStep: number;
  yMin: number;
  yMax: number;
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
export function formatVolumeAxisLabel(
  value: number,
  locale?: string,
): string;
export function formatVolumeAxisLabels(prices: readonly number[]): string[];
export function roundHalfAwayFromZero(
  value: number,
  decimalPlaces: number,
): number;
export function formatPriceAxisTickLabel(value: number): string;
export function formatPriceExactLabel(value: number): string;
export function formatPriceAxisTickLabels(prices: readonly number[]): string[];
export const PRICE_AXIS_INTEGER_TICK_ABS: 100;
export const PRICE_AXIS_FINE_MIN_MOVE: 0.01;
export const PRICE_AXIS_INTEGER_MIN_MOVE: 1;
export function resolvePriceAxisMinMove(
  rangeMin: number | null | undefined,
  rangeMax: number | null | undefined,
): number;
export function computeValidPriceBase(minMove: number): number;
export function createPriceExactPriceFormat(minMove?: number): Readonly<{
  type: "custom";
  formatter: (value: number) => string;
  tickmarksFormatter: (prices: readonly number[]) => string[];
  minMove: number;
  base: number;
}>;
export const PRICE_EXACT_PRICE_FORMAT: Readonly<{
  type: "custom";
  formatter: (value: number) => string;
  tickmarksFormatter: (prices: readonly number[]) => string[];
  minMove: number;
  base: number;
}>;
export function calculateIntradayPriceRange(
  previousClose: number | null | undefined,
  bars: ReadonlyArray<{ open: number; high: number; low: number }> | null,
): IntradayPriceRange | null;

export function calculateIntradayPriceTicks(
  P0: number,
  R: number,
): number[] | null;

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
  trades?: Array<{ trade_id: string; bucket_start: string; trade_scope: "real" | "simulated"; symbol: string; side: "buy" | "sell"; executed_at: string; price: number; quantity: number; fee: number | null; note: string; fee_plan_id: string | null }>,
): ChartGroupModel;
