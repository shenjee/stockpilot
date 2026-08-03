// Live / Replay 快照冒烟：验证完整契约快照中的 chan_analysis 结构数据能流经
// bridge -> createChartGroupModel -> pivotZones / czscMarkers 并保留下来。
//
// 本测试不复用大段局部 bars/chan fixture，而是以 chart-groups-v1.json 作为对齐的
// bars_5m + indicators + 4 个 chan 数组的单一来源，合并进 workbench / replay fixture
// 的完整快照契约（保留 chan_analysis 所有必需字段，只替换 strokes / pivot_zones /
// candidate_buy_points / candidate_sell_points）。Live 与 Replay 的时间戳均与各自
// bars_5m 对齐；Replay 的 current_time 取 session 范围内的合法时点，验证未来结构被
// current_time 正确过滤（Issue #134 / PR #135 review）。
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

// 只替换 chan_analysis 的 4 个结构数组，保留原 fixture 的完整契约字段
// （symbol / timeframe / source / engine / fractals / segments / divergences /
//  structure_alerts / signal_* / candidate_point_events / plot_primitives /
//  summary / warnings / meta），不退化为只有 4 个字段的局部 AnalysisResult。
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
  // 数据没有因时间戳不匹配被过滤：pivot zone / 候选点数量与快照契约一致。
  assert.equal(
    model.pivotZones.length,
    snapshot.chan_analysis.pivot_zones.length,
    `${label}: pivotZones must match snapshot contract (no timestamp filtering)`,
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
  // Live 不带 replay，createChartGroupModel 不做 current_time 截断。
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

test("Replay snapshot chan_analysis respects current_time truncation while keeping visible structure", async () => {
  // Replay session 范围与对齐的 bars_5m (2026-07-22) 一致；current_time 取 session
  // 范围内的合法时点 10:00:00，使 pivot zone (end 09:55) / 买点 (09:55) / 卖点 (10:00)
  // 保留，而 end_timestamp 越过 current_time 的笔（10:05）被过滤，验证未来结构截断。
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

  // current_time 截断：bars_5m 中 10:00 之后的 bar 被丢弃，最后一根为 10:00。
  assert.equal(
    model.timestamps.at(-1),
    "2026-07-22 10:00:00",
    "Replay bars must be truncated at current_time",
  );
  // 未来结构被过滤：第二笔 end_timestamp 10:05 > current_time 10:00 -> 丢弃，
  // 仅保留第一笔（end 09:50）。这验证 Replay 不会泄漏 current_time 之后的结构。
  assert.equal(
    model.strokes.length,
    snapshot.chan_analysis.strokes.filter(
      (s) => s.end_timestamp <= replayBlock.current_time,
    ).length,
    "Replay strokes must be truncated by current_time",
  );
  assert.ok(model.strokes.length > 0, "Replay must keep at least one stroke");
  // 可见结构：pivot zone / 买点 / 卖点 end/timestamp 均 <= current_time，保留。
  assertModelStructureLayer(model, snapshot, "Replay");
});
