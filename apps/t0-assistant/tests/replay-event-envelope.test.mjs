import assert from "node:assert/strict";
import test from "node:test";

import { replayEventEnvelope } from "../renderer/src/replay-event-envelope.mjs";

function baseEnvelope(overrides = {}) {
  return {
    schema_version: "t0_replay_v2",
    service_generation: 3,
    session_id: "replay-1",
    revision: 8,
    event_type: "workbench_snapshot",
    payload: { session: { revision: 8 } },
    ...overrides,
  };
}

test("replayEventEnvelope accepts integer generation and revision", () => {
  const envelope = replayEventEnvelope(baseEnvelope());
  assert.deepEqual(envelope, {
    event_type: "workbench_snapshot",
    operation_id: null,
    service_generation: 3,
    session_id: "replay-1",
    revision: 8,
    payload: { session: { revision: 8 } },
  });
});

test("replayEventEnvelope rejects missing outer revision", () => {
  const { revision: _revision, ...withoutRevision } = baseEnvelope();
  assert.equal(replayEventEnvelope(withoutRevision), null);
  assert.equal(replayEventEnvelope(baseEnvelope({ revision: null })), null);
  assert.equal(replayEventEnvelope(baseEnvelope({ revision: undefined })), null);
});

test("replayEventEnvelope rejects non-integer outer revision or generation", () => {
  assert.equal(replayEventEnvelope(baseEnvelope({ revision: 8.5 })), null);
  assert.equal(replayEventEnvelope(baseEnvelope({ revision: Number.NaN })), null);
  assert.equal(
    replayEventEnvelope(baseEnvelope({ service_generation: 3.2 })),
    null,
  );
  assert.equal(
    replayEventEnvelope(baseEnvelope({ service_generation: null })),
    null,
  );
});
