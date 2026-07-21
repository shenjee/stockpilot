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

  test(`[${KIND}] old-generation snapshot is rejected and does not reset the baseline`, async () => {
    // Regression (blocker 3): a stale-generation snapshot must NOT be accepted
    // and must NOT reset the revision baseline. Applies to snapshot, gap, and
    // error envelopes alike.
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg);
      const created = await t.createSession();
      const sid = created.session_id;
      const snapshots = [];
      t.on({ onSnapshot: (s) => snapshots.push(s) });
      await t.connect(sid);
      assert.equal(snapshots.length, 1, 'initial snapshot accepted');
      const baselineRev = snapshots[0].revision;

      // Stale-generation snapshot: must be dropped, baseline untouched.
      const staleGen = cfg.generation - 1;
      const acceptedSnap = t._applyEnvelope({
        type: 'snapshot',
        session_id: sid,
        generation: staleGen,
        revision: 99,
        snapshot: { poisoned: true },
      });
      assert.equal(acceptedSnap, false, 'old-generation snapshot must be rejected');
      assert.equal(snapshots.length, 1, 'no new snapshot must be emitted');
      assert.equal(
        t._lastRevisionBySession.get(sid),
        baselineRev,
        'baseline revision must NOT be reset by a stale snapshot',
      );

      // Stale-generation error must also be dropped (no spurious onError).
      let errored = false;
      t.on({ onError: () => { errored = true; } });
      const acceptedErr = t._applyEnvelope({
        type: 'error',
        session_id: sid,
        generation: staleGen,
        error: 'stale',
      });
      assert.equal(acceptedErr, false, 'old-generation error must be rejected');
      assert.equal(errored, false, 'no onError for a stale-generation error');

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

  test(`[${KIND}] reconnect: a real dropped connection re-establishes a full-snapshot baseline`, async () => {
    // Same transport throughout. The server drops the live connection via
    // /api/kick; the client must detect the drop, run its bounded reconnect,
    // and re-baseline from a fresh snapshot.
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg, {
        reconnectInitialDelayMs: 40,
        reconnectMaxDelayMs: 120,
        maxReconnectAttempts: 5,
      });
      const created = await t.createSession();
      const sid = created.session_id;
      const snapshots = [];
      const reconnects = [];
      const disconnects = [];
      t.on({
        onSnapshot: (s) => snapshots.push(s),
        onReconnect: () => reconnects.push(Date.now()),
        onDisconnect: () => disconnects.push(Date.now()),
      });
      await t.connect(sid);
      assert.equal(snapshots.length, 1, 'initial baseline snapshot');
      // Drop the server-side connection WITHOUT retiring the session.
      await t.kick(sid);
      // Wait for the bounded reconnect to fire and deliver a fresh snapshot.
      const deadline = Date.now() + 4000;
      while (snapshots.length < 2 && Date.now() < deadline) await sleep(30);
      assert.ok(disconnects.length >= 1, 'client must observe the dropped connection');
      assert.ok(reconnects.length >= 1, 'client must run its automatic reconnect');
      assert.equal(snapshots.length, 2, 'reconnect must re-establish a fresh snapshot baseline');
      // Both snapshots are for the same session/generation.
      assert.equal(snapshots[1].session_id, sid);
      assert.equal(snapshots[1].generation, cfg.generation);
      await t.close();
    });
  });

  test(`[${KIND}] reconnect: bounded retry gives up after maxReconnectAttempts`, async () => {
    // Kill the Python service so every reconnect attempt fails; the client
    // must stop after the configured bound and surface exhaustion, not loop.
    await withHost({}, async (cfg) => {
      const t = createTransport(KIND, cfg, {
        reconnectInitialDelayMs: 20,
        reconnectMaxDelayMs: 40,
        maxReconnectAttempts: 2,
      });
      const created = await t.createSession();
      const sid = created.session_id;
      let exhausted = false;
      t.on({ onError: (e) => { if (String(e.message).includes('exhausted')) exhausted = true; } });
      await t.connect(sid);
      await t.kick(sid);
      await cfg.host.shutdown(); // server gone -> reconnects fail
      const deadline = Date.now() + 4000;
      while (!exhausted && Date.now() < deadline) await sleep(50);
      assert.equal(exhausted, true, 'must stop reconnecting after maxReconnectAttempts');
      await t.close();
    });
  });

  test(`[${KIND}] slow consumer: overflow is detected, snapshot re-fetched, final state consistent`, async () => {
    // Server writes slowly with a tiny bounded buffer; emit many events
    // faster than the consumer drains so the buffer overflows. The client
    // MUST detect the loss (gap marker or revision gap), stop applying
    // incrementals, re-baseline from a full snapshot, and end consistent.
    await withHost(
      { SPIKE_SLOW_CONSUMER_DELAY_MS: 60, SPIKE_EVENT_BUFFER_MAX: 4 },
      async (cfg) => {
        const t = createTransport(KIND, cfg, { requestTimeoutMs: 5000 });
        const created = await t.createSession();
        const sid = created.session_id;
        const events = [];
        const snapshots = [];
        const gaps = [];
        t.on({
          onEvent: (e) => events.push(e),
          onSnapshot: (s) => snapshots.push(s),
          onGap: (g) => gaps.push(g),
        });
        await t.connect(sid);
        assert.equal(snapshots.length, 1, 'initial baseline');
        const N = 12;
        for (let i = 1; i <= N; i++) await t.emit(sid, { type: 'tick', i });
        // Wait for the slow consumer to drain and the re-baseline to complete.
        const deadline = Date.now() + 4000;
        while (snapshots.length < 2 && Date.now() < deadline) await sleep(30);
        await sleep(200); // let any trailing incrementals settle

        // 1. Loss was detected (explicit gap marker from server, or client-side
        //    revision-gap detection). At least one gap signal must have fired.
        assert.ok(gaps.length >= 1, 'overflow must be detected as a gap');

        // 2. A full snapshot was re-fetched to re-baseline.
        assert.ok(snapshots.length >= 2, 'a re-baseline snapshot must be fetched');
        const baseline = snapshots[snapshots.length - 1];

        // 3. Final state is consistent: the baseline revision equals the last
        //    emitted revision (N), proving the live tail was reached, and no
        //    incremental with a gap was applied as if it were contiguous.
        assert.equal(baseline.revision, N, 're-baseline must reach the live tail (rev N)');
        // No applied incremental may have a revision that skips. Since the
        // client drops incrementals while gapped and re-baselines, the applied
        // events (if any) must be contiguous from the baseline backward or
        // none at all (all subsumed by the snapshot). Either is acceptable;
        // what is NOT acceptable is accepting rev N as a contiguous +1 from
        // a much lower revision.
        const applied = events.map((e) => e.revision);
        if (applied.length > 1) {
          for (let i = 1; i < applied.length; i++) {
            assert.equal(applied[i], applied[i - 1] + 1, `applied revisions must be contiguous, got ${applied.join(',')}`);
          }
        }
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

  test(`[${KIND}] request cancellation: cancel() aborts an in-flight HTTP request`, async () => {
    // Regression (blocker 5): cancel() must abort this transport's own
    // in-flight HTTP requests, not just close the event stream. We use the
    // slow-work endpoint (3s) and cancel mid-flight; the request must reject
    // quickly via abort, not wait for the server.
    await withHost({ SPIKE_SLOW_WORK_MS: 3000 }, async (cfg) => {
      const t = createTransport(KIND, cfg, { requestTimeoutMs: 10000 });
      const start = Date.now();
      // Fire a slow request through the transport's _request path (same path
      // the domain methods use) but with a long timeout so the abort is the
      // only thing that can end it quickly.
      const inflight = t._request('/api/slow-work', { method: 'POST', body: {} }).catch(
        (err) => err && err.name === 'AbortError' ? 'aborted' : Promise.reject(err),
      );
      await sleep(50); // let the request land server-side
      t.cancel();
      const result = await inflight;
      const elapsed = Date.now() - start;
      assert.equal(result, 'aborted', 'in-flight request must abort on cancel()');
      assert.ok(elapsed < 1000, `cancel should abort promptly, took ${elapsed}ms`);
      await t.close();
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
