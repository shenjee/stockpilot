import { buildSafeBridge } from "../electron/safe-bridge.mjs";

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

function clone(value) {
  return value === undefined ? undefined : structuredClone(value);
}

export function createFakeSafeBridge(fixture, { replayFixture = null } = {}) {
  const listeners = new Map();
  const calls = [];
  const serviceStatus = {
    state: "ready",
    service_generation: fixture.service_generation,
    message: "Fake Safe Bridge 已就绪",
  };

  function emit(channel, payload) {
    for (const listener of listeners.get(channel) ?? []) listener(clone(payload));
  }

  const bridge = buildSafeBridge({
    invoke: async (command, request) => {
      calls.push({ command, request: clone(request) });
      if (command === "get_service_status") return clone(serviceStatus);
      if (command === "get_live_snapshot") {
        return {
          schema_version: "t0_app_v1",
          request_id: request.request_id,
          accepted: true,
          operation_id: null,
          data: clone(fixture.initial_snapshot_event.payload),
          error: null,
        };
      }
      if (command === "select_symbol") {
        return {
          schema_version: "t0_replay_v1",
          request_id: request.request_id,
          service_generation: fixture.service_generation,
          security: {
            symbol: "sh.600000",
            code: "600000",
            market: "sh",
            name: "浦发银行",
            security_type: "a_share",
          },
        };
      }
      if (command === "get_replay_snapshot") {
        if (!replayFixture?.snapshot) throw new Error("Replay fixture is required");
        return clone(replayFixture.snapshot);
      }
      if (REPLAY_COMMANDS.has(command)) {
        const createsOperation = new Set([
          "begin_replay",
          "step_replay",
          "seek_replay",
        ]).has(command);
        return {
          schema_version: "t0_replay_v1",
          request_id: request.request_id,
          service_generation: fixture.service_generation,
          session_id: request.session_id ?? "replay-fixture-1",
          ...(createsOperation ? { operation_id: `operation-${command}` } : {}),
        };
      }
      return {
        schema_version: "t0_app_v1",
        request_id: request?.request_id ?? "fake-request",
        accepted: true,
        operation_id: null,
        data: null,
        error: null,
      };
    },
    subscribe: (channel, listener) => {
      const channelListeners = listeners.get(channel) ?? new Set();
      channelListeners.add(listener);
      listeners.set(channel, channelListeners);
      return () => channelListeners.delete(listener);
    },
  });

  const controller = Object.freeze({
    calls,
    emitServiceStatus: (status = serviceStatus) => emit("service_status", status),
    emitInitialSnapshot: () => emit("app_event", fixture.initial_snapshot_event),
    emitIncrementals: () => {
      for (const event of fixture.incremental_events) emit("app_event", event);
    },
    emitOutOfOrder: () => {
      for (const index of fixture.out_of_order_delivery) {
        emit("app_event", fixture.incremental_events[index]);
      }
    },
    emitError: () => emit("app_event", fixture.operation_failed_event),
    emitReplayEvent: (event) => emit("replay_event", event),
    emitReplaySnapshot: (snapshot) => emit("replay_snapshot", snapshot),
  });

  return { bridge, controller };
}
