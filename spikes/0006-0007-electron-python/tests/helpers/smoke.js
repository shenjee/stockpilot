// Ad-hoc smoke: not part of the suite. Run with: node tests/helpers/smoke.js
'use strict';
const { PythonServiceHost, STATES } = require('../../src/main/python_service_host');

(async () => {
  const host = new PythonServiceHost({
    readinessTimeoutMs: 6000,
    onStateChange: (s, info) => console.log('[state]', s, info || ''),
  });
  const status = await host.start();
  console.log('READY', JSON.stringify(status));
  // check no orphan: record pid
  const pid = host.pid;
  console.log('pid', pid, 'port', host.port, 'gen', host.generation);
  const res = await host.shutdown();
  console.log('shutdown result', JSON.stringify(res));
  // verify pid gone
  try {
    process.kill(pid, 0);
    console.log('STILL ALIVE (bad)', pid);
    process.exit(1);
  } catch {
    console.log('EXITED clean (good)');
  }
  console.log('final state', host.state);
})().catch((e) => {
  console.error('SMOKE FAILED', e);
  process.exit(2);
});
