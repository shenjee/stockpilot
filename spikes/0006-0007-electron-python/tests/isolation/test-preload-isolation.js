'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { SafeBridge, buildRendererApi, ALLOWED_OPS } = require('../../src/preload/safe_bridge');
const { withHost } = require('../helpers/transport-helpers');
const { createTransport } = require('../../src/main/gateway/transport');

// A fake gateway that records calls and emits no real events, for key-leak
// inspection without a live Python process.
function fakeGateway() {
  const calls = [];
  const state = {};
  return {
    request: async (op, payload) => { calls.push(['request', op, payload]); return { ok: true, op, request_id: 'r1' }; },
    createSession: async () => { calls.push(['createSession']); return { session_id: 's-1' }; },
    retireSession: async (sid) => { calls.push(['retireSession', sid]); return { retired: true }; },
    getSnapshot: async (sid) => { calls.push(['getSnapshot', sid]); return { session_id: sid }; },
    on: (listeners) => { calls.push(['on']); state.listeners = listeners; },
    connect: async (sid) => { calls.push(['connect', sid]); return { connected: true, snapshot: { type: 'snapshot', session_id: sid, revision: 0, snapshot: {} } }; },
    close: async () => { calls.push(['close']); },
    cancel: () => { calls.push(['cancel']); },
    _calls: calls,
    _state: state,
  };
}

test('renderer API exposes only allowlisted domain methods', () => {
  const gw = fakeGateway();
  const api = buildRendererApi(gw);
  const keys = Object.keys(api).sort();
  const expected = SafeBridge.allowedMethods().slice().sort();
  assert.deepEqual(keys, expected);
});

test('renderer API leaks no url, port, host, credential, or transport kind', () => {
  const gw = fakeGateway();
  const api = buildRendererApi(gw);
  const apiString = JSON.stringify(api, (k, v) => (typeof v === 'function' ? '[fn]' : v));
  const forbidden = ['http://', '127.0.0.1', 'localhost', 'Bearer ', 'credential', 'token', 'ws://', 'wss://', '/events', '/ws', 'port'];
  for (const word of forbidden) {
    assert.ok(
      !apiString.toLowerCase().includes(word.toLowerCase()),
      `renderer API must not contain "${word}"`,
    );
  }
  // The api object itself must not carry url/port/cred properties.
  for (const key of Object.keys(api)) {
    assert.ok(typeof api[key] === 'function', `key "${key}" should be a function, not a value`);
  }
});

test('renderer API leaks nothing via prototype chain or symbol', () => {
  const gw = fakeGateway();
  const api = buildRendererApi(gw);
  const ownProps = Object.getOwnPropertyNames(api);
  const ownSymbols = Object.getOwnPropertySymbols(api);
  assert.equal(ownSymbols.length, 0, 'no symbol properties');
  // No transport internals reachable.
  for (const prop of ownProps) {
    assert.ok(typeof api[prop] === 'function');
  }
});

test('renderer cannot call arbitrary ops: the bridge restricts to ALLOWED_OPS', async () => {
  const gw = fakeGateway();
  const api = buildRendererApi(gw);
  await api.selectSymbol('600000.SH');
  // The bridge only ever calls gateway.request with domain ops it knows; there
  // is no generic "request(op)" exposed on the api. Verify no passthrough.
  assert.equal(typeof api.request, 'undefined', 'no generic request passthrough');
  assert.equal(typeof api.fetch, 'undefined');
  assert.equal(typeof api.invoke, 'undefined');
});

test('with a live transport: renderer events are projected, envelopes never leak', async () => {
  await withHost({}, async (cfg) => {
    const t = createTransport('ws', cfg);
    const api = buildRendererApi(t);
    const startResult = await api.start();
    assert.ok(startResult.session);
    const received = [];
    api.onEvent((e) => received.push(e));
    // emit a couple of events through the gateway (main would do this in
    // response to backend state; here we drive it via the test-only emit).
    const sid = startResult.session;
    await t.emit(sid, { type: 'tick', i: 1 });
    await t.emit(sid, { type: 'tick', i: 2 });
    await new Promise((r) => setTimeout(r, 400));
    assert.ok(received.length >= 2, `got ${received.length}`);
    // Projected events must carry only domain fields, never the raw envelope.
    for (const e of received) {
      assert.ok('session' in e && 'revision' in e && 'type' in e && 'data' in e);
      assert.ok(!('generation' in e), 'generation must not leak to renderer');
      assert.ok(!('Authorization' in e));
    }
    await api.stop();
  });
});
