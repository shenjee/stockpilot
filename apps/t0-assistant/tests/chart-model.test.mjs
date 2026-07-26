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

test("5 minute BOLL is consumed from contract and preserves null warmup", () => {
  const layered = structuredClone(fixture);
  layered.indicators.five_minute.boll = {
    period: 20,
    stddev: 2.0,
    upper: [
      { timestamp: "2026-07-22 10:05:00", value: 10.6 },
      { timestamp: "2026-07-22 10:10:00", value: 10.7 },
    ],
    middle: [
      { timestamp: "2026-07-22 10:05:00", value: null },
      { timestamp: "2026-07-22 10:10:00", value: 10.4 },
    ],
    lower: [
      { timestamp: "2026-07-22 10:05:00", value: 10.1 },
      { timestamp: "2026-07-22 10:10:00", value: 10.2 },
    ],
  };

  const model = createChartGroupModel(layered, ChartGroupKind.FIVE_MINUTE);
  assert.equal(model.boll.upper.length, 2);
  assert.equal(model.boll.middle.length, 2);
  assert.equal(model.boll.middle[0].value, null);
  assert.equal(model.boll.lower[1].value, 10.2);

  // 1 分钟组没有 BOLL 图层。
  const intraday = createChartGroupModel(layered, ChartGroupKind.ONE_MINUTE);
  assert.deepEqual(intraday.boll, { upper: [], middle: [], lower: [] });
});

test("CZSC candidate points map to 1B/1S/2B/2S/3B/3S and preserve price", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    candidate_buy_points: [
      { point_type: "first_buy", timestamp: "2026-07-22 09:55:00", price: 10.1 },
      { point_type: "second_buy", timestamp: "2026-07-22 09:55:00", price: 10.1 },
      { point_type: "third_buy", timestamp: "2026-07-22 10:05:00", price: 10.3 },
      { point_type: "structure_buy_candidate", timestamp: "2026-07-22 10:10:00", price: 10.4 },
    ],
    candidate_sell_points: [
      { point_type: "first_sell", timestamp: "2026-07-22 10:00:00", price: 10.5 },
      { point_type: "unknown_type", timestamp: "2026-07-22 10:05:00", price: 10.3 },
    ],
  };

  const model = createChartGroupModel(layered, ChartGroupKind.FIVE_MINUTE);
  // 09:55 同时 1B + 2B 同价合并；10:05 只有 3B（unknown_type 卖点被忽略）；
  // 10:10 的 structure_buy_candidate 不渲染。每个标记保留契约价格。
  assert.deepEqual(model.czscMarkers, [
    { timestamp: "2026-07-22 09:55:00", side: "buy", price: 10.1, label: "1B, 2B" },
    { timestamp: "2026-07-22 10:00:00", side: "sell", price: 10.5, label: "1S" },
    { timestamp: "2026-07-22 10:05:00", side: "buy", price: 10.3, label: "3B" },
  ]);
});

test("CZSC markers at the same time but different prices are not merged", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    candidate_buy_points: [
      { point_type: "first_buy", timestamp: "2026-07-22 09:55:00", price: 10.1 },
      { point_type: "third_buy", timestamp: "2026-07-22 09:55:00", price: 10.4 },
    ],
    candidate_sell_points: [],
  };

  const model = createChartGroupModel(layered, ChartGroupKind.FIVE_MINUTE);
  // 同一时刻、同侧、不同价格 -> 两个独立标记，按价格升序。
  assert.deepEqual(model.czscMarkers, [
    { timestamp: "2026-07-22 09:55:00", side: "buy", price: 10.1, label: "1B" },
    { timestamp: "2026-07-22 09:55:00", side: "buy", price: 10.4, label: "3B" },
  ]);
});

test("CZSC markers with invalid or missing price are dropped", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    candidate_buy_points: [
      { point_type: "first_buy", timestamp: "2026-07-22 09:55:00" }, // 缺 price
      { point_type: "second_buy", timestamp: "2026-07-22 10:00:00", price: NaN },
      { point_type: "third_buy", timestamp: "2026-07-22 10:05:00", price: "10.3" }, // 非数字
      { point_type: "first_buy", timestamp: "2026-07-22 10:10:00", price: Infinity },
    ],
    candidate_sell_points: [],
  };

  const model = createChartGroupModel(layered, ChartGroupKind.FIVE_MINUTE);
  // 非法/缺失价格不产生标记。
  assert.deepEqual(model.czscMarkers, []);
});

test("CZSC markers and BOLL are absent for the intraday group", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    candidate_buy_points: [
      { point_type: "first_buy", timestamp: "2026-07-22 09:55:00", price: 10.1 },
    ],
  };
  const model = createChartGroupModel(layered, ChartGroupKind.ONE_MINUTE);
  assert.deepEqual(model.czscMarkers, []);
});

test("pivot zone active flag is preserved through normalization", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    pivot_zones: [
      {
        start_timestamp: "2026-07-22 09:55:00",
        end_timestamp: "2026-07-22 10:05:00",
        high: 10.25,
        low: 10.15,
        active: true,
      },
      {
        start_timestamp: "2026-07-22 09:55:00",
        end_timestamp: "2026-07-22 10:05:00",
        high: 10.3,
        low: 10.1,
        active: false,
      },
    ],
  };
  const model = createChartGroupModel(
    layered,
    ChartGroupKind.FIVE_MINUTE,
    { pivot_zones: true },
  );
  assert.equal(model.pivotZones.length, 2);
  assert.equal(model.pivotZones[0].active, true);
  assert.equal(model.pivotZones[1].active, false);
});

test("replay asOf truncation drops bars, indicators, and CZSC layers after current_time", () => {
  const replay = structuredClone(fixture);
  replay.replay = {
    granularity: "five_minute",
    current_time: "2026-07-22 10:00:00",
    next_bar_time: "2026-07-22 10:05:00",
    start_time: "2026-07-22 09:30:00",
    end_time: "2026-07-22 15:00:00",
    playing: false,
    playback_speed: 1,
    step_seconds: 300,
  };
  replay.chan_analysis = {
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
        active: true,
      },
    ],
    candidate_buy_points: [
      { point_type: "first_buy", timestamp: "2026-07-22 10:05:00", price: 10.3 },
    ],
  };

  const model = createChartGroupModel(replay, ChartGroupKind.FIVE_MINUTE);
  // 10:05 / 10:10 bars dropped；10:00 保留为右边界。
  assert.equal(model.timestamps.at(-1), "2026-07-22 10:00:00");
  // 笔/中枢/买卖点 end 或 timestamp 越过 current_time -> 丢弃。
  assert.equal(model.strokes.length, 0);
  assert.equal(model.pivotZones.length, 0);
  assert.equal(model.czscMarkers.length, 0);
  // MACD/Volume 指标在 10:05 的点也被截断，不抛错。
  assert.equal(
    model.macd.histogram.at(-1)?.timestamp ?? null,
    "2026-07-22 10:00:00",
  );
});
