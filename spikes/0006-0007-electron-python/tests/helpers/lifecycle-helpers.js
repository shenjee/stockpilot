'use strict';

/**
 * Shared helpers for lifecycle + transport tests.
 */

const { spawn } = require('node:child_process');
const { execFileSync } = require('node:child_process');

/**
 * Wait until `cond` returns truthy, polling every `intervalMs`, up to `timeoutMs`.
 * Resolves with the last value of cond(); rejects on timeout.
 */
async function waitFor(cond, { timeoutMs = 4000, intervalMs = 25 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = cond();
    if (last) return last;
    await sleep(intervalMs);
  }
  throw new Error(`waitFor timed out after ${timeoutMs}ms; last=${JSON.stringify(last)}`);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/** Is a process with `pid` alive? */
function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Collect the PIDs of all running processes whose command line contains `needle`.
 * Used to assert "no orphan python child" after shutdown.
 */
function pidsMatching(needle) {
  try {
    const out = execFileSync('pgrep', ['-f', needle], { encoding: 'utf8' });
    return out
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
      .map(Number);
  } catch {
    return [];
  }
}

/**
 * Build a host options object that points at the fake service and forwards the
 * given behavior env vars (SPIKE_MODE, SPIKE_INIT_DELAY_MS, ...).
 */
function hostOpts(extraEnv = {}, overrides = {}) {
  return {
    readinessTimeoutMs: 6000,
    gracefulShutdownTimeoutMs: 3000,
    maxRestarts: 3,
    restartBackoffMs: 80,
    restartBackoffMaxMs: 400,
    extraEnv,
    ...overrides,
  };
}

module.exports = {
  waitFor,
  sleep,
  isAlive,
  pidsMatching,
  hostOpts,
};
