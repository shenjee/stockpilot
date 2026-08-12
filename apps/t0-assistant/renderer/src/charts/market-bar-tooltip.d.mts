export const MARKET_BAR_TOOLTIP_MARGIN_PX: number;
export const MARKET_BAR_TOOLTIP_CLASS: string;

export function formatMarketBarTooltipDate(
  timestamp: string | null | undefined,
): string;
export function formatMarketBarTooltipTime(
  timestamp: string | null | undefined,
): string;
export function formatMarketBarTooltipPrice(value: unknown): string;
export function formatMarketBarTooltipVolume(
  value: unknown,
  locale?: string,
): string;
export function resolveMarketBarDirection(
  open: unknown,
  close: unknown,
): { arrow: "▲" | "▼"; up: boolean } | null;
export function findMarketBarByTimestamp(
  bars: ReadonlyArray<{ timestamp?: string }> | null | undefined,
  timestamp: string | null | undefined,
): object | null;
export function findMarketBarByUtcSeconds(
  bars: ReadonlyArray<{ timestamp?: string }> | null | undefined,
  timeByTimestamp: Record<string, number> | null | undefined,
  utcSeconds: unknown,
): object | null;
export function resolveMarketBarTooltipCorner(input: {
  barCoordinate: number;
  plotWidth: number;
}): "left" | "right" | null;
export function shouldShowMarketBarTooltip(input: {
  pointerOverPricePlot: boolean;
  isDragging: boolean;
  bar: object | null | undefined;
}): boolean;

export interface MarketBarTooltipViewModel {
  date: string;
  time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  direction: { arrow: "▲" | "▼"; up: boolean } | null;
}

export function buildMarketBarTooltipViewModel(
  bar: object | null | undefined,
): MarketBarTooltipViewModel | null;
export function renderMarketBarTooltipContent(
  root: HTMLElement,
  viewModel: MarketBarTooltipViewModel | null,
): void;
export function isPointerInPricePlotArea(input: {
  clientX: number;
  clientY: number;
  containerRect: { left: number; top: number; width: number; height: number };
  plotWidth: number;
  plotHeight: number;
}): boolean;
