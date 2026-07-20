'use strict';

/**
 * PythonServiceHost - ADR 0006 process lifecycle owner.
 *
 * Owns ONE Python child process per host instance. This is pure Node (no
 * Electron dependency) so the lifecycle state machine can be unit-tested
 * headlessly. The real Electron main will wrap it (see notes at the bottom).
 *
 * Responsibilities (ADR 0006 Validation Required 1-11):
 *   - spawn the python child with an ephemeral loopback port + per-launch cred;
 *   - capture stdout handshake (SPIKE_LISTENING/SPIKE_READY/SPIKE_FAILED) and
 *     stderr diagnostics; stdout/stderr are NEVER interpreted as business data;
 *   - wait for an explicit readiness signal with a timeout;
 *   - monitor the child; on crash, perform BOUNDED automatic restart with a new
 *     service_generation (old requests/events are invalidated downstream);
 *   - on restart exhaustion, stop the loop and surface a user-facing retry path;
 *   - graceful shutdown (POST /shutdown) followed by time-bounded forced kill;
 *   - no orphan process after normal or forced quit;
 *   - executable/script discovery for dev + package-equivalent layouts.
 *
 * State machine:
 *
 *   stopped -> starting -> ready -> (crash|stop)
 *      ^                                |
 *      |   <- exhausted <- restarting <-+
 *      v
 *   failed  (user retry available via .start() again)
 */

const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const net = require('node:net');

const {
  resolvePythonExecutable,
  resolveScriptPath,
  discoverPackagedRoot,
} = require('./executable_discovery');

const STATES = Object.freeze({
  STOPPED: 'stopped',
  STARTING: 'starting',
  READY: 'ready',
  RESTARTING: 'restarting',
  STOPPING: 'stopping',
  FAILED: 'failed',
});

const LIFECYCLE_KINDS = new Set([
  'SPIKE_LISTENING',
  'SPIKE_READY',
  'SPIKE_FAILED',
  'SPIKE_STOPPING',
]);

const DEFAULTS = {
  readinessTimeoutMs: 8000,
  gracefulShutdownTimeoutMs: 4000,
  maxRestarts: 3,
  restartBackoffMs: 250,
  restartBackoffMaxMs: 2000,
};

/**
 * @typedef {Object} HostOptions
 * @property {string} [executable]
 * @property {string} [script]
 * @property {string} [packagedRoot]
 * @property {number} [readinessTimeoutMs]
 * @property {number} [gracefulShutdownTimeoutMs]
 * @property {number} [maxRestarts]
 * @property {number} [restartBackoffMs]
 * @property {number} [restartBackoffMaxMs]
 * @property {Record<string,string>} [extraEnv]   service behavior env (SPIKE_MODE, ...)
 * @property {(line: object) => void} [onDiagnostic]
 * @property {(state: string, info?: object) => void} [onStateChange]
 * @property {boolean} [autoRestart]  default true
 */

class PythonServiceHost {
  /**
   * @param {HostOptions} [options]
   */
  constructor(options = {}) {
    this.opts = { ...DEFAULTS, ...options };
    this.state = STATES.STOPPED;
    this.generation = 0;
    this.child = null;
    this.port = null;
    this.credential = null;
    this.pid = null;
    /** collected stdout/stderr diagnostic lines (capped) */
    this.diagnostics = [];
    this._maxDiagnostics = 2000;
    this._restartCount = 0;
    this._readinessTimer = null;
    this._crashTimer = null;
    this._shuttingDown = false;
    this._pendingStart = null;
    /** @type {Array<{resolve:Function,reject:Function,desc:string}>} */
    this._graceShutdownResolvers = [];
  }

  // -- public API --------------------------------------------------------

  /**
   * Start the service and resolve once READY, or reject on startup failure /
   * readiness timeout / restart exhaustion.
   * @returns {Promise<{generation:number, port:number, pid:number, credential:string}>}
   */
  async start() {
    if (this.state === STATES.READY) {
      return this._status();
    }
    if (this.state === STATES.STARTING || this.state === STATES.RESTARTING) {
      // Coalesce concurrent start calls onto the in-flight one.
      return this._pendingStart;
    }
    this._shuttingDown = false;
    this._pendingStart = this._spawnFresh();
    try {
      const status = await this._pendingStart;
      this._pendingStart = null;
      return status;
    } catch (err) {
      this._pendingStart = null;
      throw err;
    }
  }

  /**
   * Graceful shutdown: POST /shutdown, wait for exit; force-kill on timeout.
   * Safe to call when idle or with active work. Resolves once the child is gone.
   */
  async shutdown() {
    this._shuttingDown = true;
    return this._stop({ graceful: true });
  }

  /**
   * Force-kill immediately (used on graceful-timeout, or externally).
   */
  async forceKill() {
    return this._stop({ graceful: false });
  }

  /**
   * Crash the child on demand (test/diagnostic helper). Sends POST /api/crash.
   */
  async crashOnDemand() {
    if (!this.port || !this.credential) {
      throw new Error('not ready');
    }
    const res = await fetch(`http://127.0.0.1:${this.port}/api/crash`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${this.credential}` },
    }).catch(() => null);
    // The endpoint never returns; connection reset is expected.
    return res;
  }

  /** Snapshot the current status projection (what Electron would show). */
  status() {
    return this._status();
  }

  /** True when a new generation has been created by restart. */
  get restartCount() {
    return this._restartCount;
  }

  _status() {
    return {
      state: this.state,
      generation: this.generation,
      port: this.port,
      pid: this.pid,
      credential: this.credential,
      restartCount: this._restartCount,
    };
  }

  // -- internals ---------------------------------------------------------

  _setState(next, info) {
    this.state = next;
    if (typeof this.opts.onStateChange === 'function') {
      try {
        this.opts.onStateChange(next, info);
      } catch {
        /* listener errors must not break the FSM */
      }
    }
  }

  _recordDiagnostic(line) {
    if (this.diagnostics.length < this._maxDiagnostics) this.diagnostics.push(line);
    if (typeof this.opts.onDiagnostic === 'function') {
      try {
        this.opts.onDiagnostic(line);
      } catch {
        /* ignore */
      }
    }
  }

  /**
   * Spawn a fresh child and wait for SPIKE_READY (or fail).
   * @returns {Promise<object>}
   */
  _spawnFresh() {
    return new Promise((resolve, reject) => {
      this.generation += 1;
      const credential = randomBytes(32).toString('hex');
      this.credential = credential;

      const packed = discoverPackagedRoot({ packagedRoot: this.opts.packagedRoot });
      const exe = this.opts.executable || resolvePythonExecutable({ packagedRoot: packed }).path;
      const script =
        this.opts.script || resolveScriptPath({ packagedRoot: packed, here: __dirname });

      const env = {
        ...process.env,
        SPIKE_GENERATION: String(this.generation),
        SPIKE_CREDENTIAL: credential,
        ...(this.opts.extraEnv || {}),
      };

      this._setState(STATES.STARTING, { generation: this.generation, exe, script });

      const child = spawn(exe, [script], {
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      this.child = child;
      this.pid = child.pid;

      let stdoutBuf = '';
      let stderrBuf = '';
      let resolved = false;
      let stderrLines = 0;

      const onStdout = (chunk) => {
        stdoutBuf += chunk.toString('utf8');
        let nl;
        while ((nl = stdoutBuf.indexOf('\n')) >= 0) {
          const raw = stdoutBuf.slice(0, nl).trim();
          stdoutBuf = stdoutBuf.slice(nl + 1);
          if (!raw) continue;
          const parsed = this._parseLine(raw, 'stdout');
          if (!parsed) continue;
          this._recordDiagnostic({ stream: 'stdout', ...parsed });
          if (parsed.kind === 'SPIKE_LISTENING' && parsed.port) {
            this.port = parsed.port;
          } else if (parsed.kind === 'SPIKE_READY') {
            this._clearReadinessTimer();
            if (!resolved) {
              resolved = true;
              this._setState(STATES.READY, { generation: this.generation, port: this.port, pid: this.pid });
              resolve(this._status());
            }
          } else if (parsed.kind === 'SPIKE_FAILED') {
            this._clearReadinessTimer();
            if (!resolved) {
              resolved = true;
              this.child = null;
              this.pid = null;
              this._setState(STATES.FAILED, { reason: parsed.reason, generation: this.generation });
              reject(new StartupError(parsed.reason || 'startup_failed', this._status()));
            }
          }
        }
      };

      const onStderr = (chunk) => {
        stderrBuf += chunk.toString('utf8');
        let nl;
        while ((nl = stderrBuf.indexOf('\n')) >= 0) {
          const raw = stderrBuf.slice(0, nl).trim();
          stderrBuf = stderrBuf.slice(nl + 1);
          stderrLines += 1;
          if (!raw) continue;
          const parsed = this._parseLine(raw, 'stderr');
          if (parsed) {
            this._recordDiagnostic({ stream: 'stderr', ...parsed });
          } else {
            // Non-JSON line: keep as opaque diagnostic text. NEVER business data.
            this._recordDiagnostic({ stream: 'stderr', kind: 'SPIKE_RAW', text: raw });
          }
        }
      };

      child.stdout.on('data', onStdout);
      child.stderr.on('data', onStderr);

      const onExit = (code, signal) => {
        this._clearReadinessTimer();
        if (!resolved) {
          resolved = true;
          this.child = null;
          this.pid = null;
          this._setState(STATES.FAILED, {
            reason: 'exit_before_ready',
            code,
            signal,
            generation: this.generation,
          });
          reject(new StartupError('exit_before_ready', this._status()));
          return;
        }
        // If startup already failed (SPIKE_FAILED), do not restart the loop.
        if (this.state === STATES.FAILED) {
          return;
        }
        // Was READY before exit: either a crash (unexpected) or a graceful stop.
        this._handleExitAfterReady(code, signal);
      };

      child.on('exit', onExit);
      child.on('error', (err) => {
        this._clearReadinessTimer();
        if (!resolved) {
          resolved = true;
          this.child = null;
          this.pid = null;
          this._setState(STATES.FAILED, { reason: 'spawn_error', error: err.message, generation: this.generation });
          reject(new StartupError('spawn_error', this._status(), err));
        }
      });

      // Readiness timeout.
      this._readinessTimer = setTimeout(() => {
        if (resolved) return;
        resolved = true;
        // Kill the unresponsive child; it will trigger onExit.
        try {
          child.kill('SIGKILL');
        } catch {
          /* ignore */
        }
        // onExit will fire asynchronously; clear the handle now so tests see
        // a deterministic FAILED state without a lingering child reference.
        this.child = null;
        this.pid = null;
        this._setState(STATES.FAILED, { reason: 'readiness_timeout', generation: this.generation });
        reject(new StartupError('readiness_timeout', this._status()));
      }, this.opts.readinessTimeoutMs);
    });
  }

  _parseLine(raw, stream) {
    try {
      const obj = JSON.parse(raw);
      if (obj && typeof obj.kind === 'string') return obj;
    } catch {
      /* not JSON */
    }
    return null;
  }

  _clearReadinessTimer() {
    if (this._readinessTimer) {
      clearTimeout(this._readinessTimer);
      this._readinessTimer = null;
    }
  }

  /**
   * Handle child exit AFTER it was already READY.
   * If we are shutting down -> finish the stop.
   * Else (crash) -> bounded restart with a new generation.
   */
  _handleExitAfterReady(code, signal) {
    if (this._shuttingDown) {
      this._finishStop({ code, signal });
      return;
    }
    // Crash path: the exited child handle is stale; clear it so callers see a
    // deterministic status while a restart is pending.
    this.child = null;
    this.pid = null;
    const autoRestart = this.opts.autoRestart !== false;
    if (!autoRestart || this._restartCount >= this.opts.maxRestarts) {
      this._setState(STATES.FAILED, {
        reason: this._restartCount >= this.opts.maxRestarts ? 'restart_exhausted' : 'crash_no_restart',
        code,
        signal,
        restartCount: this._restartCount,
        generation: this.generation,
      });
      return;
    }
    this._scheduleRestart(code, signal);
  }

  _scheduleRestart(code, signal) {
    this._restartCount += 1;
    const n = this._restartCount;
    const backoff = Math.min(
      this.opts.restartBackoffMaxMs,
      this.opts.restartBackoffMs * (1 << Math.min(n - 1, 6)),
    );
    this._setState(STATES.RESTARTING, {
      generation: this.generation,
      restartCount: n,
      backoffMs: backoff,
      crashedBy: { code, signal },
    });
    // New generation invalidates prior connection params.
    this.port = null;
    this.credential = null;
    this.pid = null;

    this._crashTimer = setTimeout(() => {
      this._crashTimer = null;
      // Restart is fire-and-forget from the FSM's perspective; callers that
      // need to know readiness observe onStateChange.
      this._spawnFresh().catch(() => {
        /* _spawnFresh already moved us to FAILED; nothing to do */
      });
    }, backoff);
  }

  /**
   * Stop the service. If graceful, POST /shutdown then wait; otherwise SIGKILL.
   */
  async _stop({ graceful }) {
    const child = this.child;
    if (!child) {
      this._setState(STATES.STOPPED);
      return { code: null, signal: null, forced: !graceful };
    }
    this._setState(STATES.STOPPING, { graceful });

    if (graceful && this.port && this.credential) {
      // Try the graceful endpoint. Ignore its response; it exits the process.
      try {
        await fetch(`http://127.0.0.1:${this.port}/shutdown`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${this.credential}` },
          signal: AbortSignal.timeout(Math.max(500, this.opts.gracefulShutdownTimeoutMs - 500)),
        });
      } catch {
        /* endpoint may not respond (process exiting); fall through to wait */
      }
    }

    const exited = await this._waitForExit(this.opts.gracefulShutdownTimeoutMs);
    if (!exited) {
      // Forced termination.
      try {
        child.kill('SIGKILL');
      } catch {
        /* ignore */
      }
      await this._waitForExit(2000);
    }
    const result = { forced: !exited };
    this._finishStop(result);
    return result;
  }

  _waitForExit(timeoutMs) {
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode) {
      return Promise.resolve(true);
    }
    return new Promise((resolve) => {
      let done = false;
      const finish = (val) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(val);
      };
      const timer = setTimeout(() => finish(false), timeoutMs);
      child.once('exit', () => finish(true));
    });
  }

  _finishStop(info) {
    this.child = null;
    this.pid = null;
    this._shuttingDown = false;
    // Avoid emitting duplicate STOPPED (e.g. graceful exit + _stop both finish).
    if (this.state === STATES.STOPPED || this.state === STATES.FAILED) return;
    this._setState(STATES.STOPPED, info);
  }
}

class StartupError extends Error {
  constructor(reason, status, cause) {
    super(`python service startup failed: ${reason}`);
    this.name = 'StartupError';
    this.reason = reason;
    this.status = status;
    if (cause) this.cause = cause;
  }
}

module.exports = {
  PythonServiceHost,
  StartupError,
  STATES,
  DEFAULTS,
};
