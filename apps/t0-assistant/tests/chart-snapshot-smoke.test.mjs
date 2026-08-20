// Live / Replay 快照冒烟：验证完整契约快照中的 chan_analysis 结构数据能流经
// bridge -> createChartGroupModel -> pivotZones / czscMarkers 并保留下来。
//
// 本测试不复用大段局部 bars/chan fixture，而是以 chart-groups-v1.json 作为对齐的
// bars_5m + indicators + 4 个 chan 数组的单一来源，合并进 workbench / replay fixture
// 的完整快照契约（保留 chan_analysis 所有必需字段，只替换 strokes / pivot_zones /
// candidate_buy_points / candidate_sell_points）。Live 与 Replay 的时间戳均与各自
// bars_5m 对齐。Issue #154：createChartGroupModel 不再按 replay.current_time 截断，
// Live / Replay 对同一载荷投影一致。
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
const [workbenchFixture, replayFixture, chartGroups] = await Promise.all([
  readFile(resolve(testDir, "../contracts/fixtures/workbench-flow-v1.json"), "utf8")
    .then(JSON.parse),
  readFile(resolve(testDir, "../contracts/fixtures/replay-speed-v1.json"), "utf8")
    .then(JSON.parse),
  readFile(resolve(testDir, "../contracts/fixtures/chart-groups-v1.json"), "utf8")
    .then(JSON.parse),
]);

// chart-groups-v1.json 提供对齐的 bars_5m + five_minute 指标 + 4 个 chan 数组，
// 作为 Live / Replay 快照共用的结构化数据源（避免重复维护两份大段 fixture）。
const ALIGNED_BARS_5M = chartGroups.market.bars_5m;
const ALIGNED_FIVE_MINUTE_INDICATORS = chartGroups.indicators.five_minute;
const ALIGNED_STROKES = chartGroups.chan_analysis.strokes;
const ALIGNED_PIVOT_ZONES = chartGroups.chan_analysis.pivot_zones;
const ALIGNED_BUY_POINTS = chartGroups.chan_analysis.candidate_buy_points;
const ALIGNED_SELL_POINTS = chartGroups.chan_analysis.candidate_sell_points;
const ALIGNED_DIVERGENCES = chartGroups.chan_analysis.divergences;

// 只替换 chan_analysis 的结构数组，保留原 fixture 的完整契约字段
// （symbol / timeframe / source / engine / fractals / segments / divergences /
//  structure_alerts / signal_* / candidate_point_events / plot_primitives /
//  summary / warnings / meta），不退化为只有局部字段的 AnalysisResult。
function withAlignedChanAnalysis(payload) {
  const base = payload.chan_analysis ?? {};
  return {
    ...payload,
    chan_analysis: {
      ...base,
      strokes: ALIGNED_STROKES,
      pivot_zones: ALIGNED_PIVOT_ZONES,
      candidate_buy_points: ALIGNED_BUY_POINTS,
      candidate_sell_points: ALIGNED_SELL_POINTS,
      divergences: ALIGNED_DIVERGENCES,
    },
  };
}

// 用对齐的 bars_5m + five_minute 指标替换原 fixture 的对应字段，保证 chan 结构
// 的时间戳与 bars_5m 完全对齐（不会被 timestampSet 过滤掉）。
function withAlignedMarketAndIndicators(payload) {
  return {
    ...payload,
    market: {
      ...payload.market,
      bars_5m: ALIGNED_BARS_5M,
    },
    indicators: {
      ...payload.indicators,
      five_minute: ALIGNED_FIVE_MINUTE_INDICATORS,
    },
  };
}

// CandidatePoint 契约校验（issue #135 code review）。
function assertCandidatePointContract(point, label) {
  assert.equal(typeof point.id, "string", `${label}.id must be string`);
  assert.equal(typeof point.point_type, "string", `${label}.point_type must be string`);
  assert.equal(typeof point.timestamp, "string", `${label}.timestamp must be string`);
  assert.equal(typeof point.price, "number", `${label}.price must be number`);
  assert.equal(typeof point.reference_id, "string", `${label}.reference_id must be string`);
  assert.equal(typeof point.confirmed, "boolean", `${label}.confirmed must be boolean`);
  assert.equal(typeof point.reason, "string", `${label}.reason must be string`);
}

function assertModelStructureLayer(model, snapshot, label) {
  assert.ok(
    model.pivotZones.length > 0,
    `${label}: pivotZones must be non-empty`,
  );
  assert.ok(
    model.czscMarkers.some((m) => m.side === "buy" && m.label.includes("1B")),
    `${label}: model must produce 1B buy marker`,
  );
  assert.ok(
    model.czscMarkers.some((m) => m.side === "sell" && m.label.includes("1S")),
    `${label}: model must produce 1S sell marker`,
  );
  assert.ok(
    model.divergenceMarkers.some(
      (m) => m.label === "Bull Div" && m.divergenceType === "bullish",
    ),
    `${label}: model must produce Bull Div marker`,
  );
  assert.ok(
    model.divergenceMarkers.some(
      (m) => m.label === "Bear Div" && m.divergenceType === "bearish",
    ),
    `${label}: model must produce Bear Div marker`,
  );
  // 数据没有因时间戳不匹配被过滤：pivot zone / 候选点 / 背驰数量与快照契约一致。
  assert.equal(
    model.pivotZones.length,
    snapshot.chan_analysis.pivot_zones.length,
    `${label}: pivotZones must match snapshot contract (no timestamp filtering)`,
  );
  assert.equal(
    model.divergenceMarkers.length,
    snapshot.chan_analysis.divergences.length,
    `${label}: divergenceMarkers must match snapshot divergences count`,
  );
  const snapshotBuyCount = snapshot.chan_analysis.candidate_buy_points.length;
  const snapshotSellCount = snapshot.chan_analysis.candidate_sell_points.length;
  assert.equal(
    model.czscMarkers.filter((m) => m.side === "buy").length,
    snapshotBuyCount,
    `${label}: buy markers must match snapshot candidate_buy_points count`,
  );
  assert.equal(
    model.czscMarkers.filter((m) => m.side === "sell").length,
    snapshotSellCount,
    `${label}: sell markers must match snapshot candidate_sell_points count`,
  );
}

test("Live snapshot chan_analysis flows through bridge into pivot zones and CZSC markers", async () => {
  const liveSnapshot = withAlignedChanAnalysis(
    withAlignedMarketAndIndicators(
      workbenchFixture.initial_snapshot_event.payload,
    ),
  );
  // Live / Replay 均不按 replay.current_time 截断；本用例验证无 replay 载荷路径。
  assert.equal(liveSnapshot.replay, null);

  const { bridge } = createFakeSafeBridge({
    ...workbenchFixture,
    initial_snapshot_event: {
      ...workbenchFixture.initial_snapshot_event,
      payload: liveSnapshot,
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
  for (const point of snapshot.chan_analysis.candidate_buy_points) {
    assertCandidatePointContract(point, "Live candidate_buy_point");
  }
  for (const point of snapshot.chan_analysis.candidate_sell_points) {
    assertCandidatePointContract(point, "Live candidate_sell_point");
  }

  const model = createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE, {
    strokes: true,
    pivot_zones: true,
  });
  assert.equal(model.strokes.length, snapshot.chan_analysis.strokes.length);
  assertModelStructureLayer(model, snapshot, "Live");
});

test("Replay snapshot chan_analysis projects full aligned structure without current_time clipping", async () => {
  // Issue #154：Replay 元数据只给控件/进度；Chart Model 忠实投影对齐载荷。
  const replayBlock = {
    granularity: "five_minute",
    current_time: "2026-07-22 10:00:00",
    next_bar_time: "2026-07-22 10:05:00",
    start_time: "2026-07-22 09:30:00",
    end_time: "2026-07-22 15:00:00",
    playing: false,
    playback_speed: 1,
    step_seconds: 300,
  };
  const replaySnapshot = {
    ...withAlignedChanAnalysis(
      withAlignedMarketAndIndicators(replayFixture.snapshot),
    ),
    replay: replayBlock,
  };

  const { bridge } = createFakeSafeBridge(workbenchFixture, {
    replayFixture: { ...replayFixture, snapshot: replaySnapshot },
  });

  const snapshot = await bridge.getReplaySnapshot({
    schema_version: "t0_replay_v1",
    request_id: "smoke-replay-1",
    session_id: "replay-1",
  });

  assert.ok(snapshot.chan_analysis, "Replay snapshot must include chan_analysis");
  assert.equal(snapshot.replay?.current_time, "2026-07-22 10:00:00");
  for (const point of snapshot.chan_analysis.candidate_buy_points) {
    assertCandidatePointContract(point, "Replay candidate_buy_point");
  }
  for (const point of snapshot.chan_analysis.candidate_sell_points) {
    assertCandidatePointContract(point, "Replay candidate_sell_point");
  }

  const model = createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE, {
    strokes: true,
    pivot_zones: true,
  });

  assert.equal(
    model.timestamps.at(-1),
    snapshot.market.bars_5m.at(-1).timestamp,
    "Replay bars must match snapshot without current_time clipping",
  );
  assert.equal(
    model.strokes.length,
    snapshot.chan_analysis.strokes.length,
    "Replay strokes must match snapshot without current_time clipping",
  );
  assert.ok(model.strokes.length > 0, "Replay must keep at least one stroke");
  assertModelStructureLayer(model, snapshot, "Replay");
});
