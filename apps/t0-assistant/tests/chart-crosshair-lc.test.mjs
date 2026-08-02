// 十字线是时间标尺：组内同步只依赖 time，与指标是否有点无关。
import assert from "node:assert/strict";
import test from "node:test";

import {
  plotWidthsAligned,
  syncChartGroupPriceScaleWidths,
} from "../renderer/src/charts/chart-scale-alignment.mjs";


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

function attachTimeOnlyCrosshairSync({ charts, priceSeries, volumeSeries, difSeries }) {
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
          target.chart.setCrosshairPosition(0, param.time, target.series);
        }
      } finally {
        syncing = false;
      }
    };
    source.chart.subscribeCrosshairMove(handler);
    handlers.push(handler);
  }

  return {
    simulateLeave(sourceIndex) {
      handlers[sourceIndex]({ time: undefined });
      globalThis.__flushRaf();
    },
  };
}

function buildThreeChartFixture(lc, bars) {
  const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = lc;
  const charts = ["price", "volume", "macd"].map(() => {
    const container = makeEl("div");
    container.clientWidth = 800;
    container.clientHeight = 200;
    return createChart(container, {
      width: 800,
      height: 200,
      rightPriceScale: { minimumWidth: 58 },
    });
  });
  globalThis.__flushRaf();

  const priceSeries = charts[0].addSeries(CandlestickSeries);
  priceSeries.setData(
    bars.map(({ time, open, high, low, close }) => ({
      time,
      open,
      high,
      low,
      close,
    })),
  );
  const volumeSeries = charts[1].addSeries(HistogramSeries);
  volumeSeries.setData(
    bars.map(({ time, volume, close, open }) => ({
      time,
      value: volume,
      color: close >= open ? "#26a69aaa" : "#ef5350aa",
    })),
  );
  const difSeries = charts[2].addSeries(LineSeries);
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
    const { charts, difSeries } = buildThreeChartFixture(lc, bars);

    let macdCleared = false;
    const originalClear = charts[2].clearCrosshairPosition.bind(charts[2]);
    charts[2].clearCrosshairPosition = () => {
      macdCleared = true;
      originalClear();
    };

    assert.doesNotThrow(() => {
      charts[2].setCrosshairPosition(0, time, difSeries);
      globalThis.__flushRaf();
    });
    assert.equal(macdCleared, false);
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
    const { charts, priceSeries, volumeSeries, difSeries } = buildThreeChartFixture(
      lc,
      bars,
    );

    syncChartGroupPriceScaleWidths(charts, {
      flush: () => globalThis.__flushRaf(),
    });

    assert.doesNotThrow(() => {
      charts[0].setCrosshairPosition(10.32, time, priceSeries);
      charts[1].setCrosshairPosition(0, time, volumeSeries);
      charts[2].setCrosshairPosition(0, time, difSeries);
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
    const { charts, priceSeries, volumeSeries, difSeries } = buildThreeChartFixture(
      lc,
      bars,
    );

    const sync = attachTimeOnlyCrosshairSync({
      charts,
      priceSeries,
      volumeSeries,
      difSeries,
    });

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
    const { charts, priceSeries, volumeSeries, difSeries } = buildThreeChartFixture(
      lc,
      bars,
    );

    const syncResult = syncChartGroupPriceScaleWidths(charts, {
      flush: () => globalThis.__flushRaf(),
    });
    assert.equal(syncResult.converged, true, syncResult.plotWidths.join(", "));
    assert.equal(plotWidthsAligned(syncResult.plotWidths), true);

    attachTimeOnlyCrosshairSync({
      charts,
      priceSeries,
      volumeSeries,
      difSeries,
    });
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
