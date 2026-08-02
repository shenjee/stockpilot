export const DEFAULT_PRICE_SCALE_MIN_WIDTH = 58;
// Product token: docs/t0assistant/ui_layout_spec.md §4.3 chartRightYAxisWidth.
export const CHART_RIGHT_Y_AXIS_WIDTH = 72;
export const COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH = CHART_RIGHT_Y_AXIS_WIDTH;
export const PLOT_WIDTH_ALIGNMENT_TOLERANCE = 1;

export function plotWidthsAligned(
  widths,
  tolerance = PLOT_WIDTH_ALIGNMENT_TOLERANCE,
) {
  if (widths.length <= 1) {
    return true;
  }
  const reference = widths[0];
  return widths.every((width) => Math.abs(width - reference) <= tolerance);
}

export function measurePlotWidths(charts) {
  return charts.map((chart) => chart.timeScale().width());
}

export function measureRightPriceScaleWidths(charts) {
  return charts.map((chart) => chart.priceScale("right").width());
}

export function syncChartGroupPriceScaleWidths(charts, options = {}) {
  const tolerance =
    options.tolerance ?? PLOT_WIDTH_ALIGNMENT_TOLERANCE;
  const width = CHART_RIGHT_Y_AXIS_WIDTH;

  for (const chart of charts) {
    chart.applyOptions({
      rightPriceScale: { minimumWidth: width },
    });
  }
  options.flush?.();
  const plotWidths = measurePlotWidths(charts);
  const rightPriceScaleWidths = measureRightPriceScaleWidths(charts);
  return {
    converged:
      plotWidthsAligned(plotWidths, tolerance) &&
      rightPriceScaleWidths.every((scaleWidth) => scaleWidth === width),
    plotWidths,
    rightPriceScaleWidths,
    alignedPriceScaleWidth: width,
  };
}
