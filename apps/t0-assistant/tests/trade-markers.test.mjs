import test from "node:test";
import assert from "node:assert/strict";
import {
  formatLotLabel,
  projectTradeMarker,
  projectTradeMarkers,
  sortTradeMarkers,
} from "../renderer/src/charts/trade-markers.mjs";
import {
  createChartGroupModel,
  ChartGroupKind,
} from "../renderer/src/charts/chart-model.mjs";

const realBuy = {
  trade_id: "real-buy-1",
  bucket_start: "2024-07-22 10:00:00",
  trade_scope: "real",
  symbol: "sh.600584",
  side: "buy",
  executed_at: "2024-07-22 10:00:00",
  price: 38.25,
  quantity: 200,
  fee: 5.01,
  note: "",
  fee_plan_id: "shenwan-hongyuan",
};

const realSell = {
  trade_id: "real-sell-1",
  bucket_start: "2024-07-22 10:00:00",
  trade_scope: "real",
  symbol: "sh.600584",
  side: "sell",
  executed_at: "2024-07-22 10:00:00",
  price: 38.5,
  quantity: 200,
  fee: 8.1,
  note: "",
  fee_plan_id: "shenwan-hongyuan",
};

const realBuyLater = {
  trade_id: "real-buy-2",
  bucket_start: "2024-07-22 10:05:00",
  trade_scope: "real",
  symbol: "sh.600584",
  side: "buy",
  executed_at: "2024-07-22 10:05:00",
  price: 38.1,
  quantity: 300,
  fee: 5.02,
  note: "",
  fee_plan_id: "shenwan-hongyuan",
};

const realSellLater = {
  trade_id: "real-sell-2",
  bucket_start: "2024-07-22 10:05:00",
  trade_scope: "real",
  symbol: "sh.600584",
  side: "sell",
  executed_at: "2024-07-22 10:05:00",
  price: 38.15,
  quantity: 300,
  fee: 8.2,
  note: "",
  fee_plan_id: "shenwan-hongyuan",
};

function bar(timestamp, open, high, low, close, volume, amount, closed) {
  return { timestamp, open, high, low, close, volume, amount, closed };
}

function macd() {
  return {
    fast_period: 12,
    slow_period: 26,
    signal_period: 9,
    dif: [],
    dea: [],
    histogram: [],
  };
}

function minimalSnapshot({ bars_5m = [], bars_1m = [] } = {}) {
  return {
    timezone: "Asia/Shanghai",
    market: {
      bars_1m,
      bars_5m,
      daily_bars: [],
      quote: null,
    },
    indicators: {
      five_minute: {
        ma: { ma5: [], ma10: [], ma20: [], ma30: [], ma60: [] },
        volume: { values: [], ma5: [], ma10: [] },
        macd: macd(),
      },
      one_minute: {
        vwap: [],
        volume: { values: [], ma5: [], ma10: [] },
        macd: macd(),
      },
    },
  };
}

function makeSnapshot() {
  return minimalSnapshot({
    bars_5m: [
      bar("2024-07-22 10:00:00", 38.0, 38.5, 37.9, 38.25, 1000, 38000, true),
      bar("2024-07-22 10:05:00", 38.25, 38.4, 38.05, 38.1, 800, 30400, true),
    ],
  });
}

function allowedTimesFromModel(model) {
  return new Set(Object.values(model.timeByTimestamp));
}

test("formatLotLabel formats whole and fractional lots", () => {
  assert.equal(formatLotLabel(200), "2");
  assert.equal(formatLotLabel(150), "1.5");
  assert.equal(formatLotLabel(50), "0.5");
  assert.equal(formatLotLabel(1), "0.01");
});

test("projectTradeMarker maps real trades", () => {
  const real = projectTradeMarker(realBuy);
  assert.equal(real?.trade_scope, "real");
  assert.equal(real?.label, "B2");
});

test("projectTradeMarker labels buy as B{lots} and sell as S{lots}", () => {
  assert.equal(projectTradeMarker(realBuy)?.label, "B2");
  assert.equal(projectTradeMarker(realSell)?.label, "S2");
});

test("projectTradeMarker preserves fractional lot labels", () => {
  const fractional = { ...realBuy, quantity: 150 };
  assert.equal(projectTradeMarker(fractional)?.label, "B1.5");

  const tiny = { ...realBuy, quantity: 50 };
  assert.equal(projectTradeMarker(tiny)?.label, "B0.5");
});

test("projectTradeMarker uses actual trade price as the marker price", () => {
  assert.equal(projectTradeMarker(realBuy)?.price, 38.25);
});

test("projectTradeMarker derives chart time from bucket_start", () => {
  const marker = projectTradeMarker(realBuy);
  assert.equal(marker?.time, Date.UTC(2024, 6, 22, 10, 0, 0) / 1000);
});

test("projectTradeMarker drops invalid records", () => {
  assert.equal(projectTradeMarker(null), null);
  assert.equal(projectTradeMarker({ ...realBuy, side: "hold" }), null);
  assert.equal(projectTradeMarker({ ...realBuy, price: -1 }), null);
  assert.equal(projectTradeMarker({ ...realBuy, trade_scope: "fake" }), null);
});

test("projectTradeMarker keeps distinct colors for buy and sell", () => {
  const buy = projectTradeMarker(realBuy);
  const sell = projectTradeMarker(realSell);
  assert.notEqual(buy?.color, sell?.color);
  assert.equal(buy?.shape, "circle");
  assert.equal(sell?.shape, "square");
});

test("projectTradeMarkers keeps multiple trades in the same 5m bucket", () => {
  const markers = projectTradeMarkers([realBuy, realSell]);
  assert.equal(markers.length, 2);
  const ids = markers.map((m) => m.trade_id);
  assert.ok(ids.includes("real-buy-1"));
  assert.ok(ids.includes("real-sell-1"));
});

test("projectTradeMarkers keeps separate markers when prices differ in the same bucket", () => {
  const markers = projectTradeMarkers([realBuy, realSell]);
  assert.equal(markers[0].price, 38.25);
  assert.equal(markers[1].price, 38.5);
});

test("projectTradeMarkers sorts same-bucket markers into a stable order", () => {
  const markers = projectTradeMarkers([realSell, realBuy]);
  assert.deepEqual(
    markers.map((m) => m.trade_id),
    ["real-buy-1", "real-sell-1"],
  );
});

test("trade overlay projects independently of ChartGroupModel", () => {
  // Issue #163: trades are not baked into createChartGroupModel; App projects
  // markers separately and passes them to SynchronizedChartGroup.setTradeMarkers.
  const snapshot = makeSnapshot();
  const model = createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE);
  assert.equal(Object.hasOwn(model, "tradeMarkers"), false);

  const markers = projectTradeMarkers(
    [realBuy, realSell, realBuyLater, realSellLater],
    { allowedTimes: allowedTimesFromModel(model) },
  );
  assert.equal(markers.length, 4);
});

test("projectTradeMarkers filters out trades without a matching 5m K-line", () => {
  const snapshot = minimalSnapshot({
    bars_5m: [
      bar("2024-07-22 11:00:00", 38, 39, 37, 38.5, 100, 3800, true),
    ],
  });
  const model = createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE);
  const markers = projectTradeMarkers([realBuy], {
    allowedTimes: allowedTimesFromModel(model),
  });
  assert.deepEqual(markers, []);
});

test("projectTradeMarkers returns an empty array for non-array input", () => {
  assert.deepEqual(projectTradeMarkers(null), []);
  assert.deepEqual(projectTradeMarkers(undefined), []);
});

test("sortTradeMarkers orders by time, then buy before sell, then price, then trade_id", () => {
  const a = projectTradeMarker({
    ...realSell,
    trade_id: "a",
    price: 38.0,
  });
  const b = projectTradeMarker({
    ...realBuy,
    trade_id: "b",
    price: 38.0,
  });
  const c = projectTradeMarker({
    ...realSell,
    trade_id: "c",
    price: 38.5,
  });
  const d = projectTradeMarker({
    ...realSell,
    trade_id: "d",
    price: 38.5,
  });

  const sorted = sortTradeMarkers([a, b, c, d].filter(Boolean));
  assert.deepEqual(sorted.map((m) => m.trade_id), ["b", "a", "c", "d"]);
});

test("chart model rebuild is independent of trade list changes", () => {
  const snapshot = makeSnapshot();
  const modelA = createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE);
  const modelB = createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE);
  assert.deepEqual(modelA.timestamps, modelB.timestamps);
  assert.equal(modelA.bars.length, modelB.bars.length);

  const withTrades = projectTradeMarkers([realBuy], {
    allowedTimes: allowedTimesFromModel(modelA),
  });
  const withoutTrades = projectTradeMarkers([], {
    allowedTimes: allowedTimesFromModel(modelB),
  });
  assert.equal(withTrades.length, 1);
  assert.equal(withoutTrades.length, 0);
});
