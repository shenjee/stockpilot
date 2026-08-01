export const DEFAULT_PRICE_SCALE_MIN_WIDTH = 58;
// Worst-case compact labels at fontSize 10 ("1234.57万" / "1000M").
export const COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH = 72;
export const PLOT_WIDTH_ALIGNMENT_TOLERANCE = 1;
export const MAX_PRICE_SCALE_SYNC_ATTEMPTS = 5;
export const NON_CONVERGED_PRICE_SCALE_PADDING = 2;

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

export function requiredPriceScaleMinimumWidth(
  priceScaleWidths,
  alignedWidth = DEFAULT_PRICE_SCALE_MIN_WIDTH,
) {
  return Math.max(
    DEFAULT_PRICE_SCALE_MIN_WIDTH,
    COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH,
    alignedWidth,
    ...priceScaleWidths,
  );
}

export function syncChartGroupPriceScaleWidths(charts, options = {}) {
  const tolerance =
    options.tolerance ?? PLOT_WIDTH_ALIGNMENT_TOLERANCE;
  const maxAttempts =
    options.maxAttempts ?? MAX_PRICE_SCALE_SYNC_ATTEMPTS;
  let alignedWidth = options.alignedWidth ?? DEFAULT_PRICE_SCALE_MIN_WIDTH;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const plotWidths = measurePlotWidths(charts);
    if (plotWidthsAligned(plotWidths, tolerance)) {
      return {
        converged: true,
        plotWidths,
        alignedPriceScaleWidth: alignedWidth,
      };
    }

    const priceScaleWidths = charts.map((chart) =>
      chart.priceScale("right").width(),
    );
    alignedWidth = requiredPriceScaleMinimumWidth(
      priceScaleWidths,
      alignedWidth,
    );
    for (const chart of charts) {
      chart.applyOptions({
        rightPriceScale: { minimumWidth: alignedWidth },
      });
    }
    options.flush?.();
  }

  const plotWidths = measurePlotWidths(charts);
  const aligned = plotWidthsAligned(plotWidths, tolerance);
  if (aligned) {
    return {
      converged: true,
      plotWidths,
      alignedPriceScaleWidth: alignedWidth,
    };
  }

  const priceScaleWidths = charts.map((chart) =>
    chart.priceScale("right").width(),
  );
  alignedWidth =
    Math.max(
      requiredPriceScaleMinimumWidth(priceScaleWidths, alignedWidth),
      ...priceScaleWidths,
    ) + NON_CONVERGED_PRICE_SCALE_PADDING;
  for (const chart of charts) {
    chart.applyOptions({
      rightPriceScale: { minimumWidth: alignedWidth },
    });
  }
  options.flush?.();
  const forcedPlotWidths = measurePlotWidths(charts);
  return {
    converged: plotWidthsAligned(forcedPlotWidths, tolerance),
    plotWidths: forcedPlotWidths,
    alignedPriceScaleWidth: alignedWidth,
  };
}
