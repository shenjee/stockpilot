import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { ALLOWED_COMMANDS, BackendGateway } from "../electron/backend-gateway.mjs";
import { PythonServiceHost } from "../electron/python-service-host.mjs";

class FakeWebSocket {
  static instances = [];

  constructor(url, protocols) {
    this.url = url;
    this.protocols = protocols;
    this.events = new EventEmitter();
    FakeWebSocket.instances.push(this);
  }

  addEventListener(name, listener) {
    this.events.on(name, listener);
  }

  open() {
    this.events.emit("open", {});
  }

  message(data) {
    this.events.emit("message", { data: JSON.stringify(data) });
  }

  close() {
    this.events.emit("close", {});
  }
}

class DelayedCloseWebSocket extends FakeWebSocket {
  close() {
    this.closeRequested = true;
  }

  finishClose() {
    this.events.emit("close", {});
  }
}

const connection = {
  host: "127.0.0.1",
  port: 43123,
  token: "private-token",
  service_generation: 4,
};

function tick() {
  return new Promise((resolveWait) => setImmediate(resolveWait));
}

test("gateway authenticates inside main and never adds transport fields to domain requests", async () => {
  let captured;
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return {
        ok: true,
        json: async () => ({
          schema_version: "t0_app_v1",
          request_id: "req-1",
          accepted: true,
          operation_id: null,
          data: null,
          error: null,
        }),
      };
    },
  });
  gateway.start(connection);
  const request = {
    schema_version: "t0_app_v1",
    request_id: "req-1",
    command: "get_preferences",
    session_id: null,
    payload: {},
  };
  await gateway.invoke("get_preferences", request);

  assert.equal(captured.url, "http://127.0.0.1:43123/api/commands/get_preferences");
  assert.equal(captured.options.headers.Authorization, "Bearer private-token");
  assert.deepEqual(JSON.parse(captured.options.body), request);
  assert.equal(Object.hasOwn(request, "token"), false);
  assert.equal(FakeWebSocket.instances.at(-1).protocols[0], "stockpilot-auth.private-token");
  gateway.close();
});

test("gateway drops stale revisions and requests a full snapshot on a gap", async () => {
  const requests = [];
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    fetchImpl: async (_url, options) => {
      const request = JSON.parse(options.body);
      requests.push(request);
      return {
        ok: true,
        json: async () => ({
          schema_version: "t0_app_v1",
          request_id: request.request_id,
          accepted: true,
          operation_id: null,
          data: {
            session: { session_id: "live-1", revision: 4 },
          },
          error: null,
        }),
      };
    },
  });
  const accepted = [];
  gateway.on("app-event", (event) => accepted.push(event));
  gateway.start(connection);
  const socket = FakeWebSocket.instances.at(-1);
  socket.open();
  const event = (revision) => ({
    schema_version: "t0_app_v1",
    service_generation: 4,
    session_id: "live-1",
    revision,
    event_type: "market_update",
    payload: { target: "bars_1m", bars: [], quote: null },
  });
  socket.message(event(1));
  await tick();
  socket.message(event(1));
  socket.message(event(3));
  await tick();

  assert.deepEqual(accepted.map((item) => item.revision), [1, 4]);
  assert.equal(accepted[1].event_type, "workbench_snapshot");
  assert.equal(requests[0].command, "get_live_snapshot");
  gateway.close();
});

test("gateway rejects old generations and non-allowlisted commands", async () => {
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    fetchImpl: async () => { throw new Error("must not call"); },
  });
  const accepted = [];
  gateway.on("app-event", (event) => accepted.push(event));
  gateway.start(connection);
  const socket = FakeWebSocket.instances.at(-1);
  socket.message({
    schema_version: "t0_app_v1",
    service_generation: 3,
    session_id: "old",
    revision: 1,
    event_type: "market_update",
    payload: {},
  });
  await tick();

  assert.equal(accepted.length, 0);
  await assert.rejects(gateway.invoke("raw_fetch", {}), /not allowed/);
  assert.equal(ALLOWED_COMMANDS.has("raw_fetch"), false);
  gateway.close();
});

test("gateway resolves structured service errors across the IPC boundary", async () => {
  const offlineGateway = new BackendGateway({ WebSocketImpl: FakeWebSocket });
  const appFailure = await offlineGateway.invoke("get_preferences", {
    schema_version: "t0_app_v1",
    request_id: "offline-app",
    command: "get_preferences",
    session_id: null,
    payload: {},
  });
  const replayFailure = await offlineGateway.invoke("step_replay", {
    schema_version: "t0_replay_v1",
    request_id: "offline-replay",
    session_id: "replay-1",
  });

  assert.equal(appFailure.accepted, false);
  assert.equal(appFailure.error.error_code, "service_unavailable");
  assert.equal(replayFailure.error_code, "service_unavailable");
  assert.equal(replayFailure.category, "service");

  const networkGateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    fetchImpl: async () => { throw new TypeError("fetch failed"); },
  });
  networkGateway.start(connection);
  const networkFailure = await networkGateway.invoke("get_preferences", {
    schema_version: "t0_app_v1",
    request_id: "network-app",
    command: "get_preferences",
    session_id: null,
    payload: {},
  });
  assert.equal(networkFailure.accepted, false);
  assert.equal(networkFailure.error.error_code, "service_unavailable");
  assert.equal(networkFailure.error.category, "service");
  networkGateway.close();
});

test("gateway normalizes non-contract HTTP error objects before returning to Renderer", async () => {
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    fetchImpl: async () => ({
      ok: false,
      status: 401,
      json: async () => ({ status: "unauthorized" }),
    }),
  });
  gateway.start(connection);

  const failure = await gateway.invoke("get_preferences", {
    schema_version: "t0_app_v1",
    request_id: "unauthorized-app",
    command: "get_preferences",
    session_id: null,
    payload: {},
  });

  assert.equal(failure.accepted, false);
  assert.equal(failure.error.error_code, "service_unavailable");
  assert.equal(failure.error.request_id, "unauthorized-app");
  gateway.close();
});

test("bounded event overflow discards the partial tail and re-baselines from a snapshot", async () => {
  const requests = [];
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    maxEventBuffer: 2,
    fetchImpl: async (_url, options) => {
      const request = JSON.parse(options.body);
      requests.push(request);
      return {
        ok: true,
        json: async () => ({
          schema_version: "t0_app_v1",
          request_id: request.request_id,
          accepted: true,
          operation_id: null,
          data: { session: { session_id: "live-overflow", revision: 9 } },
          error: null,
        }),
      };
    },
  });
  gateway.start(connection);
  const socket = FakeWebSocket.instances.at(-1);
  for (const revision of [1, 2, 3]) {
    socket.message({
      schema_version: "t0_app_v1",
      service_generation: 4,
      session_id: "live-overflow",
      revision,
      event_type: "market_update",
      payload: { target: "bars_1m", bars: [], quote: null },
    });
  }
  await tick();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].command, "get_live_snapshot");
  assert.equal(requests[0].session_id, "live-overflow");
  gateway.close();
});

test("reconnect re-baselines every active Session from a full snapshot", async () => {
  const requests = [];
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    reconnectBackoffMs: [0],
    fetchImpl: async (_url, options) => {
      const request = JSON.parse(options.body);
      requests.push(request);
      return {
        ok: true,
        json: async () => ({
          schema_version: "t0_app_v1",
          request_id: request.request_id,
          accepted: true,
          operation_id: null,
          data: { session: { session_id: "live-reconnect", revision: 7 } },
          error: null,
        }),
      };
    },
  });
  gateway.start(connection);
  const firstSocket = FakeWebSocket.instances.at(-1);
  firstSocket.open();
  firstSocket.message({
    schema_version: "t0_app_v1",
    service_generation: 4,
    session_id: "live-reconnect",
    revision: 1,
    event_type: "market_update",
    payload: { target: "bars_1m", bars: [], quote: null },
  });
  await tick();
  firstSocket.close();
  await new Promise((resolveWait) => setTimeout(resolveWait, 5));
  const secondSocket = FakeWebSocket.instances.at(-1);
  assert.notEqual(secondSocket, firstSocket);
  secondSocket.open();
  await tick();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].command, "get_live_snapshot");
  assert.equal(requests[0].session_id, "live-reconnect");
  gateway.close();
});

test("a stale socket close cannot schedule a phantom reconnect over a new connection", async () => {
  const statuses = [];
  const gateway = new BackendGateway({
    WebSocketImpl: DelayedCloseWebSocket,
    reconnectBackoffMs: [0],
    fetchImpl: async () => ({ ok: true, json: async () => ({}) }),
  });
  gateway.on("service-status", (status) => statuses.push(status.state));
  gateway.start(connection);
  const oldSocket = FakeWebSocket.instances.at(-1);
  oldSocket.open();

  gateway.start({ ...connection, service_generation: 5 });
  const currentSocket = FakeWebSocket.instances.at(-1);
  assert.notEqual(currentSocket, oldSocket);
  assert.equal(oldSocket.closeRequested, true);
  currentSocket.open();
  oldSocket.finishClose();
  await new Promise((resolveWait) => setTimeout(resolveWait, 5));

  assert.equal(FakeWebSocket.instances.at(-1), currentSocket);
  assert.equal(statuses.filter((state) => state === "restarting").length, 0);
  gateway.close();
});

test("Replay gap rebaseline emits both the revisioned event and snapshot projection", async () => {
  const snapshot = {
    session: {
      session_id: "replay-gap",
      session_type: "replay",
      symbol: "sh.600000",
      trade_date: "2026-07-01",
      state: "paused",
      revision: 4,
    },
  };
  const requests = [];
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    fetchImpl: async (_url, options) => {
      requests.push(JSON.parse(options.body));
      return { ok: true, json: async () => snapshot };
    },
  });
  const events = [];
  const snapshots = [];
  gateway.on("replay-event", (event) => events.push(event));
  gateway.on("replay-snapshot", (value) => snapshots.push(value));
  gateway.start(connection);
  const socket = FakeWebSocket.instances.at(-1);
  const event = (revision) => ({
    schema_version: "t0_replay_v1",
    service_generation: 4,
    session_id: "replay-gap",
    revision,
    event_type: "session_status",
    payload: { state: "paused", reason: "seek_completed" },
  });
  socket.message(event(1));
  await tick();
  socket.message(event(3));
  await tick();

  assert.equal(requests[0].schema_version, "t0_replay_v1");
  assert.equal(requests[0].session_id, "replay-gap");
  assert.deepEqual(events.map((item) => item.revision), [1, 4]);
  assert.equal(events[1].event_type, "workbench_snapshot");
  assert.equal(events[1].payload, snapshot);
  assert.deepEqual(snapshots, [snapshot]);
  gateway.close();
});

test("rebaseline diagnostics preserve the concrete snapshot rejection reason", async () => {
  const diagnostics = [];
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        schema_version: "t0_app_v1",
        request_id: "rejected-snapshot",
        accepted: false,
        operation_id: null,
        data: null,
        error: {
          error_code: "service_unavailable",
          category: "service",
          severity: "error",
          retryable: true,
          affected_capability: "service",
          message: "not ready",
          request_id: "rejected-snapshot",
          details: {},
        },
      }),
    }),
  });
  gateway.on("diagnostic", (diagnostic) => diagnostics.push(diagnostic.message));
  gateway.start(connection);
  const socket = FakeWebSocket.instances.at(-1);
  const event = (revision) => ({
    schema_version: "t0_app_v1",
    service_generation: 4,
    session_id: "diagnostic-live",
    revision,
    event_type: "market_update",
    payload: {},
  });
  socket.message(event(1));
  await tick();
  socket.message(event(3));
  await tick();

  assert.ok(
    diagnostics.some((message) => message.includes("snapshot request rejected: service_unavailable")),
  );
  gateway.close();
});

test("buffer overflow re-baselines every Session whose queued event was discarded", async () => {
  const requests = [];
  const gateway = new BackendGateway({
    WebSocketImpl: FakeWebSocket,
    maxEventBuffer: 2,
    fetchImpl: async (_url, options) => {
      const request = JSON.parse(options.body);
      requests.push(request);
      return {
        ok: true,
        json: async () => ({
          schema_version: "t0_app_v1",
          request_id: request.request_id,
          accepted: true,
          operation_id: null,
          data: { session: { session_id: request.session_id, revision: 9 } },
          error: null,
        }),
      };
    },
  });
  gateway.start(connection);
  const socket = FakeWebSocket.instances.at(-1);
  const event = (sessionId, revision) => ({
    schema_version: "t0_app_v1",
    service_generation: 4,
    session_id: sessionId,
    revision,
    event_type: "market_update",
    payload: { target: "bars_1m", bars: [], quote: null },
  });
  socket.message(event("live-a", 1));
  socket.message(event("live-b", 1));
  await tick();
  socket.message(event("live-a", 2));
  socket.message(event("live-b", 2));
  socket.message(event("live-a", 3));
  await tick();

  assert.deepEqual(
    requests.map((request) => request.session_id).sort(),
    ["live-a", "live-b"],
  );
  gateway.close();
});

test("managed Python service authenticates transport and rejects unavailable domain commands explicitly", async () => {
  const host = new PythonServiceHost({
    generation: 6,
  });
  const gateway = new BackendGateway();
  try {
    await host.start();
    const connected = new Promise((resolveConnected, reject) => {
      const timer = setTimeout(() => reject(new Error("gateway did not connect")), 2_000);
      gateway.once("service-status", (status) => {
        if (status.state !== "connected") return;
        clearTimeout(timer);
        resolveConnected(status);
      });
    });
    const firstEvent = new Promise((resolveEvent, reject) => {
      const timer = setTimeout(() => reject(new Error("gateway did not receive an event")), 2_000);
      gateway.once("app-event", (event) => {
        clearTimeout(timer);
        resolveEvent(event);
      });
    });
    gateway.start(host.connectionInfo());
    const [status, event] = await Promise.all([connected, firstEvent]);
    const rejection = await gateway.invoke("get_live_snapshot", {
      schema_version: "t0_app_v1",
      request_id: "integration-snapshot",
      command: "get_live_snapshot",
      session_id: "live-fixture-1",
      payload: {},
    });

    assert.equal(status.service_generation, 6);
    assert.equal(event.event_type, "service_status");
    assert.equal(rejection.accepted, false);
    assert.equal(rejection.error.error_code, "service_unavailable");
    assert.equal(Object.hasOwn(status, "port"), false);
    assert.equal(Object.hasOwn(status, "token"), false);
  } finally {
    gateway.close();
    await host.stop();
  }
});
