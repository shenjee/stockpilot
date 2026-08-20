import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  ChartGroupKind,
  createChartGroupModel,
  tryCreateChartGroupModel,
} from "../renderer/src/charts/chart-model.mjs";
import {
  chartContractApplicationError,
  chartEnvelopeApplicationError,
  inspectWorkbenchSnapshotCandidate,
} from "../renderer/src/charts/workbench-snapshot-guard.mjs";
import { hasWorkbenchSnapshotEnvelope } from "../renderer/src/workbench-presenter.mjs";
import { LiveProjectionController } from "../renderer/src/charts/live-projection-controller.mjs";
import { createChartProjection } from "../renderer/src/charts/chart-projection.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(
    resolve(testDir, "../contracts/fixtures/workbench-flow-v2.json"),
    "utf8",
  ),
).initial_snapshot_event.payload;

test("hasWorkbenchSnapshotEnvelope is shallow only", () => {
  assert.equal(hasWorkbenchSnapshotEnvelope(fixture), true);
  const unaligned = structuredClone(fixture);
  unaligned.indicators.five_minute.macd.dif.push({
    timestamp: "2099-01-01 10:00:00",
    value: 1,
  });
  // Envelope still passes; deep probe must fail.
  assert.equal(hasWorkbenchSnapshotEnvelope(unaligned), true);
  const inspected = inspectWorkbenchSnapshotCandidate(unaligned);
  assert.equal(inspected.ok, false);
  assert.equal(inspected.reason, "contract");
});

test("inspectWorkbenchSnapshotCandidate accepts a renderable fixture", () => {
  const inspected = inspectWorkbenchSnapshotCandidate(fixture);
  assert.equal(inspected.ok, true);
  assert.equal(
    inspected.snapshot.session.session_id,
    fixture.session.session_id,
  );
});

test("tryCreateChartGroupModel preserves strict createChartGroupModel errors", () => {
  const unaligned = structuredClone(fixture);
  const barTs = unaligned.market.bars_1m[0]?.timestamp;
  assert.ok(barTs);
  unaligned.indicators.one_minute.vwap.push({
    timestamp: "2099-01-01 10:00:00",
    value: 10.3,
  });
  assert.throws(
    () => createChartGroupModel(unaligned, ChartGroupKind.ONE_MINUTE),
    /without a matching bar/,
  );
  const soft = tryCreateChartGroupModel(unaligned, ChartGroupKind.ONE_MINUTE);
  assert.equal(soft.ok, false);
  assert.match(
    String(soft.error?.message ?? soft.error),
    /without a matching bar/,
  );
});

test("contract failure keeps last good Live projection and requests rebaseline", () => {
  const good = createChartProjection(fixture, {
    service_generation: 1,
    session_id: fixture.session.session_id,
    revision: fixture.session.revision,
  });
  const live = new LiveProjectionController(good);
  const bad = structuredClone(fixture);
  bad.indicators.five_minute.macd.dif.push({
    timestamp: "2099-01-01 10:00:00",
    value: 1,
  });
  const inspected = inspectWorkbenchSnapshotCandidate(bad);
  assert.equal(inspected.ok, false);
  // Ingress must not apply the bad snapshot.
  assert.equal(live.projection.snapshot, good.snapshot);
  assert.equal(live.requestRebaseline(), true);
  assert.equal(live.projection.rebaselineRequired, true);
  assert.equal(live.projection.snapshot, good.snapshot);
  const error = chartContractApplicationError(inspected.error);
  assert.equal(error.error_code, "chart_contract_failed");
  assert.match(error.message, /已保留上一幅有效图形/);
});

test("chartEnvelopeApplicationError retains last chart and is retryable", () => {
  const error = chartEnvelopeApplicationError();
  assert.equal(error.error_code, "chart_envelope_failed");
  assert.equal(error.retryable, true);
  assert.match(error.message, /已保留上一幅有效图形/);
});
