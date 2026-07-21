// Ad-hoc transport smoke: node tests/helpers/transport-smoke.js ws|sse
'use strict';
const { PythonServiceHost } = require('../../src/main/python_service_host');
const { createTransport } = require('../../src/main/gateway/transport');

const KIND = process.argv[2] || 'ws';

(async () => {
  const host = new PythonServiceHost({ readinessTimeoutMs: 8000 });
  const status = await host.start();
  const baseURL = `http://127.0.0.1:${status.port}`;
  const t = createTransport(KIND, { baseURL, credential: status.credential, generation: status.generation });
  const events = [];
  t.on({ onEvent: (e) => events.push(e), onSnapshot: (s) => console.log('[snap]', s.revision) });
  const created = await t.createSession();
  const sid = created.session_id;
  console.log('session', sid);
  const r = await t.connect(sid);
  console.log('connected', r.connected);
  // emit 3 events
  for (let i = 1; i <= 3; i++) await t.emit(sid, { type: 'tick', i });
  await new Promise((r) => setTimeout(r, 400));
  console.log('received events:', events.length, 'revisions:', events.map((e) => e.revision).join(','));
  await t.close();
  await host.shutdown();
})().catch((e) => { console.error('SMOKE FAILED', e); process.exit(2); });
