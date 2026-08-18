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
  calculateIntradayPriceRange,
  calculateIntradayPriceTicks,
  computeValidPriceBase,
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
  assert.equal(model.strokes[0].dashed, false);
  assert.equal(model.strokes.at(-1).dashed, true);
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
  assert.ok(
    Array.isArray(fixture.chan_analysis.divergences) &&
      fixture.chan_analysis.divergences.length > 0,
    "fixture chan_analysis.divergences must be non-empty",
  );
  assert.equal(
    model.divergenceMarkers.length,
    fixture.chan_analysis.divergences.length,
  );
  assert.ok(
    model.divergenceMarkers.some(
      (m) => m.label === "Bull Div" && m.divergenceType === "bullish",
    ),
    "must have Bull Div marker",
  );
  assert.ok(
    model.divergenceMarkers.some(
      (m) => m.label === "Bear Div" && m.divergenceType === "bearish",
    ),
    "must have Bear Div marker",
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

// ---------------------------------------------------------------------------
// computeValidPriceBase — LC PriceTickSpanCalculator only accepts bases whose
// prime factors are 2 and/or 5 (issue #143 / 300133 unexpected-base crash).
// ---------------------------------------------------------------------------

function assertBaseFactorableBy2And5(base) {
  assert.ok(Number.isInteger(base) && base >= 1, `base must be positive int: ${base}`);
  let rest = base;
  while (rest > 1) {
    if (rest % 2 === 0) {
      rest = Math.floor(rest / 2);
    } else if (rest % 5 === 0) {
      rest = Math.floor(rest / 5);
    } else {
      assert.fail(`base ${base} has non-2/5 factor (rest=${rest})`);
    }
  }
}

test("computeValidPriceBase keeps 2/5-only bases and snaps invalid ones", () => {
  assert.equal(computeValidPriceBase(0.01), 100);
  assert.equal(computeValidPriceBase(1), 1);
  assert.equal(computeValidPriceBase(0.25), 4);
  assert.equal(computeValidPriceBase(0.2), 5);
  // 300133-like: Math.round(1/0.08)=13 → snap to nearest power of 10.
  assert.equal(Math.round(1 / 0.08), 13);
  assert.equal(computeValidPriceBase(0.08), 10);
  assert.equal(computeValidPriceBase(1.725), 1);
  assert.equal(computeValidPriceBase(0.03), 100);
  assertBaseFactorableBy2And5(computeValidPriceBase(0.08));
  assertBaseFactorableBy2And5(computeValidPriceBase(0.03));
});

test("computeValidPriceBase falls back for non-positive / non-finite minMove", () => {
  assert.equal(computeValidPriceBase(NaN), 100);
  assert.equal(computeValidPriceBase(0), 100);
  assert.equal(computeValidPriceBase(-0.01), 100);
  assert.equal(computeValidPriceBase(Infinity), 100);
});

test("createPriceExactPriceFormat includes a valid LC base alongside minMove", () => {
  const fine = createPriceExactPriceFormat(PRICE_AXIS_FINE_MIN_MOVE);
  assert.equal(fine.minMove, 0.01);
  assert.equal(fine.base, 100);
  const crashy = createPriceExactPriceFormat(0.08);
  assert.equal(crashy.minMove, 0.08);
  assert.equal(crashy.base, 10);
  assertBaseFactorableBy2And5(crashy.base);
  assert.equal(PRICE_EXACT_PRICE_FORMAT.base, 100);
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

test("5 minute stroke layer draws pending tail as dashed like chan-viewer", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    strokes: [
      {
        start_timestamp: "2026-07-22 09:35:00",
        end_timestamp: "2026-07-22 09:50:00",
        start_price: 10.16,
        end_price: 10.18,
        confirmed: true,
      },
    ],
    meta: {
      pending_stroke: {
        id: "stroke_pending_2026-07-22 09:50:00_2026-07-22 10:05:00",
        direction: "up",
        start_timestamp: "2026-07-22 09:50:00",
        end_timestamp: "2026-07-22 10:05:00",
        start_price: 10.18,
        end_price: 10.3,
        confirmed: false,
        meta: { pending: true, source: "czsc_ubi" },
      },
    },
  };

  const model = createChartGroupModel(
    layered,
    ChartGroupKind.FIVE_MINUTE,
    { strokes: true },
  );
  assert.equal(model.strokes.length, 2);
  assert.deepEqual(model.strokes[0], {
    start: { timestamp: "2026-07-22 09:35:00", value: 10.16 },
    end: { timestamp: "2026-07-22 09:50:00", value: 10.18 },
    color: "#2563eb",
    dashed: false,
  });
  assert.deepEqual(model.strokes[1], {
    start: { timestamp: "2026-07-22 09:50:00", value: 10.18 },
    end: { timestamp: "2026-07-22 10:05:00", value: 10.3 },
    color: "#2563eb",
    dashed: true,
  });

  const hidden = createChartGroupModel(
    layered,
    ChartGroupKind.FIVE_MINUTE,
    { strokes: false },
  );
  assert.equal(hidden.strokes.length, 0);
});

test("replay asOf truncation also drops pending stroke past current_time", () => {
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
        start_timestamp: "2026-07-22 09:35:00",
        end_timestamp: "2026-07-22 09:50:00",
        start_price: 10.16,
        end_price: 10.18,
        confirmed: true,
      },
    ],
    meta: {
      pending_stroke: {
        start_timestamp: "2026-07-22 09:50:00",
        end_timestamp: "2026-07-22 10:05:00",
        start_price: 10.18,
        end_price: 10.3,
        confirmed: false,
        meta: { pending: true },
      },
    },
  };

  const model = createChartGroupModel(replay, ChartGroupKind.FIVE_MINUTE, {
    strokes: true,
  });
  assert.equal(model.timestamps.at(-1), "2026-07-22 10:00:00");
  assert.equal(model.strokes.length, 1);
  assert.equal(model.strokes[0].dashed, false);
  assert.equal(model.strokes[0].end.timestamp, "2026-07-22 09:50:00");
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
  assert.deepEqual(model.divergenceMarkers, []);
});

test("divergence markers map Bull Div and Bear Div from chan_analysis", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    divergences: [
      {
        id: "div-bull",
        divergence_type: "bullish",
        reference_type: "stroke",
        reference_id: "s1",
        timestamp: "2026-07-22 09:55:00",
        strength: "normal",
        confirmed: true,
        description: "bull",
        meta: { price: 10.17 },
      },
      {
        id: "div-bear",
        divergence_type: "bearish",
        reference_type: "stroke",
        reference_id: "s2",
        timestamp: "2026-07-22 10:00:00",
        strength: "strong",
        confirmed: true,
        description: "bear",
        meta: { price: 10.3 },
      },
      {
        id: "div-skip-type",
        divergence_type: "unknown",
        reference_type: "stroke",
        reference_id: "s3",
        timestamp: "2026-07-22 10:05:00",
        strength: "normal",
        confirmed: true,
        description: "skip",
        meta: { price: 10.2 },
      },
      {
        id: "div-skip-price",
        divergence_type: "bullish",
        reference_type: "stroke",
        reference_id: "s4",
        timestamp: "2026-07-22 10:10:00",
        strength: "normal",
        confirmed: true,
        description: "skip",
        meta: {},
      },
    ],
  };

  const model = createChartGroupModel(layered, ChartGroupKind.FIVE_MINUTE);
  assert.deepEqual(model.divergenceMarkers, [
    {
      timestamp: "2026-07-22 09:55:00",
      side: "buy",
      price: 10.17,
      label: "Bull Div",
      divergenceType: "bullish",
    },
    {
      timestamp: "2026-07-22 10:00:00",
      side: "sell",
      price: 10.3,
      label: "Bear Div",
      divergenceType: "bearish",
    },
  ]);
});

test("pivot zone layer keeps stroke zones and drops segment zones", () => {
  const layered = structuredClone(fixture);
  layered.chan_analysis = {
    pivot_zones: [
      {
        start_timestamp: "2026-07-22 09:55:00",
        end_timestamp: "2026-07-22 10:05:00",
        high: 10.25,
        low: 10.15,
        active: true,
        level: "stroke",
      },
      {
        start_timestamp: "2026-07-22 09:55:00",
        end_timestamp: "2026-07-22 10:05:00",
        high: 10.4,
        low: 10.05,
        active: false,
        level: "segment",
      },
      {
        start_timestamp: "2026-07-22 09:55:00",
        end_timestamp: "2026-07-22 10:05:00",
        high: 10.22,
        low: 10.16,
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
  assert.deepEqual(model.pivotZones, [
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
      high: 10.22,
      low: 10.16,
      active: false,
    },
  ]);
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
    divergences: [
      {
        id: "div-future",
        divergence_type: "bearish",
        reference_type: "stroke",
        reference_id: "s1",
        timestamp: "2026-07-22 10:05:00",
        strength: "normal",
        confirmed: true,
        description: "future",
        meta: { price: 10.3 },
      },
    ],
  };

  const model = createChartGroupModel(replay, ChartGroupKind.FIVE_MINUTE);
  // 10:05 / 10:10 bars dropped；10:00 保留为右边界。
  assert.equal(model.timestamps.at(-1), "2026-07-22 10:00:00");
  // 笔/中枢/买卖点/背驰 end 或 timestamp 越过 current_time -> 丢弃。
  assert.equal(model.strokes.length, 0);
  assert.equal(model.pivotZones.length, 0);
  assert.equal(model.czscMarkers.length, 0);
  assert.equal(model.divergenceMarkers.length, 0);
  // MACD/Volume 指标在 10:05 的点也被截断，不抛错。
  assert.equal(
    model.macd.histogram.at(-1)?.timestamp ?? null,
    "2026-07-22 10:00:00",
  );
});

// ---------------------------------------------------------------------------
// calculateIntradayPriceRange — spec §6.2.1 / issue #143
// ---------------------------------------------------------------------------

test("intraday price range returns null for invalid previousClose", () => {
  const bars = [{ open: 10, high: 11, low: 9 }];
  assert.equal(calculateIntradayPriceRange(null, bars), null);
  assert.equal(calculateIntradayPriceRange(undefined, bars), null);
  assert.equal(calculateIntradayPriceRange(NaN, bars), null);
  assert.equal(calculateIntradayPriceRange(0, bars), null);
  assert.equal(calculateIntradayPriceRange(-1, bars), null);
  assert.equal(calculateIntradayPriceRange(Infinity, bars), null);
});

test("intraday price range uses ±1% when no valid bars", () => {
  const result = calculateIntradayPriceRange(100, []);
  assert.ok(result);
  assert.equal(result.P0, 100);
  assert.equal(result.R, 0.01);
  assert.equal(result.yMin, 99);
  assert.equal(result.yMax, 101);
});

test("intraday price range uses ±1% when bars are empty or invalid", () => {
  assert.ok(calculateIntradayPriceRange(100, null));
  assert.ok(calculateIntradayPriceRange(100, undefined));
  const withNaN = calculateIntradayPriceRange(100, [
    { open: NaN, high: NaN, low: NaN },
  ]);
  assert.ok(withNaN);
  assert.equal(withNaN.R, 0.01);
});

test("intraday price range is symmetric around P0 with upward-dominant deviation", () => {
  // P0=100, H=108 (8% up), L=99 (1% down) → R = 8%, mirror to 92
  const result = calculateIntradayPriceRange(100, [
    { open: 100, high: 108, low: 99 },
  ]);
  assert.ok(result);
  assert.equal(result.P0, 100);
  assert.ok(Math.abs(result.R - 0.08) < 1e-9);
  assert.ok(Math.abs(result.yMax - 108) < 1e-9);
  assert.ok(Math.abs(result.yMin - 92) < 1e-9);
  // P0 at vertical centre
  assert.ok(Math.abs((result.yMax + result.yMin) / 2 - 100) < 1e-9);
});

test("intraday price range is symmetric around P0 with downward-dominant deviation", () => {
  // P0=100, H=101 (1% up), L=90 (10% down) → R = 10%, mirror to 110
  const result = calculateIntradayPriceRange(100, [
    { open: 100, high: 101, low: 90 },
  ]);
  assert.ok(result);
  assert.ok(Math.abs(result.R - 0.1) < 1e-9);
  assert.ok(Math.abs(result.yMax - 110) < 1e-9);
  assert.ok(Math.abs(result.yMin - 90) < 1e-9);
});

test("intraday price range respects initial_range floor from gap open", () => {
  // P0=100, O=105 (5% gap), H=103, L=99 → observed=3%, initial=5%, R=5%
  const result = calculateIntradayPriceRange(100, [
    { open: 105, high: 103, low: 99 },
  ]);
  assert.ok(result);
  assert.ok(Math.abs(result.R - 0.05) < 1e-9);
  assert.ok(Math.abs(result.yMax - 105) < 1e-9);
  assert.ok(Math.abs(result.yMin - 95) < 1e-9);
});

test("intraday price range uses ±1% minimum when open equals P0", () => {
  // P0=100, O=100, H=100.5, L=99.8 → observed=0.5%, initial=1%, R=1%
  const result = calculateIntradayPriceRange(100, [
    { open: 100, high: 100.5, low: 99.8 },
  ]);
  assert.ok(result);
  assert.equal(result.R, 0.01);
});

test("intraday price range only expands as more bars arrive (live)", () => {
  const P0 = 100;
  // Prefix 1: H=102, L=99 → R = 2%
  const r1 = calculateIntradayPriceRange(P0, [
    { open: 100, high: 102, low: 99 },
  ]);
  assert.ok(Math.abs(r1.R - 0.02) < 1e-9);

  // Prefix 2: H=102, L=98 → R = 2% (still 2%, up-side dominant, only expands)
  const r2 = calculateIntradayPriceRange(P0, [
    { open: 100, high: 102, low: 99 },
    { open: 101, high: 102, low: 98 },
  ]);
  assert.ok(Math.abs(r2.R - 0.02) < 1e-9);

  // Prefix 3: H=105 → R = 5% (expanded)
  const r3 = calculateIntradayPriceRange(P0, [
    { open: 100, high: 102, low: 99 },
    { open: 101, high: 102, low: 98 },
    { open: 103, high: 105, low: 103 },
  ]);
  assert.ok(Math.abs(r3.R - 0.05) < 1e-9);
});

test("intraday price range deterministically recomputes on replay cursor movement", () => {
  const P0 = 100;
  const allBars = [
    { open: 100, high: 108, low: 99 },
    { open: 107, high: 109, low: 106 },
    { open: 108, high: 110, low: 107 },
  ];

  // Forward to cursor 2: H=109, L=99 → R = 9%
  const forward = calculateIntradayPriceRange(P0, allBars.slice(0, 2));
  assert.ok(Math.abs(forward.R - 0.09) < 1e-9);

  // Backward to cursor 1: H=108, L=99 → R = 8% (shrinks, no state retained)
  const backward = calculateIntradayPriceRange(P0, allBars.slice(0, 1));
  assert.ok(Math.abs(backward.R - 0.08) < 1e-9);

  // Forward again to cursor 2: same R as before (deterministic)
  const forwardAgain = calculateIntradayPriceRange(P0, allBars.slice(0, 2));
  assert.ok(Math.abs(forwardAgain.R - forward.R) < 1e-9);
});

test("intraday price range matches issue #143 example: 600584", () => {
  // P0=65.41, H=70.59 → R ≈ 7.92%, yMax=70.59, yMin ≈ 60.23
  const result = calculateIntradayPriceRange(65.41, [
    { open: 65.41, high: 70.59, low: 65.00 },
  ]);
  assert.ok(result);
  assert.ok(Math.abs(result.R - (70.59 / 65.41 - 1)) < 1e-9);
  assert.ok(Math.abs(result.yMax - 70.59) < 1e-6);
  assert.ok(Math.abs(result.yMin - 65.41 * (1 - result.R)) < 1e-6);
  // P0 at centre
  assert.ok(Math.abs((result.yMax + result.yMin) / 2 - 65.41) < 1e-6);
});

test("intraday model exposes previousClose from snapshot quote", () => {
  const model = createChartGroupModel(fixture, ChartGroupKind.ONE_MINUTE);
  assert.equal(model.previousClose, 10.10);
});

test("intraday model previousClose is null when quote is absent", () => {
  const noQuote = structuredClone(fixture);
  delete noQuote.market.quote;
  const model = createChartGroupModel(noQuote, ChartGroupKind.ONE_MINUTE);
  assert.equal(model.previousClose, null);
});

test("intraday model previousClose is null when previous_close is non-finite", () => {
  const badQuote = structuredClone(fixture);
  badQuote.market.quote.previous_close = "not-a-number";
  const model = createChartGroupModel(badQuote, ChartGroupKind.ONE_MINUTE);
  assert.equal(model.previousClose, null);
});

test("intraday model range from fixture bars matches P0-centred mirror", () => {
  // Fixture: P0=10.10, H=10.35, L=10.23, O=10.24
  const model = createChartGroupModel(fixture, ChartGroupKind.ONE_MINUTE);
  const result = calculateIntradayPriceRange(model.previousClose, model.bars);
  assert.ok(result);
  // Up-side dominant: H/P0-1 = 10.35/10.10-1 ≈ 2.475%
  // Down-side: 1-L/P0 = 1-10.23/10.10 ≈ negative → 0
  // initial_range = max(1%, |10.24/10.10-1|) = max(1%, 1.386%) = 1.386%
  // R = max(1.386%, 2.475%) = 2.475%
  const expectedR = 10.35 / 10.10 - 1;
  assert.ok(Math.abs(result.R - expectedR) < 1e-9);
  assert.ok(Math.abs(result.yMax - 10.35) < 1e-6);
  // P0 at centre
  assert.ok(Math.abs((result.yMax + result.yMin) / 2 - 10.10) < 1e-6);
});
// calculateIntradayPriceTicks + tickStep - spec §6.2.1
// 中轴到上下沿各四等分，刻度比例为:
// +R, +3R/4, +R/2, +R/4, 0, -R/4, -R/2, -3R/4, -R

test("intraday price range exposes tickStep = R*P0/4", () => {
  const P0 = 65.41;
  const result = calculateIntradayPriceRange(P0, [
    { open: 65.5, high: 70.59, low: 65.2 },
  ]);
  assert.ok(result);
  const expectedTickStep = (result.R * P0) / 4;
  assert.ok(Math.abs(result.tickStep - expectedTickStep) < 1e-9);
});

test("intraday price range tickStep uses ±1% floor when no bars", () => {
  const result = calculateIntradayPriceRange(100, []);
  assert.ok(result);
  // R = 1%, tickStep = 0.01 * 100 / 4 = 0.25
  assert.ok(Math.abs(result.tickStep - 0.25) < 1e-9);
});

test("calculateIntradayPriceTicks returns 9 ticks at quarter-range ratios", () => {
  const P0 = 100;
  const R = 0.08;
  const ticks = calculateIntradayPriceTicks(P0, R);
  assert.ok(ticks);
  assert.equal(ticks.length, 9);
  // Ratios: +R, +3R/4, +R/2, +R/4, 0, -R/4, -R/2, -3R/4, -R
  const expected = [
    100 * (1 + R),       // +R
    100 * (1 + (3 * R) / 4), // +3R/4
    100 * (1 + R / 2),   // +R/2
    100 * (1 + R / 4),   // +R/4
    100,                  // 0
    100 * (1 - R / 4),   // -R/4
    100 * (1 - R / 2),   // -R/2
    100 * (1 - (3 * R) / 4), // -3R/4
    100 * (1 - R),       // -R
  ];
  for (let i = 0; i < 9; i++) {
    assert.ok(Math.abs(ticks[i] - expected[i]) < 1e-9, `tick[${i}]`);
  }
});

test("calculateIntradayPriceTicks are symmetric around P0", () => {
  const P0 = 65.41;
  const R = 0.0792;
  const ticks = calculateIntradayPriceTicks(P0, R);
  assert.ok(ticks);
  // ticks[4] = P0 (centre)
  assert.ok(Math.abs(ticks[4] - P0) < 1e-9);
  // Symmetric pairs: ticks[4-k] and ticks[4+k] are equidistant from P0
  for (let k = 1; k <= 4; k++) {
    const upper = ticks[4 - k];
    const lower = ticks[4 + k];
    assert.ok(Math.abs((P0 - upper) - (lower - P0)) < 1e-9, `symmetry k=${k}`);
  }
});

test("calculateIntradayPriceTicks edges match yMin and yMax", () => {
  const P0 = 100;
  const R = 0.05;
  const ticks = calculateIntradayPriceTicks(P0, R);
  assert.ok(ticks);
  assert.ok(Math.abs(ticks[0] - P0 * (1 + R)) < 1e-9);   // yMax
  assert.ok(Math.abs(ticks[8] - P0 * (1 - R)) < 1e-9);   // yMin
});

test("calculateIntradayPriceTicks uniform spacing equals tickStep", () => {
  const P0 = 65.41;
  const R = 0.0792;
  const ticks = calculateIntradayPriceTicks(P0, R);
  assert.ok(ticks);
  const tickStep = (R * P0) / 4;
  for (let i = 1; i < 9; i++) {
    const spacing = ticks[i - 1] - ticks[i]; // descending order
    assert.ok(Math.abs(spacing - tickStep) < 1e-9, `spacing[${i}]`);
  }
});

test("calculateIntradayPriceTicks returns null for invalid inputs", () => {
  assert.equal(calculateIntradayPriceTicks(0, 0.05), null);
  assert.equal(calculateIntradayPriceTicks(-1, 0.05), null);
  assert.equal(calculateIntradayPriceTicks(NaN, 0.05), null);
  assert.equal(calculateIntradayPriceTicks(100, -0.05), null);
  assert.equal(calculateIntradayPriceTicks(100, NaN), null);
});

test("calculateIntradayPriceTicks matches issue #143 example: 600584", () => {
  const P0 = 65.41;
  const result = calculateIntradayPriceRange(P0, [
    { open: 65.5, high: 70.59, low: 65.2 },
  ]);
  assert.ok(result);
  const ticks = calculateIntradayPriceTicks(P0, result.R);
  assert.ok(ticks);
  // Top tick = yMax = 70.59
  assert.ok(Math.abs(ticks[0] - 70.59) < 1e-6);
  // Bottom tick = yMin ≈ 60.23
  assert.ok(Math.abs(ticks[8] - P0 * (1 - result.R)) < 1e-6);
  // Centre tick = P0
  assert.ok(Math.abs(ticks[4] - 65.41) < 1e-9);
});