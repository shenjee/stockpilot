import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(testDir, "../contracts/fixtures/replay-speed-v1.json");

test("TypeScript-side tooling consumes the Replay v1.0 speed fixture", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  assert.equal(fixture.schema_version, "t0_replay_v1");
  assert.deepEqual(fixture.valid_speeds, [1, 2, 5, 10]);
  assert.equal(fixture.default_speed, 1);
  assert.equal(fixture.changed_event.payload.reason, "playback_speed_changed");
  assert.equal(fixture.changed_event.operation_id, undefined);
  assert.equal(fixture.snapshot.replay.playback_speed, fixture.changed_event.payload.playback_speed);
});
