import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  applyLiveChartEvent,
  applyWorkbenchSnapshot,
  beginChartSession,
  createChartProjection,
} from "../renderer/src/charts/chart-projection.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  await readFile(
    resolve(testDir, "../contracts/fixtures/workbench-flow-v2.json"),
    "utf8",
  ),
);
const baseline = fixture.initial_snapshot_event.payload;
const [marketUpdate, indicatorUpdate, chanUpdate] = fixture.incremental_events;
const baselineProjection = () =>
  createChartProjection(baseline, fixture.initial_snapshot_event);

test("Live projection applies bar and indicator increments without dropping history", () => {
  const afterMarket = applyLiveChartEvent(
    baselineProjection(),
    marketUpdate,
  );
  const afterIndicators = applyLiveChartEvent(afterMarket, indicatorUpdate);

  assert.deepEqual(
    afterMarket.snapshot.market.bars_1m.map((bar) => bar.timestamp),
    ["2026-07-22 09:31:00", "2026-07-22 09:32:00"],
  );
  assert.deepEqual(
    afterIndicators.snapshot.indicators.one_minute.vwap.map(
      (point) => point.timestamp,
    ),
    ["2026-07-22 09:31:00", "2026-07-22 09:32:00"],
  );
  assert.equal(
    afterIndicators.snapshot.indicators.five_minute.volume.values.length,
    baseline.indicators.five_minute.volume.values.length,
  );
  assert.equal(afterIndicators.revision, 3);
});

test("Live projection drops an indicator point until its matching bar arrives", () => {
  const futureTimestamp = "2026-07-22 09:40:00";
  const event = structuredClone(indicatorUpdate);
  event.revision = 2;
  event.payload.five_minute.boll.upper.push({
    timestamp: futureTimestamp,
    value: 10.3,
  });

  const projected = applyLiveChartEvent(baselineProjection(), event);

  assert.equal(
    projected.snapshot.indicators.five_minute.boll.upper.some(
      (point) => point.timestamp === futureTimestamp,
    ),
    false,
  );
});

test("Live projection replaces an updated timestamp instead of duplicating it", () => {
  const first = applyLiveChartEvent(baselineProjection(), marketUpdate);
  const revisedEvent = structuredClone(marketUpdate);
  revisedEvent.revision = 3;
  revisedEvent.payload.bars[0].close = 10.11;
  const revised = applyLiveChartEvent(first, revisedEvent);

  assert.equal(revised.snapshot.market.bars_1m.length, 2);
  assert.equal(revised.snapshot.market.bars_1m.at(-1).close, 10.11);
});

test("Live projection ignores increments from a retired Session", () => {
  const staleEvent = structuredClone(marketUpdate);
  staleEvent.session_id = "retired-live-session";
  const projection = baselineProjection();

  assert.strictEqual(applyLiveChartEvent(projection, staleEvent), projection);
});

test("Live projection rejects old generations and stale revisions", () => {
  const projection = baselineProjection();
  const oldGeneration = structuredClone(marketUpdate);
  oldGeneration.service_generation -= 1;
  const staleRevision = structuredClone(marketUpdate);
  staleRevision.revision = 1;

  assert.strictEqual(
    applyLiveChartEvent(projection, oldGeneration),
    projection,
  );
  assert.strictEqual(
    applyLiveChartEvent(projection, staleRevision),
    projection,
  );
});

test("a revision gap blocks increments until a full snapshot rebaseline", () => {
  const projection = baselineProjection();
  const gap = structuredClone(marketUpdate);
  gap.revision = 4;
  const blocked = applyLiveChartEvent(projection, gap);

  assert.equal(blocked.rebaselineRequired, true);
  assert.equal(blocked.revision, 1);
  assert.strictEqual(
    applyLiveChartEvent(blocked, marketUpdate),
    blocked,
  );

  const replacement = structuredClone(baseline);
  replacement.session.revision = 4;
  const rebaselined = applyWorkbenchSnapshot(blocked, replacement, {
    service_generation: gap.service_generation,
    session_id: gap.session_id,
    revision: gap.revision,
  });
  assert.equal(rebaselined.rebaselineRequired, false);
  assert.equal(rebaselined.revision, 4);
});

test("a stale or mismatched full snapshot cannot replace the current baseline", () => {
  const projection = applyLiveChartEvent(
    baselineProjection(),
    marketUpdate,
  );
  const stale = applyWorkbenchSnapshot(
    projection,
    baseline,
    fixture.initial_snapshot_event,
  );
  const wrongGeneration = applyWorkbenchSnapshot(projection, baseline, {
    ...fixture.initial_snapshot_event,
    service_generation: fixture.service_generation + 1,
    revision: 3,
  });

  assert.strictEqual(stale, projection);
  assert.strictEqual(wrongGeneration, projection);
});

test("starting a new security selection retires the previous Session identity", () => {
  const selecting = beginChartSession(
    baseline,
    fixture.service_generation,
    "live-fixture-2",
  );
  const replacement = structuredClone(baseline);
  replacement.session.session_id = "live-fixture-2";
  replacement.session.symbol = "sz.000001";
  replacement.session.revision = 1;

  const selected = applyWorkbenchSnapshot(selecting, replacement, {
    service_generation: fixture.service_generation,
    session_id: "live-fixture-2",
    revision: 1,
  });

  assert.strictEqual(selecting.snapshot, baseline);
  assert.equal(selecting.sessionId, "live-fixture-2");
  assert.equal(selecting.revision, null);
  assert.equal(selected.sessionId, "live-fixture-2");
  assert.equal(selected.snapshot.session.symbol, "sz.000001");
});

test("indicator, Chan, quote, and daily-bar increments update sidebar layers", () => {
  const indicators = structuredClone(indicatorUpdate);
  indicators.payload.five_minute.ma.ma5[0] = {
    timestamp: "2026-07-22 09:35:00",
    value: 10.08,
  };
  const afterMarket = applyLiveChartEvent(
    baselineProjection(),
    marketUpdate,
  );
  const afterIndicators = applyLiveChartEvent(
    afterMarket,
    indicators,
  );
  const afterChan = applyLiveChartEvent(afterIndicators, chanUpdate);
  const quoteUpdate = {
    ...marketUpdate,
    revision: 5,
    event_type: "market_update",
    payload: {
      target: "quote",
      bars: [],
      quote: { ...baseline.market.quote, latest_price: 10.2 },
    },
  };
  const afterQuote = applyLiveChartEvent(afterChan, quoteUpdate);

  assert.equal(
    afterIndicators.snapshot.indicators.five_minute.ma.ma5[0].value,
    10.08,
  );
  assert.strictEqual(afterChan.snapshot.chan_analysis, chanUpdate.payload);
  assert.equal(afterQuote.snapshot.market.quote.latest_price, 10.2);
});

test("historical snapshot replaces the current projection without starting a Session stream", () => {
  const historical = {
    ...structuredClone(baseline),
    session: {
      session_id: "historical:sh.600000:2026-07-22",
      session_type: "historical",
      symbol: "sh.600000",
      trade_date: "2026-07-22",
      state: "ready",
      revision: 0,
    },
    replay: null,
  };
  const empty = createChartProjection(
    { ...structuredClone(baseline), session: undefined },
    { service_generation: fixture.service_generation },
  );
  const projection = applyWorkbenchSnapshot(empty, historical, {
    service_generation: fixture.service_generation,
    session_id: historical.session.session_id,
    revision: historical.session.revision,
  });

  assert.equal(projection.sessionId, "historical:sh.600000:2026-07-22");
  assert.equal(projection.snapshot.session.session_type, "historical");
  assert.equal(projection.snapshot.session.trade_date, "2026-07-22");
  assert.equal(projection.revision, 0);
  assert.equal(projection.rebaselineRequired, false);
});

function fiveMinuteBar(timestamp, extra = {}) {
  return {
    timestamp,
    open: extra.open ?? 10.0,
    high: extra.high ?? 10.2,
    low: extra.low ?? 9.9,
    close: extra.close ?? 10.1,
    volume: extra.volume ?? 100,
    amount: extra.amount ?? 1000,
    closed: extra.closed ?? false,
  };
}

function bars5mEvent(revision, bars) {
  return {
    ...marketUpdate,
    revision,
    event_type: "market_update",
    payload: { target: "bars_5m", bars, quote: null },
  };
}

test("bars_5m increments revise the dynamic bar, drop the previous bucket, and keep it after a late official close", () => {
  const snapshot = structuredClone(baseline);
  snapshot.market.bars_5m = [
    fiveMinuteBar("2026-07-22 09:35:00", {
      close: 10.1,
      volume: 100,
      amount: 1000,
      closed: false,
    }),
  ];
  let projection = createChartProjection(snapshot, fixture.initial_snapshot_event);

  projection = applyLiveChartEvent(
    projection,
    bars5mEvent(2, [
      fiveMinuteBar("2026-07-22 09:35:00", {
        high: 10.4,
        close: 10.3,
        volume: 350,
        amount: 3550,
        closed: false,
      }),
    ]),
  );
  assert.equal(projection.snapshot.market.bars_5m.length, 1);
  assert.equal(projection.snapshot.market.bars_5m[0].close, 10.3);
  assert.equal(projection.snapshot.market.bars_5m[0].closed, false);

  projection = applyLiveChartEvent(
    projection,
    bars5mEvent(3, [
      fiveMinuteBar("2026-07-22 09:40:00", {
        open: 11,
        high: 11,
        low: 11,
        close: 11,
        volume: 2,
        amount: 22,
        closed: false,
      }),
    ]),
  );
  assert.deepEqual(
    projection.snapshot.market.bars_5m.map((bar) => bar.timestamp),
    ["2026-07-22 09:40:00"],
  );
  assert.equal(projection.snapshot.market.bars_5m[0].closed, false);

  projection = applyLiveChartEvent(
    projection,
    bars5mEvent(4, [
      fiveMinuteBar("2026-07-22 09:35:00", {
        high: 10.5,
        low: 9.8,
        close: 10.4,
        volume: 900,
        amount: 9200,
        closed: true,
      }),
      fiveMinuteBar("2026-07-22 09:40:00", {
        open: 11,
        high: 11,
        low: 11,
        close: 11,
        volume: 2,
        amount: 22,
        closed: false,
      }),
    ]),
  );
  const byTimestamp = Object.fromEntries(
    projection.snapshot.market.bars_5m.map((bar) => [bar.timestamp, bar]),
  );
  assert.equal(byTimestamp["2026-07-22 09:35:00"].closed, true);
  assert.equal(byTimestamp["2026-07-22 09:35:00"].close, 10.4);
  assert.equal(byTimestamp["2026-07-22 09:40:00"].closed, false);
  assert.equal(
    projection.snapshot.market.bars_5m.filter((bar) => bar.closed === false).length,
    1,
  );
});

test("workbench_snapshot events never advance Live revision as increments", () => {
  // #155 review P1: invalid/full snapshots must replace via applySnapshot, not
  // applyLiveChartEvent. Defense in depth if App wrongly routes them here.
  const start = baselineProjection();
  const ignored = applyLiveChartEvent(start, {
    event_type: "workbench_snapshot",
    service_generation: start.serviceGeneration,
    session_id: start.sessionId,
    revision: start.revision + 1,
    payload: { timezone: "Asia/Shanghai" },
  });
  assert.equal(ignored, start);
  assert.equal(ignored.revision, start.revision);
  assert.equal(ignored.snapshot, start.snapshot);
});

test("Live projection merges bars_30m increments independently of 5m", () => {
  const event = {
    event_type: "market_update",
    service_generation: baselineProjection().serviceGeneration,
    session_id: baselineProjection().sessionId,
    revision: 2,
    payload: {
      target: "bars_30m",
      bars: [
        {
          timestamp: "2026-07-22 10:00:00",
          open: 10.0,
          high: 10.2,
          low: 9.9,
          close: 10.1,
          volume: 80000,
          amount: 808000,
          closed: true,
        },
      ],
      quote: null,
    },
  };
  const projected = applyLiveChartEvent(baselineProjection(), event);
  assert.equal(projected.snapshot.market.bars_30m.length, 1);
  assert.equal(projected.snapshot.market.bars_30m[0].timestamp, "2026-07-22 10:00:00");
  assert.equal(projected.snapshot.market.bars_5m.length, baseline.market.bars_5m.length);
});

function thirtyMinuteBar(timestamp, extra = {}) {
  return {
    timestamp,
    open: extra.open ?? 10.0,
    high: extra.high ?? 10.2,
    low: extra.low ?? 9.9,
    close: extra.close ?? 10.1,
    volume: extra.volume ?? 80000,
    amount: extra.amount ?? 808000,
    closed: extra.closed ?? false,
  };
}

function bars30mEvent(revision, bars) {
  return {
    ...marketUpdate,
    revision,
    event_type: "market_update",
    payload: { target: "bars_30m", bars, quote: null },
  };
}

test("bars_30m increments revise the dynamic bar, drop the previous bucket, and keep it after a late official close", () => {
  const snapshot = structuredClone(baseline);
  snapshot.market.bars_30m = [
    thirtyMinuteBar("2026-07-22 10:00:00", {
      close: 10.1,
      volume: 100,
      amount: 1000,
      closed: false,
    }),
  ];
  let projection = createChartProjection(snapshot, fixture.initial_snapshot_event);

  projection = applyLiveChartEvent(
    projection,
    bars30mEvent(2, [
      thirtyMinuteBar("2026-07-22 10:00:00", {
        high: 10.4,
        close: 10.3,
        volume: 350,
        amount: 3550,
        closed: false,
      }),
    ]),
  );
  assert.equal(projection.snapshot.market.bars_30m.length, 1);
  assert.equal(projection.snapshot.market.bars_30m[0].close, 10.3);
  assert.equal(projection.snapshot.market.bars_30m[0].closed, false);

  // Boundary advance: one-minute refresh publishes only the new bucket's
  // dynamic bar; the previous unclosed bar must be dropped.
  projection = applyLiveChartEvent(
    projection,
    bars30mEvent(3, [
      thirtyMinuteBar("2026-07-22 10:30:00", {
        open: 11,
        high: 11,
        low: 11,
        close: 11,
        volume: 2,
        amount: 22,
        closed: false,
      }),
    ]),
  );
  assert.deepEqual(
    projection.snapshot.market.bars_30m.map((bar) => bar.timestamp),
    ["2026-07-22 10:30:00"],
  );
  assert.equal(projection.snapshot.market.bars_30m[0].closed, false);

  // Official full payload (closed history + next dynamic bar) must not wipe
  // the dynamic bar and must close the previous bucket.
  projection = applyLiveChartEvent(
    projection,
    bars30mEvent(4, [
      thirtyMinuteBar("2026-07-22 10:00:00", {
        high: 10.5,
        low: 9.8,
        close: 10.4,
        volume: 900,
        amount: 9200,
        closed: true,
      }),
      thirtyMinuteBar("2026-07-22 10:30:00", {
        open: 11,
        high: 11,
        low: 11,
        close: 11,
        volume: 2,
        amount: 22,
        closed: false,
      }),
    ]),
  );
  const byTimestamp = Object.fromEntries(
    projection.snapshot.market.bars_30m.map((bar) => [bar.timestamp, bar]),
  );
  assert.equal(byTimestamp["2026-07-22 10:00:00"].closed, true);
  assert.equal(byTimestamp["2026-07-22 10:00:00"].close, 10.4);
  assert.equal(byTimestamp["2026-07-22 10:30:00"].closed, false);
  assert.equal(
    projection.snapshot.market.bars_30m.filter((bar) => bar.closed === false).length,
    1,
  );
});

test("Live projection replaces chan_analysis_30m without touching 5m analysis", () => {
  const replacement = {
    symbol: "600000.SH",
    timeframe: "30m",
    source: "live",
    engine: "czsc",
    engine_version: "0.10.12",
    parameters: {},
    fractals: [],
    strokes: [],
    segments: [],
    pivot_zones: [{ id: "30m-pivot" }],
    divergences: [],
    structure_alerts: [],
    signal_series: [],
    signal_events: [],
    signal_snapshots: [],
    candidate_point_events: [],
    candidate_buy_points: [],
    candidate_sell_points: [],
    plot_primitives: [],
    summary: [],
    warnings: [],
    meta: {},
  };
  const projected = applyLiveChartEvent(baselineProjection(), {
    event_type: "chan_analysis_30m_replaced",
    service_generation: baselineProjection().serviceGeneration,
    session_id: baselineProjection().sessionId,
    revision: 2,
    payload: replacement,
  });
  assert.equal(projected.snapshot.chan_analysis_30m.timeframe, "30m");
  assert.equal(projected.snapshot.chan_analysis_30m.pivot_zones[0].id, "30m-pivot");
  assert.equal(projected.snapshot.chan_analysis.timeframe, "5m");
});
