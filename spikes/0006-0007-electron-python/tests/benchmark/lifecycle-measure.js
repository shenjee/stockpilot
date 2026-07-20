'use strict';

/**
 * Collect ADR 0006 lifecycle measurements: cold-start latency, graceful
 * shutdown latency, generation/pid advancement across restart, and post-shutdown
 * orphan check. Prints a JSON blob the report can quote.
 *
 * Run: node tests/benchmark/lifecycle-measure.js
 */

const { PythonServiceHost } = require('../../src/main/python_service_host');
const { isAlive, sleep } = require('../helpers/lifecycle-helpers');

async function measureColdStartAndShutdown() {
  const N = 5;
  const starts = [];
  const stops = [];
  for (let i = 0; i < N; i++) {
    const h = new PythonServiceHost({
      readinessTimeoutMs: 8000,
      gracefulShutdownTimeoutMs: 3000,
    });
    const t0 = Date.now();
    await h.start();
    const t1 = Date.now();
    await h.shutdown();
    const t2 = Date.now();
    starts.push(t1 - t0);
    stops.push(t2 - t1);
  }
  const avg = (a) => Math.round(a.reduce((x, y) => x + y, 0) / a.length);
  return {
    N,
    cold_start_ms: starts,
    avg_cold_start_ms: avg(starts),
    max_cold_start_ms: Math.max(...starts),
    graceful_shutdown_ms: stops,
    avg_graceful_shutdown_ms: avg(stops),
  };
}

async function measureRestartGeneration() {
  const h = new PythonServiceHost({
    extraEnv: { SPIKE_MODE: 'crash-after-ready', SPIKE_CRASH_DELAY_MS: 150 },
    maxRestarts: 1,
    restartBackoffMs: 40,
    restartBackoffMaxMs: 80,
    readinessTimeoutMs: 8000,
  });
  const trace = [];
  h.opts.onStateChange = (s) => trace.push({ t: Date.now(), s });
  const t0 = Date.now();
  await h.start();
  const gen1 = h.generation;
  const pid1 = h.pid;
  const port1 = h.port;
  // wait for restart
  while (!(h.state === 'ready' && h.generation > gen1)) {
    await sleep(20);
    if (Date.now() - t0 > 6000) break;
  }
  const gen2 = h.generation;
  const pid2 = h.pid;
  const port2 = h.port;
  await h.shutdown();
  return {
    generation_before: gen1,
    generation_after: gen2,
    pid_before: pid1,
    pid_after: pid2,
    port_changed: port1 !== port2,
    pid_changed: pid1 !== pid2,
    generation_advanced: gen2 > gen1,
    state_trace: trace.map((t) => t.s),
  };
}

async function main() {
  const cs = await measureColdStartAndShutdown();
  const rs = await measureRestartGeneration();
  // Orphan sweep
  const h = new PythonServiceHost({ readinessTimeoutMs: 8000 });
  await h.start();
  const pid = h.pid;
  await h.shutdown();
  await sleep(80);
  const orphan = isAlive(pid);
  console.log(
    JSON.stringify(
      {
        ...cs,
        restart: rs,
        orphan_after_shutdown: orphan,
      },
      null,
      2,
    ),
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
