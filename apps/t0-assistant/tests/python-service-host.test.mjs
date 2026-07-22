import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PythonServiceHost } from "../electron/python-service-host.mjs";

function createFakeChild({ exitOnSignal = null } = {}) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  child.signals = [];
  child.kill = (signal) => {
    child.signals.push(signal);
    if (signal === exitOnSignal) {
      queueMicrotask(() => {
        child.signalCode = signal;
        child.emit("exit", null, signal);
      });
    }
    return true;
  };
  return child;
}

test("Electron host starts and gracefully stops the authenticated fake service", async () => {
  const host = new PythonServiceHost({ pythonExecutable: process.env.T0_PYTHON || "python", generation: 4 });
  try {
    const status = await host.start();
    assert.deepEqual(status, {
      state: "ready",
      service_generation: 4,
      message: "本地服务已就绪",
    });
    assert.equal(Object.hasOwn(status, "port"), false);
    assert.equal(Object.hasOwn(status, "token"), false);
    const health = await fetch(`http://127.0.0.1:${host.port}/health`, {
      headers: { Authorization: `Bearer ${host.token}` },
    });
    assert.equal(health.status, 200);
  } finally {
    await host.stop();
  }
  assert.equal(host.child, null);
  assert.equal(host.state, "stopped");
});

test("health polling is paced when the service responds with non-2xx statuses", async () => {
  const child = createFakeChild();
  let healthCalls = 0;
  const sleepCalls = [];
  const host = new PythonServiceHost({
    findOpenPortImpl: async () => 43123,
    spawnImpl: () => child,
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) {
        healthCalls += 1;
        return { ok: false };
      }
      child.exitCode = 0;
      queueMicrotask(() => child.emit("exit", 0, null));
      return { ok: true };
    },
    sleepImpl: async (timeoutMs) => {
      sleepCalls.push(timeoutMs);
      await new Promise((resolveWait) => setTimeout(resolveWait, timeoutMs));
    },
  });

  await assert.rejects(
    host.start({ timeoutMs: 125 }),
    /did not become ready before timeout/,
  );

  assert.ok(sleepCalls.length >= 2);
  assert.ok(sleepCalls.every((timeoutMs) => timeoutMs > 0 && timeoutMs <= 50));
  assert.ok(healthCalls <= 4, `expected paced polling, received ${healthCalls} health calls`);
  assert.equal(host.child, null);
  assert.equal(host.state, "failed");
});

test("stop waits for SIGTERM and escalates to SIGKILL before returning", async () => {
  const child = createFakeChild({ exitOnSignal: "SIGKILL" });
  const host = new PythonServiceHost({
    fetchImpl: async () => {
      throw new Error("shutdown endpoint unavailable");
    },
  });
  host.child = child;
  host.port = 43124;
  host.token = "test-token";
  host.state = "ready";

  await host.stop({ timeoutMs: 5, termTimeoutMs: 5, killTimeoutMs: 50 });

  assert.deepEqual(child.signals, ["SIGTERM", "SIGKILL"]);
  assert.equal(host.child, null);
  assert.equal(host.port, null);
  assert.equal(host.token, null);
  assert.equal(host.state, "stopped");
});
