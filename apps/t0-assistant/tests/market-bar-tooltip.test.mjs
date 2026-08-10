import assert from "node:assert/strict";
import test from "node:test";

import {
  MARKET_BAR_TOOLTIP_MARGIN_PX,
  buildMarketBarTooltipViewModel,
  findMarketBarByTimestamp,
  findMarketBarByUtcSeconds,
  formatMarketBarTooltipDate,
  formatMarketBarTooltipPrice,
  formatMarketBarTooltipTime,
  formatMarketBarTooltipVolume,
  isPointerInPricePlotArea,
  resolveMarketBarDirection,
  resolveMarketBarTooltipCorner,
  shouldShowMarketBarTooltip,
} from "../renderer/src/charts/market-bar-tooltip.mjs";
import { parseMarketTimestamp } from "../renderer/src/charts/chart-model.mjs";

test("tooltip formats date and contract time point", () => {
  assert.equal(
    formatMarketBarTooltipDate("2026-07-22 09:35:00"),
    "2026-07-22",
  );
  assert.equal(formatMarketBarTooltipTime("2026-07-22 09:35:00"), "09:35");
  assert.equal(formatMarketBarTooltipTime("2026-07-22T14:55:00"), "14:55");
  assert.equal(formatMarketBarTooltipDate(""), "");
  assert.equal(formatMarketBarTooltipTime("bad"), "");
});

test("tooltip formats prices to two decimals and volume with grouping", () => {
  assert.equal(formatMarketBarTooltipPrice(10), "10.00");
  assert.equal(formatMarketBarTooltipPrice(10.125), "10.13");
  assert.equal(formatMarketBarTooltipPrice(Number.NaN), "");
  assert.equal(formatMarketBarTooltipVolume(51000), "51,000");
  assert.equal(formatMarketBarTooltipVolume(0), "0");
  assert.equal(formatMarketBarTooltipVolume(-1), "");
  assert.equal(formatMarketBarTooltipVolume(1e12), "1,000,000,000,000");
  assert.ok(!/e/i.test(formatMarketBarTooltipVolume(1e12)));
});

test("tooltip direction uses A-share up-red / down-green arrows", () => {
  assert.deepEqual(resolveMarketBarDirection(10, 10.08), {
    arrow: "▲",
    up: true,
  });
  assert.deepEqual(resolveMarketBarDirection(10, 10), {
    arrow: "▲",
    up: true,
  });
  assert.deepEqual(resolveMarketBarDirection(10, 9.98), {
    arrow: "▼",
    up: false,
  });
  assert.equal(resolveMarketBarDirection(null, 10), null);
});

test("exact timestamp bar mapping never falls back to neighbors", () => {
  const bars = [
    {
      timestamp: "2026-07-22 09:35:00",
      open: 10,
      high: 10.1,
      low: 9.9,
      close: 10.05,
      volume: 1000,
      amount: 0,
      closed: true,
    },
    {
      timestamp: "2026-07-22 09:40:00",
      open: 10.05,
      high: 10.2,
      low: 10,
      close: 10.1,
      volume: 2000,
      amount: 0,
      closed: true,
    },
  ];
  assert.equal(
    findMarketBarByTimestamp(bars, "2026-07-22 09:35:00")?.timestamp,
    "2026-07-22 09:35:00",
  );
  assert.equal(findMarketBarByTimestamp(bars, "2026-07-22 09:37:00"), null);
  assert.equal(findMarketBarByTimestamp(bars, "2026-07-22 09:45:00"), null);

  const timeByTimestamp = Object.fromEntries(
    bars.map((bar) => [bar.timestamp, parseMarketTimestamp(bar.timestamp)]),
  );
  const hit = findMarketBarByUtcSeconds(
    bars,
    timeByTimestamp,
    parseMarketTimestamp("2026-07-22 09:40:00"),
  );
  assert.equal(hit?.timestamp, "2026-07-22 09:40:00");
  assert.equal(
    findMarketBarByUtcSeconds(
      bars,
      timeByTimestamp,
      parseMarketTimestamp("2026-07-22 09:35:00") + 60,
    ),
    null,
  );
});

test("corner strategy pins tooltip to opposite top corner", () => {
  assert.equal(
    resolveMarketBarTooltipCorner({ barCoordinate: 100, plotWidth: 400 }),
    "right",
  );
  assert.equal(
    resolveMarketBarTooltipCorner({ barCoordinate: 300, plotWidth: 400 }),
    "left",
  );
  assert.equal(
    resolveMarketBarTooltipCorner({ barCoordinate: 200, plotWidth: 400 }),
    "right",
  );
  assert.equal(
    resolveMarketBarTooltipCorner({ barCoordinate: 200, plotWidth: 0 }),
    null,
  );
  assert.equal(MARKET_BAR_TOOLTIP_MARGIN_PX, 14);
});

test("visibility requires price-plot pointer, no drag, and matched bar", () => {
  const bar = { timestamp: "2026-07-22 09:35:00" };
  assert.equal(
    shouldShowMarketBarTooltip({
      pointerOverPricePlot: true,
      isDragging: false,
      bar,
    }),
    true,
  );
  assert.equal(
    shouldShowMarketBarTooltip({
      pointerOverPricePlot: false,
      isDragging: false,
      bar,
    }),
    false,
  );
  assert.equal(
    shouldShowMarketBarTooltip({
      pointerOverPricePlot: true,
      isDragging: true,
      bar,
    }),
    false,
  );
  assert.equal(
    shouldShowMarketBarTooltip({
      pointerOverPricePlot: true,
      isDragging: false,
      bar: null,
    }),
    false,
  );
});

test("plot-area hit test excludes the right price scale", () => {
  const containerRect = { left: 10, top: 20, width: 800, height: 300 };
  assert.equal(
    isPointerInPricePlotArea({
      clientX: 10 + 100,
      clientY: 20 + 50,
      containerRect,
      plotWidth: 700,
      plotHeight: 280,
    }),
    true,
  );
  assert.equal(
    isPointerInPricePlotArea({
      clientX: 10 + 750,
      clientY: 20 + 50,
      containerRect,
      plotWidth: 700,
      plotHeight: 280,
    }),
    false,
  );
  assert.equal(
    isPointerInPricePlotArea({
      clientX: 10 + 100,
      clientY: 20 + 290,
      containerRect,
      plotWidth: 700,
      plotHeight: 280,
    }),
    false,
  );
});

test("view model builds ordered fields without forming-range copy", () => {
  const view = buildMarketBarTooltipViewModel({
    timestamp: "2026-07-22 09:35:00",
    open: 10,
    high: 10.12,
    low: 9.98,
    close: 10.08,
    volume: 51000,
    amount: 0,
    closed: false,
  });
  assert.deepEqual(view, {
    date: "2026-07-22",
    time: "09:35",
    open: "10.00",
    high: "10.12",
    low: "9.98",
    close: "10.08",
    volume: "51,000",
    direction: { arrow: "▲", up: true },
  });
  assert.equal(buildMarketBarTooltipViewModel(null), null);
  assert.equal(
    buildMarketBarTooltipViewModel({
      timestamp: "2026-07-22 09:35:00",
      open: Number.NaN,
      high: 10,
      low: 9,
      close: 10,
      volume: 1,
    }),
    null,
  );
});
