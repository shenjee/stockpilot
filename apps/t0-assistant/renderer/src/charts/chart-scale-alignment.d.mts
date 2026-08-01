import type { IChartApi } from "lightweight-charts";

export const DEFAULT_PRICE_SCALE_MIN_WIDTH: number;
export const COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH: number;
export const PLOT_WIDTH_ALIGNMENT_TOLERANCE: number;
export const MAX_PRICE_SCALE_SYNC_ATTEMPTS: number;
export const NON_CONVERGED_PRICE_SCALE_PADDING: number;

export function plotWidthsAligned(
  widths: number[],
  tolerance?: number,
): boolean;

export function measurePlotWidths(charts: IChartApi[]): number[];

export function requiredPriceScaleMinimumWidth(
  priceScaleWidths: number[],
  alignedWidth?: number,
): number;

export interface ChartGroupPriceScaleSyncResult {
  converged: boolean;
  plotWidths: number[];
  alignedPriceScaleWidth: number;
}

export function syncChartGroupPriceScaleWidths(
  charts: IChartApi[],
  options?: {
    tolerance?: number;
    maxAttempts?: number;
    alignedWidth?: number;
    flush?: () => void;
  },
): ChartGroupPriceScaleSyncResult;
