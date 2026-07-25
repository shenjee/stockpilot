import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCrosshairFallbackIndex,
  resolveCrosshairTarget,
} from "../renderer/src/charts/chart-interaction.mjs";

test("crosshair synchronization clears targets without a numeric value", () => {
  const values = new Map([[100, 1.25]]);

  assert.deepEqual(resolveCrosshairTarget(values, 100), {
    action: "position",
    value: 1.25,
  });
  assert.deepEqual(resolveCrosshairTarget(values, 101), {
    action: "clear",
  });
});

test("MACD crosshair falls back from DIF to DEA and histogram", () => {
  const index = buildCrosshairFallbackIndex(
    [
      [
        { timestamp: "09:31", value: null },
        { timestamp: "09:32", value: null },
      ],
      [
        { timestamp: "09:31", value: 0.12 },
        { timestamp: "09:32", value: null },
      ],
      [
        { timestamp: "09:31", value: 0.2 },
        { timestamp: "09:32", value: -0.08 },
      ],
    ],
    { "09:31": 100, "09:32": 101 },
  );

  assert.equal(index.values.get(100), 0.12);
  assert.equal(index.seriesIndexes.get(100), 1);
  assert.equal(index.values.get(101), -0.08);
  assert.equal(index.seriesIndexes.get(101), 2);
});
