import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  ChartGroupKind,
  createChartGroupModel,
  formatMarketTick,
  parseMarketTimestamp,
} from "../renderer/src/charts/chart-model.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  await readFile(
    resolve(testDir, "../contracts/fixtures/chart-groups-v1.json"),
    "utf8",
  ),
);

test("5 minute model consumes contract series and appends only dynamic volume", () => {
  const model = createChartGroupModel(
    fixture,
    ChartGroupKind.FIVE_MINUTE,
  );

  assert.equal(model.timestamps.length, 12);
  assert.equal(model.volume.length, 12);
  assert.deepEqual(model.volume.at(-1), {
    timestamp: "2026-07-22 10:10:00",
    value: 48000,
  });
  assert.deepEqual(
    model.macd.histogram.slice(0, 2),
    fixture.indicators.five_minute.macd.histogram.slice(0, 2),
  );
});

test("logical ordering has one slot per real bar across overnight gaps", () => {
  const model = createChartGroupModel(
    fixture,
    ChartGroupKind.FIVE_MINUTE,
  );
  const overnightLeft = model.timestamps.indexOf("2026-07-21 15:00:00");
  const overnightRight = model.timestamps.indexOf("2026-07-22 09:35:00");

  assert.equal(overnightRight - overnightLeft, 1);
  assert.ok(
    model.timeByTimestamp["2026-07-22 09:35:00"] -
      model.timeByTimestamp["2026-07-21 15:00:00"] >
      60 * 60 * 12,
  );
  assert.equal(
    formatMarketTick(
      model.timeByTimestamp["2026-07-22 09:35:00"],
      model.timeByTimestamp["2026-07-21 15:00:00"],
    ),
    "07-22",
  );
});

test("5 minute layer preferences control MA, stroke, and pivot model data", () => {
  const layered = structuredClone(fixture);
  layered.indicators.five_minute.ma = {
    ma5: [
      {
        timestamp: "2026-07-22 10:05:00",
        value: 10.2,
      },
    ],
    ma10: [],
    ma20: [],
    ma30: [],
    ma60: [],
  };
  layered.chan_analysis = {
    strokes: [
      {
        start_timestamp: "2026-07-22 09:55:00",
        end_timestamp: "2026-07-22 10:05:00",
        start_price: 10.1,
        end_price: 10.3,
        confirmed: true,
      },
    ],
    pivot_zones: [
      {
        start_timestamp: "2026-07-22 09:55:00",
        end_timestamp: "2026-07-22 10:05:00",
        high: 10.25,
        low: 10.15,
      },
    ],
  };

  const visible = createChartGroupModel(
    layered,
    ChartGroupKind.FIVE_MINUTE,
    { ma5: true, strokes: true, pivot_zones: true },
  );
  const hidden = createChartGroupModel(
    layered,
    ChartGroupKind.FIVE_MINUTE,
    { ma5: false, strokes: false, pivot_zones: false },
  );

  assert.equal(visible.movingAverages.ma5.length, 1);
  assert.equal(visible.strokes.length, 1);
  assert.equal(visible.pivotZones.length, 1);
  assert.equal(hidden.movingAverages.ma5.length, 0);
  assert.equal(hidden.strokes.length, 0);
  assert.equal(hidden.pivotZones.length, 0);
});

test("intraday model uses backend VWAP/MACD and keeps both sides of lunch", () => {
  const model = createChartGroupModel(
    fixture,
    ChartGroupKind.ONE_MINUTE,
  );
  const lunchLeft = model.timestamps.indexOf("2026-07-22 11:30:00");
  const lunchRight = model.timestamps.indexOf("2026-07-22 13:01:00");

  assert.equal(lunchRight - lunchLeft, 1);
  assert.deepEqual(model.vwap, fixture.indicators.one_minute.vwap);
  assert.deepEqual(model.macd.dif, fixture.indicators.one_minute.macd.dif);
  assert.equal(model.price.length, fixture.market.bars_1m.length);
});

test("model rejects out-of-order bars and indicator points without a bar", () => {
  const outOfOrder = structuredClone(fixture);
  outOfOrder.market.bars_1m.reverse();
  assert.throws(
    () => createChartGroupModel(outOfOrder, ChartGroupKind.ONE_MINUTE),
    /strictly ordered and unique/,
  );

  const unaligned = structuredClone(fixture);
  unaligned.indicators.one_minute.vwap.push({
    timestamp: "2026-07-22 13:05:00",
    value: 10.3,
  });
  assert.throws(
    () => createChartGroupModel(unaligned, ChartGroupKind.ONE_MINUTE),
    /without a matching bar/,
  );
});

test("timestamp parsing is timezone-independent and rejects loose input", () => {
  assert.equal(
    parseMarketTimestamp("2026-07-22 09:35:00"),
    Date.UTC(2026, 6, 22, 9, 35, 0) / 1000,
  );
  assert.throws(() => parseMarketTimestamp("2026/07/22 09:35"));
});
