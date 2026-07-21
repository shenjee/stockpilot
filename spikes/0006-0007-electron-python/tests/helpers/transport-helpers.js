'use strict';

const { PythonServiceHost } = require('../../src/main/python_service_host');

/**
 * Spin up a real PythonServiceHost and resolve a transport-config object
 * { baseURL, credential, generation, host }. Returns a dispose() to tear down.
 */
async function withHost(extraEnv, fn) {
  const host = new PythonServiceHost({
    readinessTimeoutMs: 8000,
    gracefulShutdownTimeoutMs: 3000,
    extraEnv,
  });
  const status = await host.start();
  const cfg = {
    baseURL: `http://127.0.0.1:${status.port}`,
    credential: status.credential,
    generation: status.generation,
    host,
  };
  let disposed = false;
  const dispose = async () => {
    if (disposed) return;
    disposed = true;
    await host.shutdown();
  };
  try {
    return await fn(cfg, dispose);
  } finally {
    await dispose();
  }
}

/** Collect N events with a timeout. */
async function collectEvents(transport, n, { timeoutMs = 3000 } = {}) {
  const events = [];
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => resolve(events), timeoutMs);
    transport.on({
      onEvent: (e) => {
        events.push(e);
        if (events.length >= n) {
          clearTimeout(timer);
          resolve(events);
        }
      },
      onSnapshot: (s) => events.push({ type: 'snapshot', ...s }),
    });
  });
}

module.exports = { withHost, collectEvents };
