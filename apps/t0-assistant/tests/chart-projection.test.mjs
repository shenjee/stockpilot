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
    resolve(testDir, "../contracts/fixtures/workbench-flow-v1.json"),
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
  const empty = structuredClone(baseline);
  delete empty.session;
  empty.market.bars_1m = [];
  empty.market.bars_5m = [];
  const selecting = beginChartSession(
    empty,
    fixture.service_generation,
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

  assert.equal(selecting.sessionId, null);
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
