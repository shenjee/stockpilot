import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  REPLAY_SPEEDS,
  deriveReplayControls,
  marketClockLabel,
  marketTimeFromValue,
  replayFactsFromSnapshot,
  replayOperationMatches,
  replaySessionMatches,
} from "../renderer/src/replay-controls.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  await readFile(
    resolve(testDir, "../contracts/fixtures/replay-speed-v1.json"),
    "utf8",
  ),
);

test("Replay v1 controls expose exactly the frozen four speeds", () => {
  assert.deepEqual(REPLAY_SPEEDS, [1, 2, 5, 10]);
  assert.equal(Object.isFrozen(REPLAY_SPEEDS), true);
});

test("one-minute snapshot derives playback, progress, and step controls", () => {
  const facts = replayFactsFromSnapshot(fixture.snapshot);
  const controls = deriveReplayControls(facts);

  assert.equal(facts.sessionId, "replay-1");
  assert.equal(facts.playbackSpeed, 5);
  assert.equal(facts.currentValue > facts.startValue, true);
  assert.equal(facts.currentValue < facts.endValue, true);
  assert.deepEqual(controls, {
    active: true,
    playing: false,
    canTogglePlayback: true,
    canSeek: true,
    canStep: true,
    canChangeSpeed: true,
    stepLabel: "前进 1 分钟",
    granularityLabel: "1 分钟回放",
  });
  assert.equal(marketClockLabel(facts.currentTime), "10:23");
  assert.equal(marketTimeFromValue(facts.startValue), facts.startTime);
});

test("five-minute fallback changes labels and disables step at sequence end", () => {
  const fallback = structuredClone(fixture.snapshot);
  fallback.replay.granularity = "five_minute";
  fallback.replay.step_seconds = 300;
  fallback.replay.next_bar_time = null;
  fallback.market.bars_1m = [];
  const facts = replayFactsFromSnapshot(fallback);

  assert.deepEqual(deriveReplayControls(facts), {
    active: true,
    playing: false,
    canTogglePlayback: true,
    canSeek: true,
    canStep: false,
    canChangeSpeed: true,
    stepLabel: "前进 5 分钟",
    granularityLabel: "5 分钟回放",
  });
});

test("busy cursor operation disables all conflicting controls", () => {
  const controls = deriveReplayControls(
    replayFactsFromSnapshot(fixture.snapshot),
    { busy: true },
  );
  assert.equal(controls.canTogglePlayback, false);
  assert.equal(controls.canSeek, false);
  assert.equal(controls.canStep, false);
  assert.equal(controls.canChangeSpeed, false);
});

test("invalid or non-active Replay snapshots do not become interactive", () => {
  const loading = structuredClone(fixture.snapshot);
  loading.session.state = "loading";
  assert.equal(replayFactsFromSnapshot(loading), null);
  assert.equal(deriveReplayControls(null).active, false);
});

test("late Replay sessions and unrelated operations never match active identities", () => {
  assert.equal(replaySessionMatches(null, "replay-old"), false);
  assert.equal(replaySessionMatches("replay-new", "replay-old"), false);
  assert.equal(replaySessionMatches("replay-new", "replay-new"), true);

  assert.equal(replayOperationMatches(null, "operation-old"), false);
  assert.equal(
    replayOperationMatches("operation-step", "operation-playback"),
    false,
  );
  assert.equal(
    replayOperationMatches("operation-step", "operation-step"),
    true,
  );
});
