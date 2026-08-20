/**
 * Shared live-five-minute-merge-v1 fixture: Renderer and Python must agree
 * step-by-step on bars_5m (#155 PR3).
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { mergeFiveMinuteBars } from "../renderer/src/charts/chart-projection.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(
  testDir,
  "../contracts/fixtures/live-five-minute-merge-v1.json",
);
const fixture = JSON.parse(await readFile(fixturePath, "utf8"));

test("live-five-minute-merge-v1 fixture shape is complete", () => {
  assert.equal(fixture.schema_version, "live_five_minute_merge_v1");
  assert.ok(Array.isArray(fixture.initial_bars_5m));
  assert.ok(fixture.steps.length >= 5);
  const ids = fixture.steps.map((step) => step.id);
  assert.deepEqual(ids, [
    "revise_dynamic_bucket",
    "replace_with_new_dynamic_bucket",
    "official_close_keeps_current_dynamic",
    "late_official_without_current_dynamic_drops_unclosed",
    "rebaseline_full_bars",
  ]);
});

test("Renderer mergeFiveMinuteBars matches every shared fixture step", () => {
  let bars = structuredClone(fixture.initial_bars_5m);

  for (const step of fixture.steps) {
    if (step.op === "merge") {
      bars = mergeFiveMinuteBars(bars, step.incoming);
    } else if (step.op === "replace") {
      bars = structuredClone(step.bars_5m);
    } else {
      assert.fail(`unknown op ${step.op} in step ${step.id}`);
    }
    assert.deepEqual(
      bars,
      step.expected_bars_5m,
      `Renderer mismatch at step ${step.id}`,
    );
  }
});
