'use strict';

/**
 * Preload safe bridge (ADR 0007).
 *
 * The Renderer-facing contract. It exposes ONLY domain-oriented methods and an
 * event subscription. It deliberately does NOT expose:
 *   - the Python URL, host, or port;
 *   - the per-launch credential/token;
 *   - the WebSocket / SSE transport kind;
 *   - any raw fetch / IPC pass-through.
 *
 * In a real Electron app this object would be returned from a preload script
 * under `contextBridge.exposeInMainWorld('stockpilot', ...)`. Here it is a
 * plain class so the isolation contract can be unit-tested headlessly: the
 * test inspects the returned object's own enumerable keys to prove nothing
 * sensitive leaks.
 *
 * Electron main wires a `gateway` (the transport client) into the bridge; the
 * bridge translates domain calls into gateway calls and projects domain events
 * to the renderer.
 */

/**
 * @typedef {Object} GatewayLike
 * @property {(op:string, payload?:object) => Promise<object>} request
 * @property {() => Promise<object>} createSession
 * @property {(sid:string) => Promise<object>} retireSession
 * @property {(sid:string) => Promise<object>} getSnapshot
 * @property {(listeners: object) => void} on
 * @property {(sid:string) => Promise<{connected:boolean, snapshot?:object}>} connect
 * @property {() => Promise<void>} close
 * @property {() => void} cancel
 */

const ALLOWED_OPS = new Set([
  'select_symbol',
  'begin_replay',
  'seek_replay',
  'end_replay',
  'save_trade',
  'update_trade',
  'delete_trade',
  'get_fee_policy',
  'save_preferences',
]);

class SafeBridge {
  /**
   * @param {GatewayLike} gateway
   */
  constructor(gateway) {
    this._gateway = gateway;
    this._session = null;
    this._eventHandler = null;
    this._snapshotHandler = null;
    this._statusHandler = null;
  }

  // -- domain methods (the renderer's entire vocabulary) -----------------

  async selectSymbol(symbol) {
    const r = await this._gateway.request('select_symbol', { symbol });
    return { accepted: true, requestId: r && r.request_id };
  }

  async beginReplay(date) {
    return this._gateway.request('begin_replay', { date });
  }

  async seekReplay(timestamp) {
    return this._gateway.request('seek_replay', { timestamp });
  }

  async endReplay() {
    return this._gateway.request('end_replay');
  }

  async saveTrade(trade) {
    return this._gateway.request('save_trade', { trade });
  }

  async getFeePolicy() {
    return this._gateway.request('get_fee_policy');
  }

  // -- session lifecycle --------------------------------------------------

  async start() {
    const created = await this._gateway.createSession();
    this._session = created.session_id;
    const r = await this._gateway.connect(this._session);
    return { session: this._session, connected: r.connected };
  }

  async stop() {
    if (this._session) {
      try {
        await this._gateway.retireSession(this._session);
      } catch {
        /* ignore */
      }
    }
    await this._gateway.close();
    this._session = null;
  }

  // -- event subscription (typed, no raw stream) -------------------------

  onSnapshot(handler) {
    this._snapshotHandler = handler;
    this._wire();
    return this;
  }

  onEvent(handler) {
    this._eventHandler = handler;
    this._wire();
    return this;
  }

  onStatus(handler) {
    this._statusHandler = handler;
    return this;
  }

  _wire() {
    this._gateway.on({
      onSnapshot: (snap) => {
        if (this._snapshotHandler) {
          // Project only domain-relevant fields; never the transport envelope.
          this._snapshotHandler({
            session: snap.session_id,
            revision: snap.revision,
            data: snap.snapshot,
          });
        }
      },
      onEvent: (env) => {
        if (this._eventHandler) {
          this._eventHandler({
            session: env.session_id,
            revision: env.revision,
            type: env.type,
            data: env.payload,
          });
        }
      },
      onDisconnect: () => {
        if (this._statusHandler) this._statusHandler({ state: 'disconnected' });
      },
      onReconnect: () => {
        if (this._statusHandler) this._statusHandler({ state: 'reconnected' });
      },
      onError: () => {
        if (this._statusHandler) this._statusHandler({ state: 'error' });
      },
    });
  }

  /**
   * Returns the frozen list of method names the renderer may call. This is the
   * "allowlist" the isolation test checks.
   */
  static allowedMethods() {
    return Object.freeze([
      'selectSymbol',
      'beginReplay',
      'seekReplay',
      'endReplay',
      'saveTrade',
      'getFeePolicy',
      'start',
      'stop',
      'onSnapshot',
      'onEvent',
      'onStatus',
    ]);
  }
}

/**
 * Build the renderer-facing object. Only the allowlisted keys are present;
 * nothing about URL/port/credential/transport is attached.
 */
function buildRendererApi(gateway) {
  const bridge = new SafeBridge(gateway);
  const api = {
    selectSymbol: (s) => bridge.selectSymbol(s),
    beginReplay: (d) => bridge.beginReplay(d),
    seekReplay: (t) => bridge.seekReplay(t),
    endReplay: () => bridge.endReplay(),
    saveTrade: (t) => bridge.saveTrade(t),
    getFeePolicy: () => bridge.getFeePolicy(),
    start: () => bridge.start(),
    stop: () => bridge.stop(),
    onSnapshot: (h) => bridge.onSnapshot(h),
    onEvent: (h) => bridge.onEvent(h),
    onStatus: (h) => bridge.onStatus(h),
  };
  return Object.freeze(api);
}

module.exports = { SafeBridge, buildRendererApi, ALLOWED_OPS };
