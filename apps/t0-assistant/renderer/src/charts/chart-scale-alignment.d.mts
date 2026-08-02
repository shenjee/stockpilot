import type { IChartApi } from "lightweight-charts";

export const DEFAULT_PRICE_SCALE_MIN_WIDTH: number;
export const CHART_RIGHT_Y_AXIS_WIDTH: number;
export const COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH: number;
export const PLOT_WIDTH_ALIGNMENT_TOLERANCE: number;

export function plotWidthsAligned(
  widths: number[],
  tolerance?: number,
): boolean;

export function measurePlotWidths(charts: IChartApi[]): number[];
export function measureRightPriceScaleWidths(charts: IChartApi[]): number[];

export interface ChartGroupPriceScaleSyncResult {
  converged: boolean;
  plotWidths: number[];
  rightPriceScaleWidths: number[];
  alignedPriceScaleWidth: number;
}

export function syncChartGroupPriceScaleWidths(
  charts: IChartApi[],
  options?: {
    tolerance?: number;
    alignedWidth?: number;
    flush?: () => void;
  },
): ChartGroupPriceScaleSyncResult;
