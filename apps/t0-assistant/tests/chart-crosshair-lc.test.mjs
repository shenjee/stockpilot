// 十字线是时间标尺：组内同步只依赖 time，与指标是否有点无关。
import assert from "node:assert/strict";
import test from "node:test";

import {
  CHART_RIGHT_Y_AXIS_WIDTH,
  plotWidthsAligned,
  syncChartGroupPriceScaleWidths,
} from "../renderer/src/charts/chart-scale-alignment.mjs";
import {
  PRICE_EXACT_PRICE_FORMAT,
  formatPriceAxisTickLabels,
  formatPriceExactLabel,
  formatVolumeAxisLabel,
  formatVolumeAxisLabels,
} from "../renderer/src/charts/chart-model.mjs";

const noop = () => {};
const classList = { add: noop, remove: noop, contains: () => false, toggle: noop };
const mql = {
  matches: false,
  addEventListener: noop,
  removeEventListener: noop,
  addListener: noop,
  removeListener: noop,
};
const rect = () => ({
  left: 0,
  top: 0,
  width: 800,
  height: 200,
  right: 800,
  bottom: 200,
  x: 0,
  y: 0,
});
const ctxBase = {
  canvas: {
    width: 800,
    height: 200,
    style: {},
    getBoundingClientRect: rect,
    getClientRects: () => [rect()],
  },
  measureText: (text) => ({ width: String(text).length * 6 }),
};
const ctx = new Proxy(ctxBase, {
  get: (target, prop) => (prop in target ? target[prop] : noop),
  set: (target, prop, value) => {
    target[prop] = value;
    return true;
  },
});

let DOC;
const elExtras = {
  setAttribute: noop,
  getAttribute: () => null,
  toggleAttribute: noop,
  addEventListener: noop,
  removeEventListener: noop,
  appendChild: (child) => child,
  removeChild: noop,
  insertBefore: (node) => node,
  focus: noop,
  blur: noop,
  getBoundingClientRect: rect,
  getClientRects: () => [rect()],
  contains: () => false,
  dispatchEvent: noop,
};

function makeCanvas() {
  return {
    width: 800,
    height: 200,
    style: {},
    classList,
    nodeType: 1,
    tagName: "canvas",
    ownerDocument: DOC,
    getContext: () => ctx,
    ...elExtras,
  };
}

function makeEl(tag) {
  return {
    tagName: tag,
    nodeName: tag,
    nodeType: 1,
    style: {},
    classList,
    children: [],
    childNodes: [],
    clientWidth: 800,
    clientHeight: 200,
    ownerDocument: DOC,
    innerHTML: "",
    textContent: "",
    ...elExtras,
  };
}

function stubCssColor(value) {
  if (typeof value === "string" && /^rgba?\(/i.test(value)) {
    return value;
  }
  if (typeof value === "string" && /^#([0-9a-f]{6})$/i.test(value)) {
    const n = Number.parseInt(value.slice(1), 16);
    return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
  }
  return "rgb(0, 0, 0)";
}

function installDom() {
  const saved = {
    document: globalThis.document,
    window: globalThis.window,
    location: globalThis.location,
    history: globalThis.history,
    getComputedStyle: globalThis.getComputedStyle,
    ResizeObserver: globalThis.ResizeObserver,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    cancelAnimationFrame: globalThis.cancelAnimationFrame,
    devicePixelRatio: globalThis.devicePixelRatio,
    matchMedia: globalThis.matchMedia,
  };
  DOC = {
    createElement: (tag) => (tag === "canvas" ? makeCanvas() : makeEl(tag)),
    createElementNS: (_ns, tag) => (tag === "canvas" ? makeCanvas() : makeEl(tag)),
    addEventListener: noop,
    removeEventListener: noop,
    documentElement: makeEl("html"),
    body: makeEl("body"),
    defaultView: null,
  };
  DOC.documentElement.ownerDocument = DOC;
  DOC.body.ownerDocument = DOC;
  globalThis.document = DOC;
  globalThis.window = globalThis;
  DOC.defaultView = globalThis;
  globalThis.location = {
    href: "http://localhost/",
    search: "",
    hostname: "localhost",
    pathname: "/",
  };
  globalThis.history = { pushState: noop, replaceState: noop };
  // LC 5.x ColorParser reads window.getComputedStyle(el).color as rgb/rgba.
  globalThis.getComputedStyle = (el) => ({
    getPropertyValue: () => "",
    color: stubCssColor(el?.style?.color),
  });
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  const rafQ = [];
  globalThis.requestAnimationFrame = (cb) => {
    rafQ.push(cb);
    return rafQ.length;
  };
  globalThis.cancelAnimationFrame = noop;
  globalThis.__flushRaf = () => {
    let guard = 0;
    while (rafQ.length && guard < 500) {
      rafQ.shift()(Date.now());
      guard += 1;
    }
  };
  globalThis.devicePixelRatio = 1;
  globalThis.matchMedia = () => mql;
  return function restore() {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) {
        delete globalThis[key];
      } else {
        globalThis[key] = value;
      }
    }
    delete globalThis.__flushRaf;
  };
}

function seriesValueAtTime(charts, series, time, MismatchDirection) {
  const index = charts[0].timeScale().timeToIndex(time, true);
  if (index === null) {
    return null;
  }
  const point = series.dataByIndex(index, MismatchDirection.None);
  if (!point || typeof point !== "object") {
    return null;
  }
  if ("close" in point && typeof point.close === "number") {
    return point.close;
  }
  if ("value" in point && typeof point.value === "number") {
    return point.value;
  }
  return null;
}

function attachTimeOnlyCrosshairSync(
  { charts, priceSeries, volumeSeries, difSeries },
  MismatchDirection,
) {
  const targets = [
    { chart: charts[0], series: priceSeries },
    { chart: charts[1], series: volumeSeries },
    { chart: charts[2], series: difSeries },
  ];
  let syncing = false;
  let clearFrame = null;

  const scheduleClear = () => {
    if (clearFrame !== null) {
      return;
    }
    clearFrame = requestAnimationFrame(() => {
      clearFrame = null;
      if (syncing) {
        return;
      }
      syncing = true;
      try {
        for (const chart of charts) {
          chart.clearCrosshairPosition();
        }
      } finally {
        syncing = false;
      }
    });
  };

  const cancelClear = () => {
    if (clearFrame !== null) {
      cancelAnimationFrame(clearFrame);
      clearFrame = null;
    }
  };

  const handlers = [];
  for (const source of targets) {
    const handler = (param) => {
      if (syncing) {
        return;
      }
      if (param.time === undefined) {
        scheduleClear();
        return;
      }
      cancelClear();
      syncing = true;
      try {
        for (const target of targets) {
          if (target === source) {
            continue;
          }
          const price = seriesValueAtTime(
            charts,
            target.series,
            param.time,
            MismatchDirection,
          );
          if (price === null) {
            target.chart.clearCrosshairPosition();
            continue;
          }
          target.chart.setCrosshairPosition(price, param.time, target.series);
        }
      } finally {
        syncing = false;
      }
    };
    source.chart.subscribeCrosshairMove(handler);
    handlers.push(handler);
  }

  return {
    simulateMove(sourceIndex, time) {
      handlers[sourceIndex]({ time });
      globalThis.__flushRaf();
    },
    simulateLeave(sourceIndex) {
      handlers[sourceIndex]({ time: undefined });
      globalThis.__flushRaf();
    },
  };
}

function buildThreeChartFixture(lc, bars) {
  const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = lc;
  const volumePriceFormat = {
    type: "custom",
    formatter: formatVolumeAxisLabel,
    tickmarksFormatter: formatVolumeAxisLabels,
    minMove: 1,
  };
  const charts = ["price", "volume", "macd"].map((kind) => {
    const container = makeEl("div");
    container.clientWidth = 800;
    container.clientHeight = 200;
    return createChart(container, {
      width: 800,
      height: 200,
      rightPriceScale: { minimumWidth: CHART_RIGHT_Y_AXIS_WIDTH },
      localization:
        kind === "volume"
          ? {
              priceFormatter: formatVolumeAxisLabel,
              tickmarksPriceFormatter: formatVolumeAxisLabels,
            }
          : {
              priceFormatter: formatPriceExactLabel,
              tickmarksPriceFormatter: formatPriceAxisTickLabels,
            },
    });
  });
  globalThis.__flushRaf();

  const priceSeries = charts[0].addSeries(CandlestickSeries, {
    priceFormat: PRICE_EXACT_PRICE_FORMAT,
  });
  priceSeries.setData(
    bars.map(({ time, open, high, low, close }) => ({
      time,
      open,
      high,
      low,
      close,
    })),
  );
  const volumeSeries = charts[1].addSeries(HistogramSeries, {
    priceFormat: volumePriceFormat,
  });
  volumeSeries.setData(
    bars.map(({ time, volume, close, open }) => ({
      time,
      value: volume,
      color: close >= open ? "#26a69aaa" : "#ef5350aa",
    })),
  );
  const difSeries = charts[2].addSeries(LineSeries, {
    priceFormat: PRICE_EXACT_PRICE_FORMAT,
  });
  difSeries.setData(
    bars.map(({ time, dif }) => (dif === null ? { time } : { time, value: dif })),
  );
  globalThis.__flushRaf();

  return { charts, priceSeries, volumeSeries, difSeries };
}

test("real LC: MACD accepts time-only crosshair when dif is null", async () => {
  const restore = installDom();
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 10, 0) / 1000;
    const bars = [
      {
        time: Date.UTC(2026, 6, 22, 10, 5, 0) / 1000,
        open: 10.2,
        high: 10.3,
        low: 10.1,
        close: 10.25,
        volume: 94000,
        dif: 0.05,
      },
      {
        time,
        open: 10.25,
        high: 10.35,
        low: 10.2,
        close: 10.32,
        volume: 48000,
        dif: null,
      },
    ];
    const { charts, priceSeries, volumeSeries, difSeries } =
      buildThreeChartFixture(lc, bars);

    assert.equal(
      seriesValueAtTime(charts, difSeries, time, lc.MismatchDirection),
      null,
    );

    let macdCleared = false;
    const macdSetCalls = [];
    const originalClear = charts[2].clearCrosshairPosition.bind(charts[2]);
    const originalSet = charts[2].setCrosshairPosition.bind(charts[2]);
    charts[2].clearCrosshairPosition = () => {
      macdCleared = true;
      originalClear();
    };
    charts[2].setCrosshairPosition = (...args) => {
      macdSetCalls.push(args);
      return originalSet(...args);
    };

    const sync = attachTimeOnlyCrosshairSync(
      { charts, priceSeries, volumeSeries, difSeries },
      lc.MismatchDirection,
    );
    // 从价格图触发同步 handler；dif=null 时应 clear，且不得 setCrosshairPosition(0, ...)。
    sync.simulateMove(0, time);

    assert.equal(macdCleared, true);
    assert.equal(macdSetCalls.length, 0, JSON.stringify(macdSetCalls));
    assert.notEqual(charts[2].timeScale().timeToCoordinate(time), null);
  } finally {
    restore();
  }
});

test("real LC: time-only crosshair sync keeps sibling charts at the same time coordinate", async () => {
  const restore = installDom();
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 10, 0) / 1000;
    const bars = [
      {
        time: Date.UTC(2026, 6, 22, 10, 5, 0) / 1000,
        open: 10.2,
        high: 10.3,
        low: 10.1,
        close: 10.25,
        volume: 94000,
        dif: 0.05,
      },
      {
        time,
        open: 10.25,
        high: 10.35,
        low: 10.2,
        close: 10.32,
        volume: 48000,
        dif: null,
      },
    ];
    const { charts, priceSeries, volumeSeries, difSeries } = buildThreeChartFixture(lc, bars);

    syncChartGroupPriceScaleWidths(charts, {
      flush: () => globalThis.__flushRaf(),
    });

    assert.doesNotThrow(() => {
      charts[0].setCrosshairPosition(10.32, time, priceSeries);
      charts[1].setCrosshairPosition(48000, time, volumeSeries);
      // dif 缺失：清除而非写入假价格。
      charts[2].clearCrosshairPosition();
      globalThis.__flushRaf();
    });

    const coordinates = charts.map((chart) =>
      chart.timeScale().timeToCoordinate(time),
    );
    assert.ok(coordinates.every((value) => value !== null));
    assert.ok(
      coordinates.every(
        (value) => Math.abs(value - coordinates[0]) <= 0.5,
      ),
      coordinates.join(", "),
    );
  } finally {
    restore();
  }
});

test("real LC: leaving all charts clears synced crosshairs", async () => {
  const restore = installDom();
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 10, 0) / 1000;
    const bars = [
      {
        time: Date.UTC(2026, 6, 22, 10, 5, 0) / 1000,
        open: 10.2,
        high: 10.3,
        low: 10.1,
        close: 10.25,
        volume: 94000,
        dif: 0.05,
      },
      {
        time,
        open: 10.25,
        high: 10.35,
        low: 10.2,
        close: 10.32,
        volume: 48000,
        dif: null,
      },
    ];
    const { charts, priceSeries, volumeSeries, difSeries } = buildThreeChartFixture(lc, bars);

    const sync = attachTimeOnlyCrosshairSync(
      {
        charts,
        priceSeries,
        volumeSeries,
        difSeries,
      },
      lc.MismatchDirection,
    );

    charts[0].setCrosshairPosition(10.32, time, priceSeries);
    globalThis.__flushRaf();

    const cleared = [false, false, false];
    for (const [index, chart] of charts.entries()) {
      const originalClear = chart.clearCrosshairPosition.bind(chart);
      chart.clearCrosshairPosition = () => {
        cleared[index] = true;
        originalClear();
      };
    }

    sync.simulateLeave(0);

    assert.ok(cleared.every(Boolean), cleared.join(", "));
  } finally {
    restore();
  }
});

test("real LC: aligned plot widths map the same time to the same x coordinate", async () => {
  const restore = installDom();
  try {
    const lc = await import(
      "../node_modules/lightweight-charts/dist/lightweight-charts.development.mjs"
    );
    const time = Date.UTC(2026, 6, 22, 10, 10, 0) / 1000;
    const bars = Array.from({ length: 40 }, (_, index) => ({
      time: time - (39 - index) * 300,
      open: 10,
      high: 11,
      low: 9,
      close: 10.5,
      volume: 120_000 + index * 1000,
      dif: index % 5 === 0 ? null : 0.01 * index,
    }));
    const { charts, priceSeries, volumeSeries, difSeries } = buildThreeChartFixture(lc, bars);

    const syncResult = syncChartGroupPriceScaleWidths(charts, {
      flush: () => globalThis.__flushRaf(),
    });
    assert.equal(syncResult.converged, true, syncResult.plotWidths.join(", "));
    assert.equal(plotWidthsAligned(syncResult.plotWidths), true);

    attachTimeOnlyCrosshairSync(
      {
        charts,
        priceSeries,
        volumeSeries,
        difSeries,
      },
      lc.MismatchDirection,
    );
    charts[0].setCrosshairPosition(10.5, time, priceSeries);
    globalThis.__flushRaf();

    const coordinates = charts.map((chart) =>
      chart.timeScale().timeToCoordinate(time),
    );
    assert.ok(coordinates.every((value) => value !== null));
    assert.ok(
      coordinates.every(
        (value) => Math.abs(value - coordinates[0]) <= 0.5,
      ),
      coordinates.join(", "),
    );
  } finally {
    restore();
  }
});

test("real controller: dynamic 5m MACD null slot stays x-aligned via time-anchor", async () => {
  // 走 createChartGroupModel → SynchronizedChartGroup 生产路径，而不是手搭 LC。
  // 动态未闭合 K 有成交量、无正式 MACD；fixRightEdge 下仅靠 whitespace 不够，
  // 必须经 macdTimeAnchorSeries 占槽，三图 timeToCoordinate 才对齐。
  const restore = installDom();
  try {
    const { SynchronizedChartGroup } = await import(
      "../renderer/src/charts/SynchronizedChartGroup.ts"
    );
    const { createChartGroupModel, parseMarketTimestamp } = await import(
      "../renderer/src/charts/chart-model.mjs"
    );

    const closedTs = "2026-07-22 10:00:00";
    const lastClosedTs = "2026-07-22 10:05:00";
    const dynamicTs = "2026-07-22 10:10:00";
    const bars = [
      {
        timestamp: closedTs,
        open: 10,
        high: 11,
        low: 9,
        close: 10.2,
        volume: 1000,
        amount: 10200,
        closed: true,
      },
      {
        timestamp: lastClosedTs,
        open: 10.2,
        high: 10.4,
        low: 10.1,
        close: 10.3,
        volume: 1100,
        amount: 11330,
        closed: true,
      },
      {
        timestamp: dynamicTs,
        open: 10.3,
        high: 10.4,
        low: 10.2,
        close: 10.25,
        volume: 500,
        amount: 5125,
        closed: false,
      },
    ];
    const closedMacd = [
      { timestamp: closedTs, value: 0.01 },
      { timestamp: lastClosedTs, value: 0.02 },
    ];
    const snapshot = {
      session: { trade_date: "2026-07-22" },
      market: {
        bars_5m: bars,
        bars_1m: [],
        quote: { previous_close: 10 },
      },
      indicators: {
        five_minute: {
          ma: {},
          boll: { upper: [], middle: [], lower: [] },
          volume: {
            values: bars
              .filter((bar) => bar.closed)
              .map((bar) => ({ timestamp: bar.timestamp, value: bar.volume })),
            ma5: closedMacd.map((point) => ({
              timestamp: point.timestamp,
              value: 1000,
            })),
            ma10: [],
          },
          macd: {
            fast_period: 12,
            slow_period: 26,
            signal_period: 9,
            dif: closedMacd,
            dea: closedMacd,
            histogram: closedMacd,
          },
        },
        one_minute: {
          vwap: [],
          volume: { values: [] },
          macd: { dif: [], dea: [], histogram: [] },
        },
      },
      chan_analysis: {},
    };

    const group = new SynchronizedChartGroup({
      containers: {
        price: makeEl("div"),
        volume: makeEl("div"),
        macd: makeEl("div"),
      },
      kind: "five_minute",
    });
    globalThis.__flushRaf();

    const model = createChartGroupModel(snapshot, "five_minute");
    assert.equal(model.macd.dif.at(-1).value, null);
    assert.equal(model.volume.at(-1).value, 500);
    group.setModel(model);
    globalThis.__flushRaf();

    // Issue #154：动态未闭合 K 必须追加 99 alpha；正式闭合 K 保持不透明。
    // TypeScript private 在运行时仍可访问（与下方 priceChart 用法一致）。
    const { MismatchDirection } = await import("lightweight-charts");
    const closedCandle = group.priceSeries.dataByIndex(0, MismatchDirection.None);
    const dynamicCandle = group.priceSeries.dataByIndex(2, MismatchDirection.None);
    assert.equal(closedCandle.color, "#ef5350");
    assert.equal(closedCandle.borderColor, "#ef5350");
    assert.equal(closedCandle.wickColor, "#ef5350");
    assert.equal(dynamicCandle.color, "#26a69a99");
    assert.equal(dynamicCandle.borderColor, "#26a69a99");
    assert.equal(dynamicCandle.wickColor, "#26a69a99");

    const charts = [group.priceChart, group.volumeChart, group.macdChart];
    const range = { from: 0, to: 2 };
    for (const chart of charts) {
      chart.timeScale().setVisibleLogicalRange(range);
    }
    globalThis.__flushRaf();

    const assertAligned = (timestamp) => {
      const time = parseMarketTimestamp(timestamp);
      const coordinates = charts.map((chart) =>
        chart.timeScale().timeToCoordinate(time),
      );
      assert.ok(
        coordinates.every((value) => value !== null),
        `${timestamp}: ${coordinates.join(", ")}`,
      );
      assert.ok(
        coordinates.every(
          (value) => Math.abs(value - coordinates[0]) <= 0.5,
        ),
        `${timestamp}: ${coordinates.join(", ")}`,
      );
      return coordinates[0];
    };
    const closedX = assertAligned(lastClosedTs);
    const dynamicX = assertAligned(dynamicTs);
    assert.ok(
      Math.abs(dynamicX - closedX) > 1,
      `dynamic and closed slots must map to distinct x (${closedX}, ${dynamicX})`,
    );
  } finally {
    restore();
  }
});
