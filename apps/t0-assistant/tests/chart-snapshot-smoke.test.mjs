// Live / Replay 快照冒烟：验证快照中的 chan_analysis 结构数据能进入图表并显示。
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  ChartGroupKind,
  createChartGroupModel,
} from "../renderer/src/charts/chart-model.mjs";
import { createFakeSafeBridge } from "./fake-safe-bridge.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const workbenchFixture = JSON.parse(
  await readFile(resolve(testDir, "../contracts/fixtures/workbench-flow-v1.json"), "utf8"),
);
const replayFixture = JSON.parse(
  await readFile(resolve(testDir, "../contracts/fixtures/replay-speed-v1.json"), "utf8"),
);

// 补全 chan_analysis 的最小有效数据，用于冒烟测试。
function withChanAnalysis(payload) {
  return {
    ...payload,
    chan_analysis: {
      strokes: [
        {
          start_timestamp: "2026-07-22 09:35:00",
          end_timestamp: "2026-07-22 09:50:00",
          start_price: 10.0,
          end_price: 10.15,
          confirmed: true,
        },
        {
          start_timestamp: "2026-07-22 09:50:00",
          end_timestamp: "2026-07-22 10:00:00",
          start_price: 10.15,
          end_price: 10.3,
          confirmed: false,
        },
      ],
      pivot_zones: [
        {
          start_timestamp: "2026-07-22 09:35:00",
          end_timestamp: "2026-07-22 09:55:00",
          high: 10.25,
          low: 10.1,
          active: true,
        },
      ],
      candidate_buy_points: [
        {
          id: "bp-001",
          point_type: "first_buy",
          timestamp: "2026-07-22 09:55:00",
          price: 10.17,
          reference_id: "s1",
          confirmed: true,
          reason: "downward_stroke_inside_pivot_zone",
        },
      ],
      candidate_sell_points: [
        {
          id: "sp-001",
          point_type: "first_sell",
          timestamp: "2026-07-22 10:00:00",
          price: 10.3,
          reference_id: "s2",
          confirmed: true,
          reason: "upward_stroke_extension",
        },
      ],
    },
  };
}

function withBars(payload) {
  return {
    ...payload,
    market: {
      ...payload.market,
      bars_5m: [
        { timestamp: "2026-07-22 09:35:00", open: 10.0, high: 10.2, low: 9.9, close: 10.1, volume: 50000, amount: 500000, closed: true },
        { timestamp: "2026-07-22 09:40:00", open: 10.1, high: 10.3, low: 10.0, close: 10.2, volume: 60000, amount: 600000, closed: true },
        { timestamp: "2026-07-22 09:45:00", open: 10.2, high: 10.25, low: 10.15, close: 10.18, volume: 55000, amount: 550000, closed: true },
        { timestamp: "2026-07-22 09:50:00", open: 10.18, high: 10.22, low: 10.1, close: 10.15, volume: 52000, amount: 520000, closed: true },
        { timestamp: "2026-07-22 09:55:00", open: 10.15, high: 10.18, low: 10.12, close: 10.17, volume: 48000, amount: 480000, closed: true },
        { timestamp: "2026-07-22 10:00:00", open: 10.17, high: 10.35, low: 10.17, close: 10.3, volume: 70000, amount: 700000, closed: true },
      ],
    },
    indicators: {
      ...payload.indicators,
      five_minute: {
        ma: { ma5: [], ma10: [], ma20: [], ma30: [], ma60: [] },
        volume: { values: [], ma5: [], ma10: [] },
        macd: { fast_period: 12, slow_period: 26, signal_period: 9, dif: [], dea: [], histogram: [] },
      },
    },
    // Replay 快照若带有 current_time，chan_analysis 中的 end_timestamp 必须 <= current_time
    // 才能不被截断。这里把 current_time 推到足够晚，保证所有结构数据可见。
    replay: payload.replay
      ? { ...payload.replay, current_time: "2026-07-22 15:00:00" }
      : payload.replay,
  };
}

test("Live snapshot chan_analysis produces non-empty pivot zones and CZSC markers", async () => {
  const enriched = withBars(withChanAnalysis(workbenchFixture.initial_snapshot_event.payload));
  const { bridge } = createFakeSafeBridge({
    ...workbenchFixture,
    initial_snapshot_event: {
      ...workbenchFixture.initial_snapshot_event,
      payload: enriched,
    },
  });

  const response = await bridge.getLiveSnapshot({
    schema_version: "t0_app_v1",
    request_id: "smoke-live-1",
    command: "get_live_snapshot",
    session_id: workbenchFixture.session_id,
    payload: {},
  });

  const snapshot = response.data;
  assert.ok(snapshot.chan_analysis, "Live snapshot must include chan_analysis");
  assert.ok(
    Array.isArray(snapshot.chan_analysis.strokes) && snapshot.chan_analysis.strokes.length > 0,
    "Live snapshot chan_analysis.strokes must be non-empty",
  );
  assert.ok(
    Array.isArray(snapshot.chan_analysis.pivot_zones) && snapshot.chan_analysis.pivot_zones.length > 0,
    "Live snapshot chan_analysis.pivot_zones must be non-empty",
  );
  assert.ok(
    Array.isArray(snapshot.chan_analysis.candidate_buy_points) &&
      snapshot.chan_analysis.candidate_buy_points.length > 0,
    "Live snapshot chan_analysis.candidate_buy_points must be non-empty",
  );
  assert.ok(
    Array.isArray(snapshot.chan_analysis.candidate_sell_points) &&
      snapshot.chan_analysis.candidate_sell_points.length > 0,
    "Live snapshot chan_analysis.candidate_sell_points must be non-empty",
  );

  // CandidatePoint 契约校验
  for (const point of snapshot.chan_analysis.candidate_buy_points) {
    assert.ok(point.id, "Live candidate_buy_point must have id");
    assert.ok(point.reference_id, "Live candidate_buy_point must have reference_id");
    assert.equal(typeof point.confirmed, "boolean");
    assert.ok(point.reason, "Live candidate_buy_point must have reason");
  }
  for (const point of snapshot.chan_analysis.candidate_sell_points) {
    assert.ok(point.id, "Live candidate_sell_point must have id");
    assert.ok(point.reference_id, "Live candidate_sell_point must have reference_id");
    assert.equal(typeof point.confirmed, "boolean");
    assert.ok(point.reason, "Live candidate_sell_point must have reason");
  }

  const model = createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE, {
    strokes: true,
    pivot_zones: true,
  });
  assert.equal(model.strokes.length, snapshot.chan_analysis.strokes.length);
  assert.equal(model.pivotZones.length, snapshot.chan_analysis.pivot_zones.length);
  assert.ok(
    model.czscMarkers.some((m) => m.side === "buy" && m.label.includes("1B")),
    "Live model must produce 1B buy marker",
  );
  assert.ok(
    model.czscMarkers.some((m) => m.side === "sell" && m.label.includes("1S")),
    "Live model must produce 1S sell marker",
  );
});

test("Replay snapshot chan_analysis produces non-empty pivot zones and CZSC markers", async () => {
  const enriched = withBars(withChanAnalysis(replayFixture.snapshot));
  const { bridge } = createFakeSafeBridge(workbenchFixture, {
    replayFixture: { ...replayFixture, snapshot: enriched },
  });

  const response = await bridge.getReplaySnapshot({
    schema_version: "t0_replay_v1",
    request_id: "smoke-replay-1",
    session_id: "replay-1",
  });

  const snapshot = response;
  assert.ok(snapshot.chan_analysis, "Replay snapshot must include chan_analysis");
  assert.ok(
    Array.isArray(snapshot.chan_analysis.strokes) && snapshot.chan_analysis.strokes.length > 0,
    "Replay snapshot chan_analysis.strokes must be non-empty",
  );
  assert.ok(
    Array.isArray(snapshot.chan_analysis.pivot_zones) && snapshot.chan_analysis.pivot_zones.length > 0,
    "Replay snapshot chan_analysis.pivot_zones must be non-empty",
  );
  assert.ok(
    Array.isArray(snapshot.chan_analysis.candidate_buy_points) &&
      snapshot.chan_analysis.candidate_buy_points.length > 0,
    "Replay snapshot chan_analysis.candidate_buy_points must be non-empty",
  );
  assert.ok(
    Array.isArray(snapshot.chan_analysis.candidate_sell_points) &&
      snapshot.chan_analysis.candidate_sell_points.length > 0,
    "Replay snapshot chan_analysis.candidate_sell_points must be non-empty",
  );

  // CandidatePoint 契约校验
  for (const point of snapshot.chan_analysis.candidate_buy_points) {
    assert.ok(point.id, "Replay candidate_buy_point must have id");
    assert.ok(point.reference_id, "Replay candidate_buy_point must have reference_id");
    assert.equal(typeof point.confirmed, "boolean");
    assert.ok(point.reason, "Replay candidate_buy_point must have reason");
  }
  for (const point of snapshot.chan_analysis.candidate_sell_points) {
    assert.ok(point.id, "Replay candidate_sell_point must have id");
    assert.ok(point.reference_id, "Replay candidate_sell_point must have reference_id");
    assert.equal(typeof point.confirmed, "boolean");
    assert.ok(point.reason, "Replay candidate_sell_point must have reason");
  }

  const model = createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE, {
    strokes: true,
    pivot_zones: true,
  });
  assert.equal(model.strokes.length, snapshot.chan_analysis.strokes.length);
  assert.equal(model.pivotZones.length, snapshot.chan_analysis.pivot_zones.length);
  assert.ok(
    model.czscMarkers.some((m) => m.side === "buy" && m.label.includes("1B")),
    "Replay model must produce 1B buy marker",
  );
  assert.ok(
    model.czscMarkers.some((m) => m.side === "sell" && m.label.includes("1S")),
    "Replay model must produce 1S sell marker",
  );
});
