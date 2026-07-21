'use strict';

/**
 * Transport benchmark (ADR 0007). Measures HTTP + WebSocket vs HTTP + SSE:
 *   - representative payload sizes (snapshot + incremental event)
 *   - serialization time (JSON.stringify) client-side
 *   - end-to-end event latency (emit -> client receives)
 *   - throughput for a burst of N events
 *   - process RSS delta around a session
 *
 * Output is a JSON blob the report quotes. Run: node tests/benchmark/transport-bench.js
 */

const { PythonServiceHost } = require('../../src/main/python_service_host');
const { createTransport } = require('../../src/main/gateway/transport');
const { sleep } = require('../helpers/lifecycle-helpers');

function now() {
  return process.hrtime.bigint();
}
function msNs(a, b) {
  return Number(b - a) / 1e6;
}

function measurePayloadSizes(cfg) {
  // Build representative fixture shapes (mirror the fake service fixture).
  const smallSnapshot = {
    type: 'snapshot',
    session_id: 's-1',
    generation: cfg.generation,
    revision: 0,
    snapshot: {
      session_id: 's-1',
      generation: cfg.generation,
      revision: 0,
      fixture: {
        bars: Array.from({ length: 8 }, (_, i) => ({ i, c: round(i * 0.5, 2) })),
      },
    },
  };
  // A realistic 500-bar 5m workbench snapshot (representative of the T+0 surface).
  const largeSnapshotBars = Array.from({ length: 500 }, (_, i) => ({
    t: 1700000000 + i * 300,
    o: round(10 + Math.sin(i / 10) * 0.5, 4),
    h: round(10.5 + Math.sin(i / 10) * 0.5, 4),
    l: round(9.5 + Math.sin(i / 10) * 0.5, 4),
    c: round(10 + Math.cos(i / 10) * 0.4, 4),
    v: 100000 + (i * 137) % 50000,
  }));
  const largeSnapshot = {
    type: 'snapshot',
    session_id: 's-1',
    generation: cfg.generation,
    revision: 0,
    snapshot: { bars: largeSnapshotBars },
  };
  const incrementalEvent = {
    type: 'event',
    session_id: 's-1',
    generation: cfg.generation,
    revision: 1,
    payload: { type: 'bar', bar: largeSnapshotBars[0] },
  };
  return {
    small_snapshot_bytes: Buffer.byteLength(JSON.stringify(smallSnapshot)),
    large_snapshot_bytes: Buffer.byteLength(JSON.stringify(largeSnapshot)),
    incremental_event_bytes: Buffer.byteLength(JSON.stringify(incrementalEvent)),
    large_snapshot_bars: largeSnapshotBars.length,
  };
}

function measureSerializationTime(cfg) {
  const bars = Array.from({ length: 500 }, (_, i) => ({
    t: 1700000000 + i * 300,
    o: round(10 + Math.sin(i / 10) * 0.5, 4),
    h: round(10.5 + Math.sin(i / 10) * 0.5, 4),
    l: round(9.5 + Math.sin(i / 10) * 0.5, 4),
    c: round(10 + Math.cos(i / 10) * 0.4, 4),
    v: 100000 + (i * 137) % 50000,
  }));
  const snap = { type: 'snapshot', session_id: 's-1', generation: cfg.generation, revision: 0, snapshot: { bars } };
  const N = 200;
  const t0 = now();
  for (let i = 0; i < N; i++) JSON.stringify(snap);
  const elapsed = msNs(t0, now());
  return { n: N, total_ms: round(elapsed, 3), avg_us: round((elapsed / N) * 1000, 2) };
}

function round(n, d) {
  const p = Math.pow(10, d);
  return Math.round(n * p) / p;
}

async function measureEventLatency(cfg, kind, n) {
  const t = createTransport(kind, cfg);
  const created = await t.createSession();
  const sid = created.session_id;
  const latencies = [];
  const sentTimes = new Map();
  let connected = false;
  const connectedPromise = new Promise((resolve) => {
    t.on({
      onSnapshot: () => {
        if (!connected) {
          connected = true;
          resolve();
        }
      },
      onEvent: (e) => {
        const sent = sentTimes.get(e.revision);
        if (sent !== undefined) {
          latencies.push(round(msNs(sent, now()), 3));
        }
      },
    });
  });
  await t.connect(sid);
  // Wait for the snapshot baseline before emitting events.
  await Promise.race([
    connectedPromise,
    new Promise((r) => setTimeout(r, 2000)),
  ]);
  for (let i = 1; i <= n; i++) {
    const t0 = now();
    sentTimes.set(i, t0);
    await t.emit(sid, { type: 'tick', i });
    if (i % 10 === 0) await sleep(1);
  }
  // drain
  const deadline = Date.now() + 3000;
  while (latencies.length < n && Date.now() < deadline) await sleep(10);
  await t.close();
  latencies.sort((a, b) => a - b);
  const pct = (p) => (latencies.length ? latencies[Math.floor((p / 100) * latencies.length)] : null);
  return {
    kind,
    n,
    received: latencies.length,
    avg_ms: round(latencies.reduce((a, b) => a + b, 0) / Math.max(1, latencies.length), 3),
    p50_ms: pct(50),
    p95_ms: pct(95),
    max_ms: latencies.length ? latencies[latencies.length - 1] : null,
  };
}

async function measureMemoryDelta(cfg, kind) {
  const before = process.memoryUsage().rss;
  const t = createTransport(kind, cfg);
  const created = await t.createSession();
  const sid = created.session_id;
  await t.connect(sid);
  // emit a burst to exercise buffering
  const burstN = 500;
  const cpuBefore = process.cpuUsage();
  const wallBefore = process.hrtime.bigint();
  for (let i = 0; i < burstN; i++) await t.emit(sid, { type: 'tick', i });
  await sleep(300);
  const cpuAfter = process.cpuUsage(cpuBefore);
  const wallMs = msNs(wallBefore, process.hrtime.bigint());
  const after = process.memoryUsage().rss;
  await t.close();
  return {
    kind,
    burst_events: burstN,
    rss_before_mb: round(before / 1e6, 2),
    rss_after_mb: round(after / 1e6, 2),
    delta_mb: round((after - before) / 1e6, 2),
    cpu_user_ms: round(cpuAfter.user / 1000, 3),
    cpu_system_ms: round(cpuAfter.system / 1000, 3),
    cpu_total_ms: round((cpuAfter.user + cpuAfter.system) / 1000, 3),
    wall_ms: round(wallMs, 2),
    cpu_pct_of_wall: round(((cpuAfter.user + cpuAfter.system) / 1000 / wallMs) * 100, 2),
  };
}

async function main() {
  const host = new PythonServiceHost({ readinessTimeoutMs: 8000, gracefulShutdownTimeoutMs: 3000 });
  const status = await host.start();
  const cfg = {
    baseURL: `http://127.0.0.1:${status.port}`,
    credential: status.credential,
    generation: status.generation,
    host,
  };

  const sizes = measurePayloadSizes(cfg);
  const ser = measureSerializationTime(cfg);
  const latWs = await measureEventLatency(cfg, 'ws', 50);
  const latSse = await measureEventLatency(cfg, 'sse', 50);
  const memWs = await measureMemoryDelta(cfg, 'ws');
  const memSse = await measureMemoryDelta(cfg, 'sse');

  await host.shutdown();

  console.log(
    JSON.stringify(
      {
        platform: { node: process.version, python: '3.14', aiohttp: '3.14' },
        payload_sizes: sizes,
        serialization: ser,
        latency: { ws: latWs, sse: latSse },
        memory: { ws: memWs, sse: memSse },
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
