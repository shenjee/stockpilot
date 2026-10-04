import { EventEmitter } from "node:events";

export const RESTART_REQUIRED_MESSAGE =
  "应用内部服务异常，成交记录可能未更新。请退出应用后重新打开。";

const ALLOWED_COMMANDS = new Set([
  "search_securities",
  "resolve_security_identity",
  "select_security",
  "save_last_symbol",
  "get_live_snapshot",
  "retry_live",
  "list_trades",
  "list_trade_history",
  "create_trade",
  "update_trade",
  "delete_trade",
  "list_fee_plans",
  "create_fee_plan",
  "update_fee_plan",
  "delete_fee_plan",
  "calculate_trade_fee",
  "get_preferences",
  "save_preferences",
  "get_historical_snapshot",
  "select_symbol",
  "begin_replay",
  "set_replay_playback",
  "set_replay_speed",
  "step_replay",
  "seek_replay",
  "end_replay",
  "get_replay_snapshot",
]);

const REPLAY_COMMANDS = new Set([
  "select_symbol",
  "begin_replay",
  "set_replay_playback",
  "set_replay_speed",
  "step_replay",
  "seek_replay",
  "end_replay",
  "get_replay_snapshot",
]);

function serviceUnavailable(requestId = "service-unavailable") {
  return {
    error_code: "service_unavailable",
    category: "service",
    severity: "error",
    retryable: true,
    affected_capability: "service",
    message: "本地服务尚未就绪",
    request_id: requestId,
    details: {},
  };
}

function synchronousFailure(command, request, error = serviceUnavailable(request?.request_id)) {
  if (REPLAY_COMMANDS.has(command)) return error;
  return {
    schema_version: "t0_app_v2",
    request_id: request?.request_id ?? error.request_id,
    accepted: false,
    operation_id: null,
    data: null,
    error,
  };
}

function isStructuredFailure(command, payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return false;
  if (REPLAY_COMMANDS.has(command)) {
    return typeof payload.error_code === "string";
  }
  return (
    payload.accepted === false
    && payload.error
    && typeof payload.error === "object"
    && typeof payload.error.error_code === "string"
  );
}

function errorMessage(error) {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

export class BackendGateway extends EventEmitter {
  constructor({
    fetchImpl = globalThis.fetch,
    WebSocketImpl = globalThis.WebSocket,
    requestTimeoutMs = 5_000,
    commandTimeouts = {},
    maxReconnectAttempts = 3,
    reconnectBackoffMs = [100, 250, 500],
    maxEventBuffer = 128,
  } = {}) {
    super();
    this.fetchImpl = fetchImpl;
    this.WebSocketImpl = WebSocketImpl;
    this.requestTimeoutMs = requestTimeoutMs;
    this.commandTimeouts = commandTimeouts;
    this.maxReconnectAttempts = maxReconnectAttempts;
    this.reconnectBackoffMs = reconnectBackoffMs;
    this.maxEventBuffer = maxEventBuffer;
    this.restartRequiredStatus = null;
    this.connection = null;
    this.socket = null;
    this.closed = true;
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this.pendingRequests = new Set();
    this.pendingEvents = [];
    this.draining = false;
    this.baselines = new Map();
    this.lastEnvelopeByKey = new Map();
    this.rebaselining = new Set();
  }

  start(connection) {
    // A lost service stream is terminal for this app lifetime. Ordinary host
    // readiness/retry must not make the UI appear recovered.
    if (this.restartRequiredStatus) return;
    this.close();
    this.connection = Object.freeze({ ...connection });
    this.closed = false;
    this.reconnectAttempts = 0;
    this.baselines.clear();
    this.lastEnvelopeByKey.clear();
    this.#connect();
  }

  async invoke(command, request) {
    if (!ALLOWED_COMMANDS.has(command)) throw new Error(`Safe Bridge command is not allowed: ${command}`);
    if (this.restartRequiredStatus) {
      return synchronousFailure(command, request, {
        ...serviceUnavailable(request?.request_id),
        retryable: false,
        message: RESTART_REQUIRED_MESSAGE,
      });
    }
    if (!this.connection || this.closed) return synchronousFailure(command, request);
    const controller = new AbortController();
    this.pendingRequests.add(controller);
    const timeoutMs = this.commandTimeouts[command] ?? this.requestTimeoutMs;
    try {
      try {
        const response = await this.fetchImpl(
          `http://${this.connection.host}:${this.connection.port}/api/commands/${command}`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${this.connection.token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(request ?? {}),
            signal: AbortSignal.any([
              controller.signal,
              AbortSignal.timeout(timeoutMs),
            ]),
          },
        );
        const payload = await response.json();
        if (response.ok) return payload;
        return isStructuredFailure(command, payload)
          ? payload
          : synchronousFailure(command, request);
      } catch {
        return synchronousFailure(command, request);
      }
    } finally {
      this.pendingRequests.delete(controller);
    }
  }

  #connect() {
    if (this.closed || !this.connection || !this.WebSocketImpl) return;
    const { host, port, token } = this.connection;
    const socket = new this.WebSocketImpl(
      `ws://${host}:${port}/events`,
      [`stockpilot-auth.${token}`],
    );
    this.socket = socket;
    socket.addEventListener("open", () => {
      if (this.socket !== socket || this.closed) return;
      const reconnected = this.reconnectAttempts > 0;
      this.reconnectAttempts = 0;
      this.emit("service-status", {
        state: "connected",
        service_generation: this.connection.service_generation,
        message: "本地服务事件通道已连接",
      });
      if (reconnected) {
        for (const envelope of this.lastEnvelopeByKey.values()) {
          void this.#rebaseline(envelope);
        }
      }
    });
    socket.addEventListener("message", (event) => {
      if (this.socket !== socket || this.closed) return;
      try {
        this.#enqueue(JSON.parse(String(event.data)));
      } catch (error) {
        this.emit("diagnostic", { stream: "stderr", message: `invalid backend event: ${error}` });
      }
    });
    socket.addEventListener("error", () => {
      // close owns the bounded reconnect path.
    });
    socket.addEventListener("close", () => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (!this.closed) this.#scheduleReconnect();
    });
  }

  #scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.emit("service-status", {
        state: "disconnected",
        service_generation: this.connection?.service_generation ?? 0,
        message: "本地服务事件通道重连失败",
      });
      return;
    }
    const index = this.reconnectAttempts++;
    const delay = this.reconnectBackoffMs[Math.min(index, this.reconnectBackoffMs.length - 1)] ?? 0;
    this.emit("service-status", {
      state: "restarting",
      service_generation: this.connection?.service_generation ?? 0,
      message: `正在重新连接本地服务（${this.reconnectAttempts}/${this.maxReconnectAttempts}）`,
    });
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.#connect();
    }, delay);
  }

  #enqueue(envelope) {
    if (this.closed) return;
    if (envelope.service_generation !== this.connection?.service_generation) return;
    if (this.pendingEvents.length >= this.maxEventBuffer) {
      const discarded = [...this.pendingEvents, envelope];
      if (discarded.some((pending) => !pending.session_id)) {
        this.#stopIncompleteStream("service event buffer overflow");
        return;
      }
      const affected = new Map();
      for (const pending of discarded) {
        if (!pending.session_id) continue;
        affected.set(`${pending.schema_version}:${pending.session_id}`, pending);
      }
      this.pendingEvents.length = 0;
      for (const pending of affected.values()) void this.#rebaseline(pending);
      return;
    }
    this.pendingEvents.push(envelope);
    if (!this.draining) {
      this.draining = true;
      queueMicrotask(() => this.#drain());
    }
  }

  #drain() {
    try {
      while (this.pendingEvents.length) {
        const envelope = this.pendingEvents.shift();
        const key = `${envelope.schema_version}:${envelope.session_id ?? "service"}`;
        const currentRevision = this.baselines.get(key);
        if (currentRevision !== undefined && envelope.revision <= currentRevision) continue;
        // Initialization is a watermark, not a new broadcast. Even a +1
        // watermark on reconnect means one service fact was missed.
        if (!envelope.session_id && envelope.event_type === "service_status"
            && currentRevision !== undefined && envelope.revision > currentRevision) {
          this.#stopIncompleteStream(
            `service messages missed before reconnect: last ${currentRevision}, watermark ${envelope.revision}`,
          );
          break;
        }
        if (currentRevision !== undefined && envelope.revision > currentRevision + 1) {
          if (!envelope.session_id) {
            this.#stopIncompleteStream(
              `service revision gap: expected ${currentRevision + 1}, received ${envelope.revision}`,
            );
            break;
          }
          void this.#rebaseline(envelope);
          continue;
        }
        this.baselines.set(key, envelope.revision);
        if (envelope.session_id) this.lastEnvelopeByKey.set(key, envelope);
        const replay = envelope.schema_version === "t0_replay_v2";
        this.emit(replay ? "replay-event" : "app-event", envelope);
        if (replay && envelope.event_type === "workbench_snapshot") {
          this.emit("replay-snapshot", envelope.payload);
        }
      }
    } finally {
      this.draining = false;
    }
  }

  #stopIncompleteStream(reason) {
    if (this.restartRequiredStatus) return;
    this.restartRequiredStatus = Object.freeze({
      state: "failed",
      service_generation: this.connection?.service_generation ?? 0,
      message: RESTART_REQUIRED_MESSAGE,
    });
    this.close();
    this.emit("diagnostic", { stream: "stderr", message: reason });
    this.emit("service-status", this.restartRequiredStatus);
  }

  async #rebaseline(envelope) {
    if (!envelope.session_id) return;
    const replay = envelope.schema_version === "t0_replay_v2";
    const key = `${envelope.schema_version}:${envelope.session_id}`;
    if (this.rebaselining.has(key)) return;
    this.rebaselining.add(key);
    const connection = this.connection;
    const socket = this.socket;
    try {
      const command = replay ? "get_replay_snapshot" : "get_live_snapshot";
      const response = await this.invoke(command, {
        schema_version: envelope.schema_version,
        request_id: `rebaseline-${envelope.service_generation}-${envelope.session_id}-${envelope.revision}`,
        session_id: envelope.session_id,
        ...(replay ? {} : { command, payload: {} }),
      });
      if (this.closed || this.connection !== connection || this.socket !== socket) return;
      if (response?.accepted === false || response?.error_code) {
        throw new Error(
          `snapshot request rejected: ${response.error?.error_code ?? response.error_code}`,
        );
      }
      const snapshot = response.data ?? response.snapshot ?? response;
      const revision = snapshot?.session?.revision;
      if (!Number.isInteger(revision)) {
        throw new Error("snapshot response is missing session.revision");
      }
      this.baselines.set(key, revision);
      const baselineEnvelope = {
        schema_version: replay ? "t0_replay_v2" : "t0_app_v2",
        service_generation: envelope.service_generation,
        session_id: envelope.session_id,
        revision,
        event_type: "workbench_snapshot",
        payload: snapshot,
      };
      this.emit(replay ? "replay-event" : "app-event", baselineEnvelope);
      if (replay) this.emit("replay-snapshot", snapshot);
    } catch (error) {
      this.emit("diagnostic", {
        stream: "stderr",
        message: `snapshot rebaseline failed: ${errorMessage(error)}`,
      });
    } finally {
      this.rebaselining.delete(key);
    }
  }

  close() {
    this.closed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    for (const controller of this.pendingRequests) controller.abort();
    this.pendingRequests.clear();
    this.pendingEvents.length = 0;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }
}

export { ALLOWED_COMMANDS };
