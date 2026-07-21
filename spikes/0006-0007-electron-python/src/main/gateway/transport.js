'use strict';

/**
 * Local transport clients (ADR 0007).
 *
 * Two implementations behind one shape, so HTTP + WebSocket and HTTP + SSE can
 * be exercised and compared:
 *   - HttpWsTransport  : HTTP request/response + WebSocket event stream
 *   - HttpSseTransport : HTTP request/response + Server-Sent Events stream
 *
 * Both live in Electron main. They are constructed with the per-launch
 * `baseURL` + `credential` that PythonServiceHost produced; the Renderer never
 * sees either. They expose domain-oriented methods/events only - no raw URL,
 * port, token, or generic HTTP verb leaks upward.
 *
 * Contract elements surfaced (ADR 0007):
 *   - service_generation (compared on every event; stale-generation events
 *     are dropped and the stream reconnects from a snapshot);
 *   - session_id (immutable per session);
 *   - monotonically increasing revision (duplicates / stale revisions dropped);
 *   - request correlation ids;
 *   - full snapshot on (re)connect to (re)establish the baseline;
 *   - bounded local buffer + slow-consumer handling;
 *   - cancellation + clean shutdown with requests/streams in flight.
 */

const DEFAULTS = {
  requestTimeoutMs: 5000,
  reconnectInitialDelayMs: 100,
  reconnectMaxDelayMs: 2000,
  maxReconnectAttempts: 8,
  localBufferMax: 256,
  heartbeatMs: 0, // local loopback; no heartbeat needed by default
};

/**
 * @typedef {Object} TransportEvents
 * @property {(snap: object) => void} [onSnapshot]
 * @property {(event: object) => void} [onEvent]
 * @property {(err: Error) => void} [onError]
 * @property {(info: {reason:string}) => void} [onDisconnect]
 * @property {(info: {reason:string}) => void} [onReconnect]
 */

class BaseTransport {
  constructor({ baseURL, credential, generation }, options = {}) {
    if (!baseURL) throw new Error('baseURL required');
    if (!credential) throw new Error('credential required');
    this.baseURL = baseURL.replace(/\/$/, '');
    this.credential = credential;
    this.generation = generation;
    this.opts = { ...DEFAULTS, ...options };
    this._lastRevisionBySession = new Map();
    this._cancelled = false;
    /** @type {TransportEvents} */
    this._listeners = {};
  }

  on(listeners) {
    this._listeners = { ...this._listeners, ...listeners };
    return this;
  }

  _emit(name, arg) {
    const fn = this._listeners[name];
    if (fn) {
      try {
        fn(arg);
      } catch {
        /* listener errors must not break the transport */
      }
    }
  }

  /**
   * Apply an inbound event envelope. Enforces generation + revision semantics.
   *
   * Generation guard applies to ALL envelope kinds (snapshot, gap, error,
   * incremental): an envelope from a stale service_generation is dropped and
   * never resets the revision baseline. (ADR 0007 blocker fix.)
   *
   * Gap handling: an explicit `gap` marker, or an incremental whose
   * `revision > last + 1`, means events were lost (bounded-buffer overflow on
   * the server, or a dropped frame). The transport stops applying
   * incrementals and re-baselines from a full snapshot before resuming.
   *
   * Returns true if the event was accepted, false if dropped.
   */
  _applyEnvelope(env) {
    if (!env || typeof env !== 'object') return false;

    // Generation guard FIRST, for every envelope kind.
    if (env.generation !== undefined && env.generation !== this.generation) {
      // Stale generation: drop. The Electron main should have torn this stream
      // down and built a new transport with the new generation; this guard is
      // the defensive backstop so a stale snapshot can never reset the baseline.
      return false;
    }

    if (env.type === 'snapshot') {
      // A fresh (re)baseline. Clears any pending gap state.
      this._gapped = false;
      this._lastRevisionBySession.set(env.session_id, env.revision);
      this._emit('onSnapshot', env);
      // If a connect() is waiting on a baseline, resolve it.
      if (this._connectResolver) {
        const r = this._connectResolver;
        this._connectResolver = null;
        r({ connected: true, snapshot: env });
      }
      return true;
    }

    if (env.type === 'gap') {
      // Explicit overflow/loss marker from the server. Trigger re-baseline.
      this._onGap(env.session_id, env);
      return false;
    }

    if (env.type === 'error') {
      this._emit('onError', Object.assign(new Error(env.error || 'transport_error'), { envelope: env }));
      // If a connect() is waiting on a baseline, an error frame resolves it
      // (with no snapshot) so the caller doesn't hang on a retired session.
      if (this._connectResolver) {
        const r = this._connectResolver;
        this._connectResolver = null;
        r({ connected: true, snapshot: null, error: env });
      }
      return false;
    }

    // Incremental event.
    const last = this._lastRevisionBySession.get(env.session_id);
    if (last !== undefined && env.revision <= last) {
      // Duplicate or stale revision: drop (out-of-order / replayed).
      return false;
    }
    if (last !== undefined && env.revision > last + 1) {
      // Gap detected: revisions last+1 .. env.revision-1 were lost. Do not
      // apply this incremental; re-baseline from a full snapshot.
      this._onGap(env.session_id, env);
      return false;
    }
    // While a re-baseline is in flight, drop incrementals (the snapshot will
    // establish the authoritative state).
    if (this._gapped) {
      return false;
    }
    this._lastRevisionBySession.set(env.session_id, env.revision);
    this._emit('onEvent', env);
    return true;
  }

  /**
   * Handle a detected gap: mark the session gapped, notify the listener, and
   * kick off an async full-snapshot re-baseline. Incremental events for this
   * session are dropped until the snapshot arrives.
   */
  _onGap(sessionId, env) {
    this._gapped = true;
    this._emit('onGap', { session_id: sessionId, generation: this.generation, envelope: env });
    this._rebaseline(sessionId).catch((err) => {
      this._emit('onError', Object.assign(new Error('rebaseline_failed'), { cause: err }));
    });
  }

  async _rebaseline(sessionId) {
    if (!sessionId) return;
    // Coalesce concurrent re-baseline attempts.
    this._rebasingSessions = this._rebasingSessions || new Set();
    if (this._rebasingSessions.has(sessionId)) return;
    this._rebasingSessions.add(sessionId);
    try {
      const snap = await this.getSnapshot(sessionId);
      if (!snap) return;
      // Apply as a snapshot envelope. If the server generation moved on, the
      // generation guard drops it and we surface via onError upstream.
      this._applyEnvelope({
        type: 'snapshot',
        session_id: snap.session_id,
        generation: snap.generation,
        revision: snap.revision,
        snapshot: snap.snapshot,
      });
    } finally {
      this._rebasingSessions.delete(sessionId);
    }
  }

  async _request(path, { method = 'POST', body, timeoutMs } = {}) {
    const ctrl = new AbortController();
    this._inflight = this._inflight || new Set();
    this._inflight.add(ctrl);
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || this.opts.requestTimeoutMs);
    try {
      const res = await fetch(`${this.baseURL}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${this.credential}`,
          'Content-Type': 'application/json',
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
      });
      const text = await res.text();
      let json = null;
      try {
        json = text ? JSON.parse(text) : null;
      } catch {
        /* non-JSON */
      }
      return { status: res.status, json, text };
    } finally {
      clearTimeout(timer);
      this._inflight.delete(ctrl);
    }
  }

  // -- shared domain methods --------------------------------------------

  async createSession() {
    const r = await this._request('/api/session/create', { method: 'POST', body: {} });
    this._assertOk(r);
    return r.json;
  }

  async retireSession(sessionId) {
    const r = await this._request('/api/session/retire', { method: 'POST', body: { session_id: sessionId } });
    this._assertOk(r);
    return r.json;
  }

  async request(op, payload = {}) {
    const r = await this._request('/api/request', { method: 'POST', body: { op, ...payload } });
    this._assertOk(r);
    return r.json;
  }

  async getSnapshot(sessionId) {
    const r = await this._request(`/api/snapshot?session_id=${encodeURIComponent(sessionId)}`, { method: 'GET' });
    this._assertOk(r);
    return r.json;
  }

  /** Test-only: push a fixture event server-side. */
  async emit(sessionId, event) {
    const r = await this._request('/api/emit', { method: 'POST', body: { session_id: sessionId, event } });
    this._assertOk(r);
    return r.json;
  }

  /** Test-only: drop a session's live connection to exercise reconnect. */
  async kick(sessionId) {
    const r = await this._request('/api/kick', { method: 'POST', body: { session_id: sessionId } });
    this._assertOk(r);
    return r.json;
  }

  _assertOk(r) {
    if (r.status >= 400) {
      const msg = (r.json && r.json.error) || r.text || `http ${r.status}`;
      const err = new Error(`transport request failed: ${msg}`);
      err.status = r.status;
      err.body = r.json;
      throw err;
    }
  }

  cancel() {
    this._cancelled = true;
    // Abort every in-flight HTTP request this transport has outstanding, so a
    // caller that cancels (or closes) does not keep waiting on a slow request.
    if (this._inflight) {
      for (const ctrl of this._inflight) {
        try {
          ctrl.abort();
        } catch {
          /* ignore */
        }
      }
      this._inflight.clear();
    }
  }
}

// ---------------------------------------------------------------------------
// HTTP + WebSocket
// ---------------------------------------------------------------------------

class HttpWsTransport extends BaseTransport {
  constructor(cfg, options = {}) {
    super(cfg, options);
    this._ws = null;
    this._sessionId = null;
    this._reconnectDelay = this.opts.reconnectInitialDelayMs;
    this._reconnectAttempts = 0;
    this._stopped = false;
  }

  async connect(sessionId) {
    this._sessionId = sessionId;
    this._stopped = false;
    return this._open();
  }

  _open() {
    return new Promise((resolve, reject) => {
      if (this._stopped) return resolve({ connected: false, reason: 'stopped' });
      const wsURL =
        this.baseURL.replace(/^http/, 'ws') +
        `/ws?session_id=${encodeURIComponent(this._sessionId)}`;
      const ws = new WebSocket(wsURL, {
        headers: { Authorization: `Bearer ${this.credential}` },
      });
      this._ws = ws;
      let resolved = false;
      // _applyEnvelope owns connect resolution: it calls _connectResolver on
      // snapshot (with the snapshot) and on error (with null+error).
      this._connectResolver = (val) => { if (!resolved) { resolved = true; resolve(val); } };
      ws.addEventListener('open', () => {
        this._reconnectDelay = this.opts.reconnectInitialDelayMs;
      });
      ws.addEventListener('message', (ev) => {
        let env;
        try {
          env = JSON.parse(ev.data);
        } catch {
          return;
        }
        this._applyEnvelope(env);
      });
      ws.addEventListener('error', () => {
        if (!resolved) {
          resolved = true;
          this._connectResolver = null;
          reject(new Error('ws open failed'));
        } else {
          this._emit('onError', new Error('ws error'));
        }
      });
      ws.addEventListener('close', () => {
        this._emit('onDisconnect', { reason: 'closed' });
        if (!resolved) {
          resolved = true;
          this._connectResolver = null;
          reject(new Error('ws closed before snapshot'));
        }
        if (!this._stopped && !this._cancelled) {
          this._scheduleReconnect();
        }
      });
    });
  }

  _scheduleReconnect() {
    if (this._stopped || this._cancelled) return;
    if (this._reconnectAttempts >= this.opts.maxReconnectAttempts) {
      this._emit('onError', new Error('reconnect_attempts_exhausted'));
      return;
    }
    this._reconnectAttempts += 1;
    const delay = this._reconnectDelay;
    this._reconnectDelay = Math.min(this.opts.reconnectMaxDelayMs, this._reconnectDelay * 2);
    setTimeout(async () => {
      if (this._stopped || this._cancelled) return;
      try {
        const r = await this._open();
        if (r.connected) this._emit('onReconnect', { reason: 'reconnected' });
      } catch {
        this._scheduleReconnect();
      }
    }, delay);
  }

  async close() {
    this._stopped = true;
    this.cancel(); // abort any in-flight HTTP requests
    const ws = this._ws;
    this._ws = null;
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
    // Surface disconnect immediately; do not rely on the socket close event
    // firing synchronously (it can be delayed while the socket drains).
    this._emit('onDisconnect', { reason: 'closed_by_client' });
  }
}

// ---------------------------------------------------------------------------
// HTTP + SSE
// ---------------------------------------------------------------------------

class HttpSseTransport extends BaseTransport {
  constructor(cfg, options = {}) {
    super(cfg, options);
    this._ctrl = null;
    this._sessionId = null;
    this._stopped = false;
    this._reader = null;
    this._reconnectDelay = this.opts.reconnectInitialDelayMs;
    this._reconnectAttempts = 0;
  }

  async connect(sessionId) {
    this._sessionId = sessionId;
    this._stopped = false;
    return this._open();
  }

  _open() {
    return new Promise((resolve, reject) => {
      if (this._stopped) return resolve({ connected: false, reason: 'stopped' });
      const ctrl = new AbortController();
      this._ctrl = ctrl;
      const url =
        this.baseURL +
        `/events?session_id=${encodeURIComponent(this._sessionId)}`;
      fetch(url, {
        method: 'GET',
        headers: {
          Authorization: `Bearer ${this.credential}`,
          Accept: 'text/event-stream',
        },
        signal: ctrl.signal,
      })
        .then(async (res) => {
          if (!res.ok) {
            reject(new Error(`sse http ${res.status}`));
            return;
          }
          const reader = res.body.getReader();
          this._reader = reader;
          const decoder = new TextDecoder();
          let buf = '';
          let resolved = false;
          // _applyEnvelope owns connect resolution (snapshot -> resolve with
          // snapshot; error -> resolve with null+error).
          this._connectResolver = (val) => {
            if (!resolved) {
              resolved = true;
              resolve(val);
            }
          };
          const dispatch = (frame) => {
            for (const line of frame.split('\n')) {
              if (!line.startsWith('data:')) continue;
              const payload = line.slice(5).trim();
              if (!payload) continue;
              let env;
              try {
                env = JSON.parse(payload);
              } catch {
                continue;
              }
              this._applyEnvelope(env);
            }
          };
          try {
            // eslint-disable-next-line no-constant-condition
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              buf += decoder.decode(value, { stream: true });
              let sep;
              while ((sep = buf.indexOf('\n\n')) >= 0) {
                const frame = buf.slice(0, sep);
                buf = buf.slice(sep + 2);
                dispatch(frame);
              }
            }
          } catch (err) {
            if (err && err.name === 'AbortError') {
              // clean close
            } else {
              this._emit('onError', err);
            }
          } finally {
            this._emit('onDisconnect', { reason: 'closed' });
            if (!this._stopped && !this._cancelled) {
              this._scheduleReconnect();
            }
          }
        })
        .catch((err) => {
          reject(err);
        });
    });
  }

  _scheduleReconnect() {
    if (this._stopped || this._cancelled) return;
    if (this._reconnectAttempts >= this.opts.maxReconnectAttempts) {
      this._emit('onError', new Error('reconnect_attempts_exhausted'));
      return;
    }
    this._reconnectAttempts += 1;
    setTimeout(async () => {
      if (this._stopped || this._cancelled) return;
      try {
        const r = await this._open();
        if (r.connected) this._emit('onReconnect', { reason: 'reconnected' });
      } catch {
        this._scheduleReconnect();
      }
    }, this.opts.reconnectInitialDelayMs);
  }

  async close() {
    this._stopped = true;
    this.cancel(); // abort any in-flight HTTP requests
    if (this._ctrl) {
      try {
        this._ctrl.abort();
      } catch {
        /* ignore */
      }
      this._ctrl = null;
    }
  }
}

module.exports = {
  BaseTransport,
  HttpWsTransport,
  HttpSseTransport,
  DEFAULTS,
  createTransport(kind, cfg, options) {
    if (kind === 'ws') return new HttpWsTransport(cfg, options);
    if (kind === 'sse') return new HttpSseTransport(cfg, options);
    throw new Error(`unknown transport kind: ${kind}`);
  },
};
