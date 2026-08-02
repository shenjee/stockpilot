import assert from "node:assert/strict";
import test from "node:test";

import {
  CHART_RIGHT_Y_AXIS_WIDTH,
  COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH,
  plotWidthsAligned,
  syncChartGroupPriceScaleWidths,
} from "../renderer/src/charts/chart-scale-alignment.mjs";

test("plotWidthsAligned accepts sub-pixel drift within tolerance", () => {
  assert.equal(plotWidthsAligned([800, 800.5, 799.6]), true);
  assert.equal(plotWidthsAligned([800, 802, 800]), false);
});

test("chart right Y axis width token matches product spec", () => {
  assert.equal(CHART_RIGHT_Y_AXIS_WIDTH, 72);
  assert.equal(COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH, CHART_RIGHT_Y_AXIS_WIDTH);
});

test("syncChartGroupPriceScaleWidths applies fixed token width", () => {
  const applied = [];
  const charts = [
    {
      timeScale: () => ({ width: () => 728 }),
      priceScale: () => ({ width: () => 72 }),
      applyOptions: (options) => {
        applied.push(options.rightPriceScale.minimumWidth);
      },
    },
    {
      timeScale: () => ({ width: () => 726 }),
      priceScale: () => ({ width: () => 74 }),
      applyOptions: (options) => {
        applied.push(options.rightPriceScale.minimumWidth);
      },
    },
  ];
  const result = syncChartGroupPriceScaleWidths(charts);
  assert.deepEqual(applied, [72, 72]);
  assert.equal(result.alignedPriceScaleWidth, CHART_RIGHT_Y_AXIS_WIDTH);
  assert.deepEqual(result.rightPriceScaleWidths, [72, 74]);
  assert.equal(result.converged, false);
});

test("syncChartGroupPriceScaleWidths does not ratchet on repeated calls", () => {
  let aligned = CHART_RIGHT_Y_AXIS_WIDTH;
  const history = [];
  for (let tick = 0; tick < 12; tick += 1) {
    const charts = [800, 798, 798].map((containerWidth) => {
      const state = { minWidth: CHART_RIGHT_Y_AXIS_WIDTH };
      return {
        timeScale: () => ({
          width: () => containerWidth - state.minWidth,
        }),
        priceScale: () => ({ width: () => state.minWidth }),
        applyOptions: (options) => {
          state.minWidth = options.rightPriceScale.minimumWidth;
        },
      };
    });
    const result = syncChartGroupPriceScaleWidths(charts, {
      alignedWidth: aligned,
      flush: () => {},
    });
    aligned = result.alignedPriceScaleWidth;
    history.push(aligned);
  }
  assert.deepEqual(history, Array(12).fill(CHART_RIGHT_Y_AXIS_WIDTH));
});
