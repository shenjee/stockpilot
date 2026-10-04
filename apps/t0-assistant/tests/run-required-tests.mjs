// Execute each Node file in its own process so an empty file cannot be counted
// as a passing file-level test by `node --test`. Vitest uses its JSON results.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const [mode, ...targets] = process.argv.slice(2);
console.log(`Node ${process.version} (${process.execPath}), ${process.platform}/${process.arch}`);
if (!["node", "vitest"].includes(mode) || (mode === "node" && targets.length === 0)) {
  console.error("Usage: run-required-tests.mjs node <files...> | vitest [filters...]");
  process.exit(1);
}

function execute(args) {
  const env = { ...process.env };
  // Nested gate regression tests inherit Node's private binary-reporting mode.
  // These independent children must use the reporter selected in their args.
  delete env.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, args, { env, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
  process.stdout.write(result.stdout ?? "");
  process.stderr.write(result.stderr ?? "");
  if (result.error) console.error(result.error);
  if (result.status !== 0) process.exit(result.status || 1);
  return result.stdout;
}

function requireCount(target, count, passed) {
  console.log(`Required target: ${target}; discovered=${count}, passed=${passed}`);
  if (!(count > 0 && passed > 0)) {
    console.error(`Required target has no executed tests: ${target}`);
    process.exit(1);
  }
}

if (mode === "node") {
  for (const target of targets) {
    console.log(`Required target: ${target}`);
    const output = execute(["--test-reporter=tap", target]);
    const count = Number(output.match(/^# tests (\d+)$/m)?.[1] ?? 0);
    const passed = Number(output.match(/^# pass (\d+)$/m)?.[1] ?? 0);
    requireCount(target, count, passed);
  }
} else {
  const vitest = fileURLToPath(new URL("../node_modules/vitest/vitest.mjs", import.meta.url));
  console.log(`Required target: Vitest config vitest.config.ts; filters=${JSON.stringify(targets)}`);
  const output = execute([vitest, "run", "--config", "vitest.config.ts", "--reporter=json", "--passWithNoTests=false", ...targets]);
  const report = JSON.parse(output);
  requireCount("Vitest", report.numTotalTests, report.numPassedTests);
  for (const file of report.testResults) {
    requireCount(file.name, file.assertionResults.length,
      file.assertionResults.filter((test) => test.status === "passed").length);
  }
}
