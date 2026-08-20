export type TradeScope = "real" | "simulated";
export type TradeSide = "buy" | "sell";

/** Mirrors the frozen trade_record shape in app-v2.schema.json. */
export interface TradeRecord {
  trade_id: string;
  bucket_start: string;
  trade_scope: TradeScope;
  symbol: string;
  side: TradeSide;
  executed_at: string;
  price: number;
  quantity: number;
  fee: number | null;
  note: string;
  fee_plan_id: string | null;
}

export interface TradeMarkerModel {
  trade_id: string;
  trade_scope: TradeScope;
  /** Chart time (Unix timestamp of the 5m bucket). */
  time: number;
  /** Actual trade price used as the y-coordinate. */
  price: number;
  side: TradeSide;
  /** Shares traded. */
  quantity: number;
  /** Display label, e.g. "B2" / "S0.5". */
  label: string;
  color: string;
  shape: "circle" | "square";
}

export interface ProjectTradeMarkersOptions {
  /** If provided, only keep markers whose chart time matches an existing 5m K-line time. */
  allowedTimes?: Set<number> | number[] | undefined;
}

export function parseMarketTimestampSeconds(timestamp: string): number;

export function formatLotLabel(quantity: number): string;

export function projectTradeMarker(
  trade: TradeRecord,
): TradeMarkerModel | null;

export function projectTradeMarkers(
  trades: TradeRecord[],
  options?: ProjectTradeMarkersOptions,
): TradeMarkerModel[];

export function sortTradeMarkers(markers: TradeMarkerModel[]): TradeMarkerModel[];
