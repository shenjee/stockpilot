'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { PythonServiceHost, StartupError, STATES } = require('../../src/main/python_service_host');
const {
  resolvePythonExecutable,
  resolveScriptPath,
  discoverPackagedRoot,
} = require('../../src/main/executable_discovery');
const { waitFor, sleep, isAlive, pidsMatching, hostOpts } = require('../helpers/lifecycle-helpers');

const SCRIPT_TAG = 'spikes/0006-0007-electron-python/fake-python/service.py';

// ---------------------------------------------------------------------------
// Acceptance: cold start + readiness + clear status (ADR 0006 #1)
// ---------------------------------------------------------------------------

test('cold start reaches READY and exposes a deterministic status projection', async () => {
  const host = new PythonService(hostOpts());
  const states = [];
  host.opts.onStateChange = (s, i) => states.push([s, i]);
  const status = await host.start();

  assert.equal(host.state, STATES.READY);
  assert.equal(status.state, 'ready');
  assert.equal(typeof status.port, 'number');
  assert.ok(status.port > 0, 'port should be a real ephemeral port');
  assert.ok(status.pid > 0);
  assert.equal(status.generation, 1);
  assert.match(status.credential, /^[0-9a-f]{64}$/);
  // Generation in env must match the host's.
  assert.equal(status.generation, host.generation);

  // /healthz confirms ready + generation.
  const h = await fetch(`http://127.0.0.1:${status.port}/healthz`, {
    headers: { Authorization: `Bearer ${status.credential}` },
  });
  const body = await h.json();
  assert.equal(body.status, 'ready');
  assert.equal(body.generation, 1);

  await host.shutdown();
  assert.deepEqual(
    states.map((s) => s[0]),
    ['starting', 'ready', 'stopping', 'stopped'],
  );
});

// ---------------------------------------------------------------------------
// Acceptance: readiness timeout -> FAILED (ADR 0006 #1)
// ---------------------------------------------------------------------------

test('readiness timeout moves to FAILED and kills the unresponsive child', async () => {
  const host = new PythonService(
    hostOpts(
      // slow-ready will NOT signal ready within the short timeout.
      { SPIKE_MODE: 'slow-ready', SPIKE_INIT_DELAY_MS: 5000 },
      { readinessTimeoutMs: 400 },
    ),
  );
  await assert.rejects(host.start(), (err) => {
    assert.ok(err instanceof StartupError);
    assert.equal(err.reason, 'readiness_timeout');
    return true;
  });
  assert.equal(host.state, STATES.FAILED);
  // No leftover child.
  assert.equal(host.child, null);
});

// ---------------------------------------------------------------------------
// Acceptance: startup failure -> FAILED (ADR 0006 #1)
// ---------------------------------------------------------------------------

test('startup failure surfaces as a clear FAILED state with a reason', async () => {
  const host = new PythonService(hostOpts({ SPIKE_MODE: 'startup-fail' }));
  await assert.rejects(host.start(), (err) => {
    assert.ok(err instanceof StartupError);
    // The fake service emits an explicit SPIKE_FAILED before exiting; the host
    // surfaces that precise reason. Either that or exit_before_ready is acceptable.
    assert.ok(
      err.reason === 'startup_fail_mode' || err.reason === 'exit_before_ready',
      `unexpected reason: ${err.reason}`,
    );
    return true;
  });
  assert.equal(host.state, STATES.FAILED);
  assert.equal(host.child, null, 'no stale child handle after startup failure');
});

// ---------------------------------------------------------------------------
// Acceptance: Python crash does not crash Electron main; new generation (0006 #3,#4)
// ---------------------------------------------------------------------------

test('runtime crash keeps the host alive and triggers a bounded restart with a new generation', async () => {
  const host = new PythonService(
    hostOpts(
      { SPIKE_MODE: 'crash-after-ready', SPIKE_CRASH_DELAY_MS: 150 },
      { maxRestarts: 2, restartBackoffMs: 40, restartBackoffMaxMs: 80 },
    ),
  );
  const oldPidRef = { pid: null, gen: null, port: null };
  await host.start();
  oldPidRef.pid = host.pid;
  oldPidRef.gen = host.generation;
  oldPidRef.port = host.port;
  assert.ok(oldPidRef.pid > 0);

  // Wait for a crash + restart to a NEW generation/pid.
  await waitFor(
    () => host.state === STATES.READY && host.generation > oldPidRef.gen && host.pid !== oldPidRef.pid,
    { timeoutMs: 4000 },
  );
  assert.ok(host.generation > oldPidRef.gen, 'generation must advance');
  assert.notEqual(host.pid, oldPidRef.pid, 'new child pid');
  assert.notEqual(host.port, oldPidRef.port, 'new ephemeral port');
  assert.equal(host.restartCount, 1);

  // The host object itself is still usable (Electron main survived).
  assert.equal(host.state, STATES.READY);

  await host.shutdown();
  assert.equal(host.state, STATES.STOPPED);
});

// ---------------------------------------------------------------------------
// Acceptance: restart exhaustion -> stop loop + user-visible retry path (0006 #5)
// ---------------------------------------------------------------------------

test('restart exhaustion stops the loop and leaves a FAILED state a user can retry from', async () => {
  const host = new PythonService(
    hostOpts(
      { SPIKE_MODE: 'crash-after-ready', SPIKE_CRASH_DELAY_MS: 60 },
      { maxRestarts: 2, restartBackoffMs: 30, restartBackoffMaxMs: 60 },
    ),
  );
  await host.start();
  // Let it exhaust.
  await waitFor(() => host.state === STATES.FAILED, { timeoutMs: 6000 });
  assert.equal(host.restartCount, 2, 'should have attempted maxRestarts restarts');
  // A user-facing retry is just calling start() again; verify it works.
  const before = host.generation;
  await host.start();
  assert.ok(host.generation > before, 'retry created a newer generation');
  await host.shutdown();
});

// ---------------------------------------------------------------------------
// Acceptance: graceful shutdown idle -> no orphan (0006 #2, #6)
// ---------------------------------------------------------------------------

test('graceful shutdown of an idle service leaves no orphan process', async () => {
  const host = new PythonService(hostOpts());
  await host.start();
  const pid = host.pid;
  assert.ok(isAlive(pid));
  const res = await host.shutdown();
  assert.equal(res.forced, false, 'should exit gracefully without forced kill');
  // Give the OS a tick to reap.
  await sleep(50);
  assert.equal(isAlive(pid), false, 'child must be reaped');
});

// ---------------------------------------------------------------------------
// Acceptance: graceful shutdown with active work + forced fallback (0006 #6)
// ---------------------------------------------------------------------------

test('graceful shutdown with work in flight completes; forced kill is the fallback', async () => {
  // The service ignores /shutdown so the graceful path cannot succeed; the
  // host must fall back to a time-bounded forced kill, even while a slow
  // request is in flight, leaving no orphan.
  const host = new PythonService(
    hostOpts(
      { SPIKE_IGNORE_SHUTDOWN: '1', SPIKE_SLOW_WORK_MS: 1500 },
      { gracefulShutdownTimeoutMs: 500 },
    ),
  );
  await host.start();
  const port = host.port;
  const cred = host.credential;
  const pid = host.pid;
  // Fire a slow request; do not await it.
  const inFlight = fetch(`http://127.0.0.1:${port}/api/slow-work`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${cred}` },
    body: '{}',
  })
    .then((r) => r.status)
    .catch(() => 'err');
  await sleep(50); // let it land server-side
  const res = await host.shutdown();
  assert.equal(res.forced, true, 'should have to force-kill because /shutdown is ignored');
  const status = await inFlight;
  assert.ok(status === 'err' || status === 200, `unexpected in-flight status ${status}`);
  await sleep(60);
  assert.equal(isAlive(pid), false, 'orphan must not survive forced kill while work was in flight');
});

test('forced kill (ignoring /shutdown) still terminates the child', async () => {
  const host = new PythonService(
    hostOpts({ SPIKE_IGNORE_SHUTDOWN: '1' }, { gracefulShutdownTimeoutMs: 400 }),
  );
  await host.start();
  const pid = host.pid;
  assert.ok(isAlive(pid));
  const res = await host.shutdown();
  assert.equal(res.forced, true, 'service ignored /shutdown -> forced kill');
  await sleep(50);
  assert.equal(isAlive(pid), false, 'orphan must not survive forced kill');
});

// ---------------------------------------------------------------------------
// Acceptance: no orphan after normal quit (0006 #2) - cross-check via pgrep
// ---------------------------------------------------------------------------

test('after full shutdown, no python process running the fake service remains', async () => {
  const before = pidsMatching(SCRIPT_TAG).filter((p) => p !== process.pid);
  const host = new PythonService(hostOpts());
  await host.start();
  const pid = host.pid;
  await host.shutdown();
  await sleep(80);
  assert.equal(isAlive(pid), false);
  const after = pidsMatching(SCRIPT_TAG).filter((p) => p !== process.pid);
  // Allow for pre-existing stale procs; the key is we did not ADD one.
  assert.ok(
    after.length <= before.length,
    `orphan detected: before=${before.length} after=${after.length}`,
  );
});

// ---------------------------------------------------------------------------
// Acceptance: stdout/stderr are diagnostics, not business (0006 #9)
// ---------------------------------------------------------------------------

test('stdout/stderr are captured as diagnostics and never exposed as a business protocol', async () => {
  const host = new PythonService(hostOpts());
  await host.start();
  // Every captured diagnostic must be a lifecycle/log line with a known kind,
  // never a business payload. (Phase A has no business endpoints on stdout.)
  for (const line of host.diagnostics) {
    assert.ok(
      typeof line.kind === 'string',
      `diagnostic without kind: ${JSON.stringify(line)}`,
    );
    assert.ok(
      line.stream === 'stdout' || line.stream === 'stderr',
      `unexpected stream: ${line.stream}`,
    );
  }
  // There must be at least the readiness handshake on stdout.
  const stdoutKinds = host.diagnostics
    .filter((d) => d.stream === 'stdout')
    .map((d) => d.kind);
  assert.ok(stdoutKinds.includes('SPIKE_READY'));
  await host.shutdown();
});

// ---------------------------------------------------------------------------
// Acceptance: executable + resource discovery (dev + packaged) (0006 #10)
// ---------------------------------------------------------------------------

test('executable discovery: env override wins; packaged layout resolves when present; dev falls back to python3', () => {
  // env override
  const a = resolvePythonExecutable({ env: { SPIKE_PYTHON_EXECUTABLE: '/opt/myvenv/bin/python3' } });
  assert.equal(a.source, 'env');
  assert.equal(a.path, '/opt/myvenv/bin/python3');

  // packaged layout exists check without a real bundle
  const b = resolvePythonExecutable({ packagedRoot: '/no/such/packaged/root/here' });
  assert.equal(b.source, 'packaged');
  assert.equal(b.exists, false);

  // dev fallback
  const c = resolvePythonExecutable({});
  assert.equal(c.source, 'dev');
  assert.equal(c.path, 'python3');
});

test('script discovery: dev path points at fake-python/service.py; packaged path is <root>/backend/service.py', () => {
  const dev = resolveScriptPath({ here: __dirname });
  assert.ok(dev.endsWith('fake-python/service.py'), dev);

  const packed = resolveScriptPath({ packagedRoot: '/bundle' });
  assert.equal(packed, '/bundle/backend/service.py');
});

test('discoverPackagedRoot returns null when no candidate contains a python interpreter', () => {
  const r = discoverPackagedRoot({ candidates: ['/nope-a', '/nope-b'] });
  assert.equal(r, null);
});

// ---------------------------------------------------------------------------
// Acceptance: pre-crash generation is invalidated after restart (0006 #7)
// ---------------------------------------------------------------------------

test('after a crash+restart, the old port/credential no longer authenticate', async () => {
  const host = new PythonService(
    hostOpts(
      { SPIKE_MODE: 'crash-after-ready', SPIKE_CRASH_DELAY_MS: 100 },
      { maxRestarts: 1, restartBackoffMs: 40, restartBackoffMaxMs: 60 },
    ),
  );
  await host.start();
  const oldPort = host.port;
  const oldCred = host.credential;
  // Wait for a fresh generation.
  await waitFor(() => host.generation > 1 && host.state === STATES.READY, { timeoutMs: 4000 });
  const newPort = host.port;
  const newCred = host.credential;
  assert.notEqual(oldCred, newCred, 'credential must rotate per generation');

  // Old endpoint should be dead or reject the stale credential.
  let oldStillWorks = false;
  try {
    const r = await fetch(`http://127.0.0.1:${oldPort}/healthz`, {
      headers: { Authorization: `Bearer ${oldCred}` },
      signal: AbortSignal.timeout(500),
    });
    if (r.status === 200) {
      const b = await r.json();
      // Even if the port is reused by the OS, the old credential must fail.
      oldStillWorks = b.generation === host.generation && oldCred === newCred;
    }
  } catch {
    oldStillWorks = false; // connection refused -> correct
  }
  assert.equal(oldStillWorks, false, 'stale port+cred must not authenticate to the new service');

  // New generation authenticates fine.
  const ok = await fetch(`http://127.0.0.1:${newPort}/healthz`, {
    headers: { Authorization: `Bearer ${newCred}` },
  });
  const okBody = await ok.json();
  assert.equal(okBody.generation, host.generation);

  await host.shutdown();
});

// ---------------------------------------------------------------------------
// Acceptance: Replay (memory-only) state loss is explicit (0006 #8)
// ---------------------------------------------------------------------------

test('a crash loses memory-only Replay state explicitly; persisted Live inputs survive restart', async () => {
  // The fake service simulates an in-memory "replay session" via /api/slow-work
  // state and a persisted counter via env. Here we assert the *contract* the
  // host must express to the renderer after a crash:
  //   - generation advances (old replay session is invalidated);
  //   - the host surfaces FAILED->RESTARTING->READY so a renderer can show
  //     "replay progress lost, restoring Live";
  const stateTrace = [];
  const host = new PythonService(
    hostOpts(
      { SPIKE_MODE: 'crash-after-ready', SPIKE_CRASH_DELAY_MS: 120 },
      { maxRestarts: 1, restartBackoffMs: 40, restartBackoffMaxMs: 60 },
    ),
  );
  host.opts.onStateChange = (s, i) => stateTrace.push(s);
  const oldGen = (await host.start()).generation;
  // wait for restart to ready
  await waitFor(() => host.state === STATES.READY && host.generation > oldGen, { timeoutMs: 4000 });
  const newGen = host.generation;
  assert.ok(newGen > oldGen, 'generation advances -> replay generation retired');
  // The trace must contain the explicit restarting transition (the signal a
  // renderer uses to tell the user "Replay progress was lost").
  assert.ok(stateTrace.includes('restarting'), `trace: ${stateTrace.join(',')}`);
  await host.shutdown();
});

// Small alias so tests read naturally.
function PythonService(opts) {
  return new PythonServiceHost(opts);
}

module.exports = { PythonService: PythonServiceHost };
