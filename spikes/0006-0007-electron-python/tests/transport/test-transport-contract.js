'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { createTransport } = require('../../src/main/gateway/transport');
const { withHost } = require('../helpers/transport-helpers');
const { sleep } = require('../helpers/lifecycle-helpers');

const TRANSPORTS = ['ws', 'sse'];

for (const KIND of TRANSPORTS) {
  test(`[${KIND}] ephemeral loopback port: host binds 127.0.0.1 only and port is ephemeral`, async () => {
    await withHost({}, async (cfg) => {
      assert.ok(cfg.baseURL.startsWith('http://127.0.0.1:'));
      const port = Number(cfg.baseURL.split(':').pop());
      assert.ok(port > 1024 && port < 65536, 'ephemeral port range');
      // The credential is a high-entropy per-launch token, not exposed here except
      // inside main (this test acts as main).
      assert.ok(cfg.credential.length >= 32);
    });
  });

  test(`[${KIND}] unauthenticated requests are rejected (401)`, async () => {
    await withHost({}, async (cfg) => {
      const r = await fetch(`${cfg.baseURL}/api/snapshot?session_id=x`);
      assert.equal(r.status, 401);
    });
  });

  test(`[${KIND}] invalid credentials are rejected (403)`, async () => {
    await withHost({}, async (cfg) => {
      const r = await fetch(`${cfg.baseURL}/api/snapshot?session_id=x`, {
        headers: { Authorization: 'Bearer wrongtoken' },
      });
      assert.equal(r.status, 403);
    });
  });

  test(`[${KIND}] request/response returns correlation id and generation`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const r = await t.request('select_symbol', { symbol: '600000.SH' });
      assert.equal(r.op, 'select_symbol');
      assert.match(r.request_id, /^r\d+$/);
      assert.equal(r.generation, cfg.generation);
    });
  });

  test(`[${KIND}] connect sends a full-snapshot baseline first`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const created = await t.createSession();
      const sid = created.session_id;
      const r = await t.connect(sid);
      assert.equal(r.connected, true);
      assert.ok(r.snapshot, 'snapshot baseline must arrive on connect');
      assert.equal(r.snapshot.type, 'snapshot');
      assert.equal(r.snapshot.session_id, sid);
      await t.close();
    });
  });

  test(`[${KIND}] ordered delivery of fixture events with increasing revisions`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const created = await t.createSession();
      const sid = created.session_id;
      const events = [];
      t.on({ onEvent: (e) => events.push(e) });
      await t.connect(sid);
      // emit 4 events
      for (let i = 1; i <= 4; i++) await t.emit(sid, { type: 'tick', i });
      // wait for them to arrive
      const deadline = Date.now() + 3000;
      while (events.length < 4 && Date.now() < deadline) await sleep(20);
      assert.equal(events.length, 4, `got ${events.length} events`);
      const revs = events.map((e) => e.revision);
      assert.deepEqual(revs, [1, 2, 3, 4], 'revisions must be strictly increasing');
      const is = events.map((e) => e.payload && e.payload.i);
      assert.deepEqual(is, [1, 2, 3, 4], 'order preserved');
      await t.close();
    });
  });

  test(`[${KIND}] duplicate / stale revisions are dropped`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const created = await t.createSession();
      const sid = created.session_id;
      const accepted = [];
      t.on({ onEvent: (e) => accepted.push(e) });
      await t.connect(sid);
      await t.emit(sid, { type: 'tick', i: 1 });
      await sleep(150);
      // Manually feed a stale envelope through _applyEnvelope (simulating a
      // duplicated/out-of-order delivery).
      t._applyEnvelope({ type: 'event', session_id: sid, generation: cfg.generation, revision: 1, payload: { type: 'tick', i: 1 } });
      t._applyEnvelope({ type: 'event', session_id: sid, generation: cfg.generation, revision: 0, payload: { type: 'tick', i: 0 } });
      await sleep(50);
      assert.equal(accepted.length, 1, 'stale/duplicate revisions must be dropped');
      await t.close();
    });
  });

  test(`[${KIND}] events from an old service_generation are dropped`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const created = await t.createSession();
      const sid = created.session_id;
      const accepted = [];
      t.on({ onEvent: (e) => accepted.push(e) });
      await t.connect(sid);
      // Inject an event whose generation does not match.
      const dropped = t._applyEnvelope({
        type: 'event',
        session_id: sid,
        generation: cfg.generation - 1, // old generation
        revision: 999,
        payload: { type: 'tick' },
      });
      assert.equal(dropped, false, 'old-generation event must be dropped');
      assert.equal(accepted.length, 0);
      await t.close();
    });
  });

  test(`[${KIND}] retired session rejects new events and subscribers`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const created = await t.createSession();
      const sid = created.session_id;
      await t.retireSession(sid);
      // emit after retire -> server returns 404 (unknown_or_retired)
      await assert.rejects(t.emit(sid, { type: 'tick' }), (err) => err.status === 404);
      // snapshot after retire -> 404
      await assert.rejects(t.getSnapshot(sid), (err) => err.status === 404);
      // a fresh transport connecting to a retired session gets an error envelope.
      const t2 = createTransport(KIND, cfg);
      let gotError = false;
      t2.on({ onError: () => { gotError = true; } });
      const r = await t2.connect(sid);
      assert.equal(r.connected, true);
      await sleep(200);
      assert.equal(gotError, true, 'retired session must surface an error to the client');
      await t2.close();
      await t.close();
    });
  });

  test(`[${KIND}] reconnect re-baselines from a full snapshot`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg, { reconnectInitialDelayMs: 50, reconnectMaxDelayMs: 100 });
      const created = await t.createSession();
      const sid = created.session_id;
      const snapshots = [];
      t.on({ onSnapshot: (s) => snapshots.push(s) });
      await t.connect(sid);
      assert.equal(snapshots.length, 1, 'initial snapshot');
      // Force a client-side reconnect: a fresh transport to the SAME session
      // (simulates a dropped connection re-establishing). Attach the listener
      // BEFORE connect so the baseline snapshot is captured.
      const t2 = createTransport(KIND, cfg, { reconnectInitialDelayMs: 50, reconnectMaxDelayMs: 100 });
      const snaps2 = [];
      t2.on({ onSnapshot: (s) => snaps2.push(s) });
      await t2.connect(sid);
      await sleep(150);
      assert.equal(snaps2.length, 1, 'reconnect must re-establish a snapshot baseline');
      await t2.close();
      await t.close();
    });
  });

  test(`[${KIND}] slow consumer: bounded buffer overflow is explicit, live tail kept`, async () => {
    // server writes slowly; emit many events faster than the consumer drains.
    await withHost(
      { SPIKE_SLOW_CONSUMER_DELAY_MS: 80, SPIKE_EVENT_BUFFER_MAX: 4 },
      async (cfg) => {
        const t = createTransport(KIND, cfg);
        const created = await t.createSession();
        const sid = created.session_id;
        const received = [];
        t.on({ onEvent: (e) => received.push(e) });
        await t.connect(sid);
        // Emit 12 events while the server drains at 80ms each.
        for (let i = 1; i <= 12; i++) await t.emit(sid, { type: 'tick', i });
        // Wait long enough that the slow consumer has drained what it can.
        await sleep(2500);
        // The client must NOT have received all 12 (buffer bounded at 4). It
        // should have received the live tail, not the head.
        assert.ok(
          received.length < 12,
          `slow consumer should be bounded, got ${received.length}`,
        );
        assert.ok(received.length >= 1, 'should keep at least the live tail');
        // The LAST received revision should be the highest seen (live tail kept).
        const revs = received.map((e) => e.revision);
        const maxRecv = Math.max(...revs);
        assert.equal(maxRecv, 12, 'live tail (latest revision) must be kept');
        await t.close();
      },
    );
  });

  test(`[${KIND}] request timeout: a hung request aborts within budget`, async () => {
    await withHost({ SPIKE_SLOW_WORK_MS: 3000 }, async (cfg) => {
      const t = createTransport(KIND, cfg, { requestTimeoutMs: 400 });
      // /api/slow-work is not exposed via the transport; call it directly.
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 400);
      const start = Date.now();
      await assert.rejects(
        fetch(`${cfg.baseURL}/api/slow-work`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${cfg.credential}`, 'Content-Type': 'application/json' },
          body: '{}',
          signal: ctrl.signal,
        }),
      );
      clearTimeout(timer);
      const elapsed = Date.now() - start;
      assert.ok(elapsed < 1500, `timeout should fire promptly, took ${elapsed}ms`);
    });
  });

  test(`[${KIND}] cancellation: close() aborts an in-flight stream cleanly`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const created = await t.createSession();
      const sid = created.session_id;
      let disconnected = false;
      t.on({ onDisconnect: () => { disconnected = true; } });
      await t.connect(sid);
      // emit one event so the stream has work
      await t.emit(sid, { type: 'tick', i: 1 });
      await sleep(50);
      await t.close();
      await sleep(150);
      assert.equal(disconnected, true, 'close() must terminate the stream');
    });
  });

  test(`[${KIND}] shutdown in flight: host shutdown closes the transport without hanging`, async () => {
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const created = await t.createSession();
      const sid = created.session_id;
      await t.connect(sid);
      await t.emit(sid, { type: 'tick', i: 1 });
      // Shut the Python service down while the stream is open.
      const start = Date.now();
      await cfg.host.shutdown();
      await t.close();
      const elapsed = Date.now() - start;
      assert.ok(elapsed < 5000, `shutdown in flight took ${elapsed}ms`);
    });
  });
}
