import test from "node:test";
import assert from "node:assert/strict";
import { projectTradeMarkers } from "../renderer/src/charts/trade-markers.mjs";
import {
  ChartGroupKind,
  createChartGroupModel,
} from "../renderer/src/charts/chart-model.mjs";

/**
 * T0-043 "进入当天图形": from a history record, the renderer loads the trade's
 * symbol + trading day, then overlays ALL of that day's real trades on the 5m
 * chart at their execution price (reusing the T0-042 marker layer). It must not
 * recompute market data or markers in the renderer, and must not start Replay.
 *
 * These tests pin the marker-overlay composition: filter the authoritative full
 * snapshot to one symbol/date, then project with the day's 5m bar times as the
 * `allowedTimes` gate so markers can never invent candles.
 */

function ts(timestamp) {
  // Mirror trade-markers.parseMarketTimestampSeconds (UTC seconds).
  const [date, time] = timestamp.split(" ");
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm, ss] = time.split(":").map(Number);
  return Date.UTC(y, m - 1, d, hh, mm, ss) / 1000;
}

function bar(timestamp, overrides = {}) {
  return {
    timestamp,
    open: 10,
    high: 11,
    low: 9,
    close: 10.5,
    volume: 1000,
    amount: 10000,
    closed: true,
    ...overrides,
  };
}

function snapshotWithBars(bars) {
  return {
    timezone: "Asia/Shanghai",
    market: {
      bars_5m: bars,
      bars_1m: [],
      daily_bars: [],
      quote: null,
    },
    indicators: {
      five_minute: {
        ma: { ma5: [], ma10: [], ma20: [], ma30: [], ma60: [] },
        volume: { values: [], ma5: [], ma10: [] },
        macd: { fast_period: 12, slow_period: 26, signal_period: 9, dif: [], dea: [], histogram: [] },
      },
      one_minute: {
        vwap: [],
        volume: { values: [] },
        macd: { fast_period: 12, slow_period: 26, signal_period: 9, dif: [], dea: [], histogram: [] },
      },
    },
    chan_analysis: { strokes: [], pivot_zones: [] },
  };
}

function trade(overrides = {}) {
  return {
    trade_id: "t-1",
    bucket_start: "2026-07-24 10:00:00",
    trade_scope: "real",
    symbol: "sh.600584",
    side: "buy",
    executed_at: "2026-07-24 10:03:00",
    price: 38.25,
    quantity: 200,
    fee: 5.01,
    note: "",
    fee_plan_id: "shenwan-hongyuan",
    ...overrides,
  };
}

function dayTrades(snapshot, symbol, tradeDate) {
  // The renderer filters the full snapshot client-side (no scope on the wire).
  return snapshot.filter(
    (t) => t.symbol === symbol && t.executed_at.slice(0, 10) === tradeDate,
  );
}

test("进入当天图形 marks every real trade of that symbol/day at its execution price", () => {
  const snapshot = [
    trade({ trade_id: "a", executed_at: "2026-07-24 10:03:00", price: 38.20, quantity: 200 }),
    trade({ trade_id: "b", executed_at: "2026-07-24 10:04:00", price: 38.40, quantity: 100 }),
    // Other symbol / other day must NOT appear on this day's chart.
    trade({ trade_id: "c", symbol: "sz.000001", executed_at: "2026-07-24 10:03:00", price: 12.0, quantity: 500 }),
    trade({ trade_id: "d", executed_at: "2026-07-25 14:10:00", price: 39.0, quantity: 200 }),
  ];
  const barTimes = new Set([
    ts("2026-07-24 10:00:00"),
    ts("2026-07-24 10:05:00"),
    ts("2026-07-24 10:10:00"),
  ]);
  const markers = projectTradeMarkers(dayTrades(snapshot, "sh.600584", "2026-07-24"), {
    allowedTimes: barTimes,
  });
  assert.deepEqual(
    markers.map((m) => m.trade_id).sort(),
    ["a", "b"],
    "only this symbol/day's trades are marked",
  );
  // Vertical coordinate is the actual execution price (T0-042 contract).
  const byId = Object.fromEntries(markers.map((m) => [m.trade_id, m]));
  assert.equal(byId.a.price, 38.20);
  assert.equal(byId.b.price, 38.40);
  // Both fall in the 10:00 5m bucket.
  assert.equal(byId.a.time, ts("2026-07-24 10:00:00"));
  assert.equal(byId.b.time, ts("2026-07-24 10:00:00"));
});

test("multiple trades in the same 5m bucket are shown separately and distinctly from CZSC", () => {
  const snapshot = [
    trade({ trade_id: "buy1", executed_at: "2026-07-24 10:01:00", price: 38.20, quantity: 200, side: "buy" }),
    trade({ trade_id: "buy2", executed_at: "2026-07-24 10:02:00", price: 38.30, quantity: 100, side: "buy" }),
    trade({ trade_id: "sell1", executed_at: "2026-07-24 10:04:00", price: 38.50, quantity: 300, side: "sell" }),
  ];
  const barTimes = new Set([ts("2026-07-24 10:00:00")]);
  const markers = projectTradeMarkers(dayTrades(snapshot, "sh.600584", "2026-07-24"), {
    allowedTimes: barTimes,
  });
  assert.equal(markers.length, 3, "all three survive in the same bucket");
  // Labels are B/S + lots (not CZSC 1B/1S/2B/2S).
  const labels = markers.map((m) => m.label).sort();
  assert.deepEqual(labels, ["B2", "B1", "S3"].sort());
  assert.ok(markers.every((m) => !/^(\d[BS]|[BS]\d)/.test(m.label) || /^[BS]\d/.test(m.label)));
  // Stable order: time asc, buy before sell, price asc, trade_id asc.
  assert.deepEqual(
    markers.map((m) => m.trade_id),
    ["buy1", "buy2", "sell1"],
  );
});

test("a trade whose 5m bucket has no chart bar is dropped (no fake candle)", () => {
  const snapshot = [
    trade({ trade_id: "on-chart", executed_at: "2026-07-24 10:03:00", bucket_start: "2026-07-24 10:00:00" }),
    // This trade's bucket (11:00) is not in the day's bars.
    trade({ trade_id: "off-chart", executed_at: "2026-07-24 11:03:00", bucket_start: "2026-07-24 11:00:00" }),
  ];
  const barTimes = new Set([ts("2026-07-24 10:00:00")]);
  const markers = projectTradeMarkers(dayTrades(snapshot, "sh.600584", "2026-07-24"), {
    allowedTimes: barTimes,
  });
  assert.deepEqual(
    markers.map((m) => m.trade_id),
    ["on-chart"],
    "the off-chart trade is dropped rather than inventing a candle",
  );
});

test("createChartGroupModel wires trades into the five_minute group markers", () => {
  // createChartGroupModel does not filter by symbol/date; App filters the
  // authoritative full snapshot before passing it in. This test verifies the
  // actual App -> chart wiring path: trades arrive as markers on the 5m group.
  const model = createChartGroupModel(
    snapshotWithBars([bar("2026-07-24 10:00:00"), bar("2026-07-24 10:05:00")]),
    ChartGroupKind.FIVE_MINUTE,
    {},
    dayTrades(
      [
        trade({ trade_id: "a", executed_at: "2026-07-24 10:03:00", price: 38.20, quantity: 200 }),
        trade({ trade_id: "b", executed_at: "2026-07-24 10:04:00", price: 38.40, quantity: 100, side: "sell" }),
        trade({ trade_id: "c", symbol: "sz.000001", executed_at: "2026-07-24 10:03:00" }),
        trade({ trade_id: "d", executed_at: "2026-07-25 14:10:00" }),
      ],
      "sh.600584",
      "2026-07-24",
    ),
  );
  assert.equal(model.tradeMarkers?.length, 2, "two markers rendered");
  const ids = model.tradeMarkers.map((m) => m.trade_id).sort();
  assert.deepEqual(ids, ["a", "b"]);
  const byId = Object.fromEntries(model.tradeMarkers.map((m) => [m.trade_id, m]));
  assert.equal(byId.a.price, 38.20);
  assert.equal(byId.b.price, 38.40);
});

test("createChartGroupModel ignores trades on the one_minute group", () => {
  const model = createChartGroupModel(
    snapshotWithBars([bar("2026-07-24 10:00:00")]),
    ChartGroupKind.ONE_MINUTE,
    {},
    [trade({ trade_id: "a", executed_at: "2026-07-24 10:03:00" })],
  );
  assert.deepEqual(model.tradeMarkers, []);
});

test("historical wiring path overlays only the loaded day's trades on the 5m chart", () => {
  // App filters the full repository snapshot by snapshot.session.symbol and
  // snapshot.session.trade_date before passing trades to createChartGroupModel.
  const historicalSnapshot = {
    ...snapshotWithBars([
      bar("2026-07-20 10:00:00"),
      bar("2026-07-20 10:05:00"),
    ]),
    session: {
      session_id: "historical:sh.600584:2026-07-20",
      session_type: "historical",
      symbol: "sh.600584",
      trade_date: "2026-07-20",
      state: "ready",
      revision: 0,
    },
  };
  const allTrades = [
    trade({ trade_id: "on-day", bucket_start: "2026-07-20 10:00:00", executed_at: "2026-07-20 10:01:00", price: 38.2, quantity: 200 }),
    trade({ trade_id: "other-symbol", bucket_start: "2026-07-20 10:00:00", symbol: "sz.000001", executed_at: "2026-07-20 10:02:00" }),
    trade({ trade_id: "other-day", bucket_start: "2026-07-21 10:00:00", executed_at: "2026-07-21 10:01:00" }),
  ];
  const filtered = allTrades.filter(
    (t) => t.symbol === historicalSnapshot.session.symbol && t.executed_at.slice(0, 10) === historicalSnapshot.session.trade_date,
  );
  const model = createChartGroupModel(
    historicalSnapshot,
    ChartGroupKind.FIVE_MINUTE,
    {},
    filtered,
  );
  assert.equal(model.tradeMarkers.length, 1);
  assert.equal(model.tradeMarkers[0].trade_id, "on-day");
  assert.equal(model.tradeMarkers[0].price, 38.2);
});
