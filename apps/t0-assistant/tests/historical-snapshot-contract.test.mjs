import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(
  testDir,
  "../contracts/fixtures/historical-snapshot-flow-v1.json",
);

test("renderer-side tooling consumes the canonical historical snapshot fixture", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const request = fixture.historical_snapshot_request;
  const response = fixture.historical_snapshot_response;
  const snapshot = response.data;

  assert.equal(fixture.schema_version, "t0_app_v1");
  assert.equal(request.schema_version, "t0_app_v1");
  assert.equal(request.command, "get_historical_snapshot");
  assert.equal(request.session_id, null);

  assert.equal(response.accepted, true);
  assert.equal(response.operation_id, null);
  assert.equal(response.error, null);
  assert.equal(snapshot.session.session_type, "historical");
  assert.equal(snapshot.session.state, "ready");
  assert.equal(snapshot.session.trade_date, request.payload.trade_date);
  assert.equal(snapshot.replay, null);
});

test("historical failure fixtures preserve synchronous error semantics", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const expectedErrors = {
    historical_data_unavailable_response: {
      error_code: "historical_data_unavailable",
      category: "data",
    },
    service_unavailable_response: {
      error_code: "service_unavailable",
      category: "service",
    },
  };

  for (const [fixtureName, expected] of Object.entries(expectedErrors)) {
    const response = fixture[fixtureName];
    assert.equal(response.accepted, false);
    assert.equal(response.operation_id, null);
    assert.equal(response.data, null);
    assert.equal(response.error.error_code, expected.error_code);
    assert.equal(response.error.category, expected.category);
    assert.equal(response.error.affected_capability, "historical_chart");
    assert.equal(response.error.retryable, true);
  }
});
