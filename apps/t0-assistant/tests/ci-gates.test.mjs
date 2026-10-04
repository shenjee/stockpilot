import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const runner = fileURLToPath(new URL("./run-required-tests.mjs", import.meta.url));
const app = fileURLToPath(new URL("../", import.meta.url));

function run(...args) {
  return spawnSync(process.execPath, [runner, ...args], { cwd: app, encoding: "utf8" });
}

test("required Node targets reject missing, empty, skipped, and failed tests", () => {
  const directory = mkdtempSync(join(tmpdir(), "t0-ci-gates-"));
  try {
    assert.notEqual(run("node").status, 0);
    assert.notEqual(run("node", join(directory, "missing.mjs")).status, 0);
    for (const source of [
      "",
      "import test from 'node:test'; test.skip('skipped', () => {});",
      "import test from 'node:test'; test('failure', () => { throw Error('sentinel'); });",
    ]) {
      const target = join(directory, "sample.mjs");
      writeFileSync(target, source);
      const result = run("node", target);
      assert.notEqual(result.status, 0, result.stdout + result.stderr);
    }
    const passing = join(directory, "passing.mjs");
    writeFileSync(passing, "import test from 'node:test'; test('real test', () => {});");
    const result = run("node", passing);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /discovered=1, passed=1/);
    const empty = join(directory, "empty.mjs");
    writeFileSync(empty, "");
    assert.notEqual(run("node", passing, empty).status, 0, "a passing file must not hide an empty target");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("required React target rejects a nonexistent test filter", () => {
  const result = run("vitest", "__issue_185_missing_test__");
  assert.notEqual(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout + result.stderr, /"numTotalTests":0|no executed tests/);
});
