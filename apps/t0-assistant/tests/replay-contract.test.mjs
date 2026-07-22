import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(testDir, "../contracts/fixtures/replay-speed-v1.json");
const appSchemaPath = resolve(testDir, "../contracts/app-v1.schema.json");

test("TypeScript-side tooling consumes the Replay v1.0 speed fixture", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  assert.equal(fixture.schema_version, "t0_replay_v1");
  assert.deepEqual(fixture.valid_speeds, [1, 2, 5, 10]);
  assert.equal(fixture.default_speed, 1);
  assert.equal(fixture.changed_event.payload.reason, "playback_speed_changed");
  assert.equal(fixture.changed_event.operation_id, undefined);
  assert.equal(fixture.snapshot.replay.playback_speed, fixture.changed_event.payload.playback_speed);
});

test("app v1 imports Replay v1 and keeps Replay commands out of its enum", async () => {
  const schema = JSON.parse(await readFile(appSchemaPath, "utf8"));
  const commands = schema.$defs.command_request.properties.command.enum;
  assert.equal(schema.$defs.replay_event_envelope.$ref.includes("t0-replay-v1.schema.json"), true);
  assert.equal(commands.includes("set_replay_speed"), false);
  assert.equal(commands.includes("select_security"), true);
  assert.equal(commands.includes("create_trade"), true);
  assert.equal(commands.includes("save_preferences"), true);
});
