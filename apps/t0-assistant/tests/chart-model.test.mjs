import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  ChartGroupKind,
  PRICE_AXIS_FINE_MIN_MOVE,
  PRICE_AXIS_INTEGER_MIN_MOVE,
  PRICE_EXACT_PRICE_FORMAT,
  createChartGroupModel,
  createPriceExactPriceFormat,
  formatMarketTick,
  formatPriceAxisTickLabel,
  formatPriceAxisTickLabels,
  formatPriceExactLabel,
  formatVolumeAxisLabel,
  parseMarketTimestamp,
  resolvePriceAxisMinMove,
  roundHalfAwayFromZero,
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

test("fixture chan_analysis is complete and rendered in 5 minute model", () => {
  assert.ok(
    fixture.chan_analysis,
    "fixture must include chan_analysis (issue #134)",
  );
  assert.ok(
    Array.isArray(fixture.chan_analysis.strokes) &&
      fixture.chan_analysis.strokes.length > 0,
    "fixture chan_analysis.strokes must be non-empty",
  );
  assert.ok(
    Array.isArray(fixture.chan_analysis.pivot_zones) &&
      fixture.chan_analysis.pivot_zones.length > 0,
    "fixture chan_analysis.pivot_zones must be non-empty",
  );
  assert.ok(
    Array.isArray(fixture.chan_analysis.candidate_buy_points) &&
      fixture.chan_analysis.candidate_buy_points.length > 0,
    "fixture chan_analysis.candidate_buy_points must be non-empty",
  );
  assert.ok(
    Array.isArray(fixture.chan_analysis.candidate_sell_points) &&
      fixture.chan_analysis.candidate_sell_points.length > 0,
    "fixture chan_analysis.candidate_sell_points must be non-empty",
  );
  // CandidatePoint 契约校验（issue #135 code review）
  for (const point of fixture.chan_analysis.candidate_buy_points) {
    assert.ok(point.id, "candidate_buy_point must have id");
    assert.ok(point.reference_id, "candidate_buy_point must have reference_id");
    assert.equal(typeof point.confirmed, "boolean", "candidate_buy_point.confirmed must be boolean");
    assert.ok(point.reason, "candidate_buy_point must have reason");
  }
  for (const point of fixture.chan_analysis.candidate_sell_points) {
    assert.ok(point.id, "candidate_sell_point must have id");
    assert.ok(point.reference_id, "candidate_sell_point must have reference_id");
    assert.equal(typeof point.confirmed, "boolean", "candidate_sell_point.confirmed must be boolean");
    assert.ok(point.reason, "candidate_sell_point must have reason");
  }

  const model = createChartGroupModel(
    fixture,
    ChartGroupKind.FIVE_MINUTE,
    { strokes: true, pivot_zones: true },
  );
  assert.equal(model.strokes.length, fixture.chan_analysis.strokes.length);
  assert.equal(
    model.pivotZones.length,
    fixture.chan_analysis.pivot_zones.length,
  );
  // 分别验证买点和卖点均存在（issue #135 code review）
  const buyMarkers = model.czscMarkers.filter((m) => m.side === "buy");
  const sellMarkers = model.czscMarkers.filter((m) => m.side === "sell");
  assert.ok(buyMarkers.length > 0, "must have at least one buy marker");
  assert.ok(
    buyMarkers.some((m) => m.label.includes("1B")),
    "buy markers must include 1B",
  );
  assert.ok(sellMarkers.length > 0, "must have at least one sell marker");
  assert.ok(
    sellMarkers.some((m) => m.label.includes("1S")),
    "sell markers must include 1S",
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

test("volume axis labels use compact Chinese and English units", () => {
  assert.equal(formatVolumeAxisLabel(0), "0");
  assert.equal(formatVolumeAxisLabel(4_000_000), "400万");
  assert.equal(formatVolumeAxisLabel(12_345_678), "1234.57万");
  assert.equal(formatVolumeAxisLabel(250_000_000), "2.5亿");
  assert.equal(formatVolumeAxisLabel(9_999), "9999");
  assert.equal(formatVolumeAxisLabel(10_000), "1万");
  assert.equal(formatVolumeAxisLabel(99_999_999), "1亿");
  assert.equal(formatVolumeAxisLabel(4_000_000, "en-US"), "4M");
  assert.equal(formatVolumeAxisLabel(1_500, "en-US"), "1.5K");
  assert.equal(formatVolumeAxisLabel(999_999_999, "en-US"), "1B");
  assert.equal(formatVolumeAxisLabel(-100), "");
  assert.equal(formatVolumeAxisLabel(null), "");
  assert.equal(formatVolumeAxisLabel(Number.NaN), "");
});

test("price axis tick labels round before applying display branches", () => {
  assert.equal(formatPriceAxisTickLabel(99.994), "99.99");
  assert.equal(formatPriceAxisTickLabel(99.996), "100");
  assert.equal(formatPriceAxisTickLabel(-99.995), "-100");
  assert.equal(formatPriceAxisTickLabel(100.4), "100");
  assert.equal(formatPriceAxisTickLabel(100.6), "101");
  assert.equal(formatPriceAxisTickLabel(100.5), "101");
  assert.equal(formatPriceAxisTickLabel(-100.5), "-101");
  assert.equal(formatPriceAxisTickLabel(-100.6), "-101");
  assert.equal(formatPriceAxisTickLabel(12.34), "12.34");
  assert.equal(formatPriceAxisTickLabel(-5.2), "-5.20");
});

test("price exact labels always keep two decimal places", () => {
  assert.equal(formatPriceExactLabel(949.91), "949.91");
  assert.equal(formatPriceExactLabel(100), "100.00");
  assert.equal(formatPriceExactLabel(-12.3), "-12.30");
  assert.equal(formatPriceExactLabel(99.996), "100.00");
});

test("PRICE_EXACT_PRICE_FORMAT separates exact and tickmark formatters", () => {
  assert.equal(PRICE_EXACT_PRICE_FORMAT.formatter(100), "100.00");
  assert.deepEqual(PRICE_EXACT_PRICE_FORMAT.tickmarksFormatter([100, 99.5]), [
    "100",
    "99.50",
  ]);
  assert.deepEqual(formatPriceAxisTickLabels([100.6, -5.2]), ["101", "-5.20"]);
});

test("roundHalfAwayFromZero avoids binary float boundary drift", () => {
  assert.equal(roundHalfAwayFromZero(99.994, 2), 99.99);
  assert.equal(roundHalfAwayFromZero(99.996, 2), 100);
  assert.equal(roundHalfAwayFromZero(-99.995, 2), -100);
  assert.equal(roundHalfAwayFromZero(1.005, 2), 1.01);
  assert.equal(roundHalfAwayFromZero(-1.005, 2), -1.01);
  assert.equal(formatPriceExactLabel(1.005), "1.01");
  assert.equal(formatPriceExactLabel(-1.005), "-1.01");
});

test("roundHalfAwayFromZero handles scientific-notation magnitudes", () => {
  assert.equal(roundHalfAwayFromZero(1e-7, 2), 0);
  assert.equal(roundHalfAwayFromZero(-1e-7, 2), 0);
  assert.equal(roundHalfAwayFromZero(9e-7, 2), 0);
  assert.equal(roundHalfAwayFromZero(-9e-7, 2), 0);
  assert.equal(formatPriceExactLabel(1e-7), "0.00");
  assert.equal(formatPriceExactLabel(-1e-7), "0.00");
  assert.equal(formatPriceExactLabel(9e-7), "0.00");
  assert.equal(formatPriceExactLabel(-9e-7), "0.00");
  assert.equal(roundHalfAwayFromZero(1e21, 2), 1e21);
  assert.equal(roundHalfAwayFromZero(-1e21, 2), -1e21);
});

test("resolvePriceAxisMinMove forces integer ticks when abs range reaches 100", () => {
  assert.equal(resolvePriceAxisMinMove(12, 34), PRICE_AXIS_FINE_MIN_MOVE);
  assert.equal(resolvePriceAxisMinMove(99.5, 99.9), PRICE_AXIS_FINE_MIN_MOVE);
  assert.equal(resolvePriceAxisMinMove(100, 120), PRICE_AXIS_INTEGER_MIN_MOVE);
  assert.equal(resolvePriceAxisMinMove(-150, -80), PRICE_AXIS_INTEGER_MIN_MOVE);
  assert.equal(
    createPriceExactPriceFormat(PRICE_AXIS_INTEGER_MIN_MOVE).minMove,
    1,
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
  const afternoonOpen = model.timestamps.indexOf("2026-07-22 13:00:00");
  const lunchRight = model.timestamps.indexOf("2026-07-22 13:01:00");

  assert.equal(model.timestamps[0], "2026-07-22 09:30:00");
  assert.equal(model.timestamps.at(-1), "2026-07-22 15:00:00");
  assert.equal(model.timestamps.length, 242);
  assert.equal(afternoonOpen - lunchLeft, 1);
  assert.equal(lunchRight - afternoonOpen, 1);
  assert.equal(
    model.vwap.find((point) => point.timestamp === "2026-07-22 13:01:00")
      .value,
    fixture.indicators.one_minute.vwap.find(
      (point) => point.timestamp === "2026-07-22 13:01:00",
    ).value,
  );
  assert.equal(model.vwap.at(-1).value, null);
  assert.equal(model.macd.dif.at(-1).value, null);
  assert.equal(model.price.length, 242);
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

test("CZSC candidate points map standard and structural labels while preserving price", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    candidate_buy_points: [
      { id: "bp-001", point_type: "first_buy", timestamp: "2026-07-22 09:55:00", price: 10.1, reference_id: "s1", confirmed: true, reason: "test" },
      { id: "bp-002", point_type: "second_buy", timestamp: "2026-07-22 09:55:00", price: 10.1, reference_id: "s1", confirmed: true, reason: "test" },
      { id: "bp-003", point_type: "third_buy", timestamp: "2026-07-22 10:05:00", price: 10.3, reference_id: "s2", confirmed: false, reason: "test" },
      { id: "bp-004", point_type: "structure_buy_candidate", timestamp: "2026-07-22 10:10:00", price: 10.4, reference_id: "s2", confirmed: false, reason: "test" },
    ],
    candidate_sell_points: [
      { id: "sp-001", point_type: "first_sell", timestamp: "2026-07-22 10:00:00", price: 10.5, reference_id: "s3", confirmed: true, reason: "test" },
      { id: "sp-002", point_type: "unknown_type", timestamp: "2026-07-22 10:05:00", price: 10.3, reference_id: "s3", confirmed: false, reason: "test" },
      { id: "sp-003", point_type: "structure_sell_candidate", timestamp: "2026-07-22 10:10:00", price: 10.6, reference_id: "s4", confirmed: false, reason: "test" },
    ],
  };

  const model = createChartGroupModel(layered, ChartGroupKind.FIVE_MINUTE);
  // 09:55 同时 1B + 2B 同价合并；10:05 只有 3B（unknown_type 卖点被忽略）；
  // 10:10 的结构候选点与 Chan Viewer 一致显示 Buy?/Sell?，并保留各自价格。
  assert.deepEqual(model.czscMarkers, [
    { timestamp: "2026-07-22 09:55:00", side: "buy", price: 10.1, label: "1B, 2B" },
    { timestamp: "2026-07-22 10:00:00", side: "sell", price: 10.5, label: "1S" },
    { timestamp: "2026-07-22 10:05:00", side: "buy", price: 10.3, label: "3B" },
    { timestamp: "2026-07-22 10:10:00", side: "buy", price: 10.4, label: "Buy?" },
    { timestamp: "2026-07-22 10:10:00", side: "sell", price: 10.6, label: "Sell?" },
  ]);
});

test("CZSC markers at the same time but different prices are not merged", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    candidate_buy_points: [
      { id: "bp-001", point_type: "first_buy", timestamp: "2026-07-22 09:55:00", price: 10.1, reference_id: "s1", confirmed: true, reason: "test" },
      { id: "bp-002", point_type: "third_buy", timestamp: "2026-07-22 09:55:00", price: 10.4, reference_id: "s1", confirmed: false, reason: "test" },
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
      { id: "bp-001", point_type: "first_buy", timestamp: "2026-07-22 09:55:00", reference_id: "s1", confirmed: true, reason: "test" }, // 缺 price
      { id: "bp-002", point_type: "second_buy", timestamp: "2026-07-22 10:00:00", price: NaN, reference_id: "s1", confirmed: true, reason: "test" },
      { id: "bp-003", point_type: "third_buy", timestamp: "2026-07-22 10:05:00", price: "10.3", reference_id: "s2", confirmed: false, reason: "test" }, // 非数字
      { id: "bp-004", point_type: "first_buy", timestamp: "2026-07-22 10:10:00", price: Infinity, reference_id: "s2", confirmed: false, reason: "test" },
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
      { id: "bp-001", point_type: "first_buy", timestamp: "2026-07-22 09:55:00", price: 10.1, reference_id: "s1", confirmed: true, reason: "test" },
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
      { id: "bp-001", point_type: "first_buy", timestamp: "2026-07-22 10:05:00", price: 10.3, reference_id: "s1", confirmed: true, reason: "test" },
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
