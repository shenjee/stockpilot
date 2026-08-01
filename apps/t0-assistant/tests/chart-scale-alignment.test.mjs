import assert from "node:assert/strict";
import test from "node:test";

import {
  COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH,
  DEFAULT_PRICE_SCALE_MIN_WIDTH,
  plotWidthsAligned,
  requiredPriceScaleMinimumWidth,
} from "../renderer/src/charts/chart-scale-alignment.mjs";

test("plotWidthsAligned accepts sub-pixel drift within tolerance", () => {
  assert.equal(plotWidthsAligned([800, 800.5, 799.6]), true);
  assert.equal(plotWidthsAligned([800, 802, 800]), false);
});

test("requiredPriceScaleMinimumWidth keeps compact-label floor", () => {
  assert.equal(
    requiredPriceScaleMinimumWidth([58, 58]),
    COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH,
  );
  assert.equal(
    requiredPriceScaleMinimumWidth([58, 58], DEFAULT_PRICE_SCALE_MIN_WIDTH),
    COMPACT_LABEL_PRICE_SCALE_MIN_WIDTH,
  );
  assert.equal(requiredPriceScaleMinimumWidth([90, 58]), 90);
});
