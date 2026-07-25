import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { RotatingTechnicalLog } from "../electron/technical-log.mjs";

test("technical log is chronological, rotating, read-only consumable, and redacted", async () => {
  const directory = await mkdtemp(join(tmpdir(), "stockpilot-log-"));
  const path = join(directory, "technical.log");
  const log = new RotatingTechnicalLog(path, { maxBytes: 90, maxFiles: 2 });

  await log.write(
    "stderr",
    "first Bearer secret-token",
    new Date("2026-07-25T01:00:00.000Z"),
  );
  await log.write(
    "stdout",
    "second token=private-value",
    new Date("2026-07-25T01:01:00.000Z"),
  );
  await log.write(
    "stderr",
    "third diagnostic",
    new Date("2026-07-25T01:02:00.000Z"),
  );

  const review = await log.read();
  assert.equal(review.includes("secret-token"), false);
  assert.equal(review.includes("private-value"), false);
  assert.match(review, /Bearer \[REDACTED\]/);
  assert.match(review, /token=\[REDACTED\]/);
  assert.ok(review.indexOf("first") < review.indexOf("third"));
  assert.match(await readFile(path, "utf8"), /third diagnostic/);
});

test("a failed log operation does not poison later writes", async () => {
  const directory = await mkdtemp(join(tmpdir(), "stockpilot-log-recovery-"));
  const path = join(directory, "technical.log");
  const log = new RotatingTechnicalLog(path);
  log.pending = Promise.reject(new Error("previous disk failure"));

  await log.write("stdout", "recovered");

  assert.match(await log.read(), /recovered/);
});

test("technical log redacts common credentials in plain text and JSON", async () => {
  const directory = await mkdtemp(join(tmpdir(), "stockpilot-log-secrets-"));
  const log = new RotatingTechnicalLog(join(directory, "technical.log"));

  await log.write(
    "stderr",
    'password=hunter2 secret:private api_key = key-123 {"credential":"json-secret"}',
  );

  const review = await log.read();
  for (const secret of ["hunter2", "private", "key-123", "json-secret"]) {
    assert.equal(review.includes(secret), false);
  }
  assert.equal(review.match(/\[REDACTED\]/g)?.length, 4);
});
