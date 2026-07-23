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

function waitForStatus(host, expected) {
  if (host.state === expected) return Promise.resolve();
  return new Promise((resolveWait) => {
    const listener = (status) => {
      if (status.state !== expected) return;
      host.off("status", listener);
      resolveWait();
    };
    host.on("status", listener);
  });
}

test("Electron host starts and gracefully stops the authenticated formal service", async () => {
  const host = new PythonServiceHost({ generation: 4 });
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
  const failedStatuses = [];
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
  host.on("status", (status) => {
    if (status.state === "failed") failedStatuses.push(status);
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
  assert.deepEqual(failedStatuses.map((status) => status.message), ["本地服务启动超时"]);
});

test("spawn errors become a recoverable startup failure instead of an uncaught process error", async () => {
  const children = [];
  const diagnostics = [];
  const host = new PythonServiceHost({
    findOpenPortImpl: async () => 43125,
    spawnImpl: () => {
      const child = createFakeChild({ exitOnSignal: "SIGTERM" });
      children.push(child);
      if (children.length === 1) {
        queueMicrotask(() => child.emit("error", Object.assign(new Error("spawn python ENOENT"), { code: "ENOENT" })));
      }
      return child;
    },
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) return { ok: children.length > 1 };
      return { ok: true };
    },
    sleepImpl: async () => {},
  });
  host.on("diagnostic", (diagnostic) => diagnostics.push(diagnostic.message));

  await assert.rejects(host.start({ timeoutMs: 50 }), /did not become ready/);
  assert.equal(host.state, "failed");
  assert.equal(host.child, null);
  assert.equal(host.port, null);
  assert.equal(host.token, null);
  assert.ok(diagnostics.some((message) => message.includes("ENOENT")));

  await host.start({ timeoutMs: 50 });
  assert.equal(host.state, "ready");
  assert.equal(children.length, 2);
  await host.stop({ timeoutMs: 1, termTimeoutMs: 20, killTimeoutMs: 20 });
});

test("port allocation failures publish failed and leave startup retryable", async () => {
  let attempts = 0;
  const child = createFakeChild({ exitOnSignal: "SIGTERM" });
  const host = new PythonServiceHost({
    findOpenPortImpl: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("no file descriptors");
      return 43126;
    },
    spawnImpl: () => child,
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) return { ok: true };
      return { ok: true };
    },
  });

  await assert.rejects(host.start(), /no file descriptors/);
  assert.equal(host.state, "failed");
  assert.equal(host.child, null);

  await host.start();
  assert.equal(host.state, "ready");
  await host.stop({ timeoutMs: 1, termTimeoutMs: 20, killTimeoutMs: 20 });
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

test("a stop timeout clears stale process state so a later start can recover", async () => {
  const children = [];
  const diagnostics = [];
  const host = new PythonServiceHost({
    findOpenPortImpl: async () => 43200 + children.length,
    spawnImpl: () => {
      const child = createFakeChild({
        exitOnSignal: children.length === 0 ? null : "SIGTERM",
      });
      children.push(child);
      return child;
    },
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) return { ok: true };
      throw new Error("shutdown endpoint unavailable");
    },
  });
  host.on("diagnostic", (diagnostic) => diagnostics.push(diagnostic.message));

  await host.start();
  await assert.rejects(
    host.stop({ timeoutMs: 1, termTimeoutMs: 1, killTimeoutMs: 1 }),
    /did not exit after SIGKILL/,
  );

  assert.equal(host.state, "failed");
  assert.equal(host.child, null);
  assert.equal(host.port, null);
  assert.equal(host.token, null);
  assert.ok(diagnostics.some((message) => message.includes("SIGKILL")));

  const status = await host.start();
  assert.equal(status.state, "ready");
  assert.equal(children.length, 2);
  await host.stop({ timeoutMs: 1, termTimeoutMs: 20, killTimeoutMs: 20 });
});

test("runtime crash performs a bounded restart with a new generation and credential", async () => {
  const children = [];
  const host = new PythonServiceHost({
    generation: 7,
    maxRestarts: 2,
    restartBackoffMs: [0],
    findOpenPortImpl: async () => 44000 + children.length,
    spawnImpl: () => {
      const child = createFakeChild({ exitOnSignal: "SIGTERM" });
      children.push(child);
      return child;
    },
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) return { ok: true };
      const child = children.at(-1);
      child.exitCode = 0;
      queueMicrotask(() => child.emit("exit", 0, null));
      return { ok: true };
    },
    sleepImpl: async () => {},
  });

  await host.start();
  const oldToken = host.token;
  const first = children[0];
  first.exitCode = 1;
  first.emit("exit", 1, null);
  const restarted = waitForStatus(host, "ready");
  await restarted;

  assert.equal(host.generation, 8);
  assert.equal(children.length, 2);
  assert.notEqual(host.token, oldToken);
  assert.equal(host.restartCount, 1);
  await host.stop();
});

test("successful automatic retry does not publish a transient failed state", async () => {
  const children = [];
  const states = [];
  const host = new PythonServiceHost({
    maxRestarts: 2,
    restartBackoffMs: [0],
    findOpenPortImpl: async () => 44500 + children.length,
    spawnImpl: () => {
      const child = createFakeChild({ exitOnSignal: "SIGTERM" });
      children.push(child);
      if (children.length === 2) {
        queueMicrotask(() => child.emit("error", new Error("restart spawn failed")));
      }
      return child;
    },
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) return { ok: children.length !== 2 };
      return { ok: true };
    },
    sleepImpl: async () => {},
  });
  host.on("status", (status) => states.push(status.state));

  await host.start();
  const restarting = waitForStatus(host, "restarting");
  children[0].exitCode = 1;
  children[0].emit("exit", 1, null);
  await restarting;
  const readyAgain = waitForStatus(host, "ready");
  await readyAgain;

  assert.equal(children.length, 3);
  assert.equal(states.filter((state) => state === "failed").length, 0);
  await host.stop({ timeoutMs: 1, termTimeoutMs: 20, killTimeoutMs: 20 });
});

test("an immediately exiting restart attempt does not publish a transient failed state", async () => {
  const children = [];
  const states = [];
  const host = new PythonServiceHost({
    maxRestarts: 2,
    restartBackoffMs: [0],
    findOpenPortImpl: async () => 44600 + children.length,
    spawnImpl: () => {
      const child = createFakeChild({ exitOnSignal: "SIGTERM" });
      children.push(child);
      if (children.length === 2) {
        queueMicrotask(() => {
          child.exitCode = 1;
          child.emit("exit", 1, null);
        });
      }
      return child;
    },
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) return { ok: children.length !== 2 };
      return { ok: true };
    },
    sleepImpl: async () => {},
  });
  host.on("status", (status) => states.push(status.state));

  await host.start();
  const restarting = waitForStatus(host, "restarting");
  children[0].exitCode = 1;
  children[0].emit("exit", 1, null);
  await restarting;
  const readyAgain = waitForStatus(host, "ready");
  await readyAgain;

  assert.equal(children.length, 3);
  assert.equal(states.filter((state) => state === "failed").length, 0);
  await host.stop({ timeoutMs: 1, termTimeoutMs: 20, killTimeoutMs: 20 });
});

test("immediately exiting restart attempts publish failed only when retries are exhausted", async () => {
  const children = [];
  const failedMessages = [];
  const host = new PythonServiceHost({
    maxRestarts: 2,
    restartBackoffMs: [0],
    findOpenPortImpl: async () => 44700 + children.length,
    spawnImpl: () => {
      const child = createFakeChild();
      children.push(child);
      if (children.length > 1) {
        queueMicrotask(() => {
          child.exitCode = 1;
          child.emit("exit", 1, null);
        });
      }
      return child;
    },
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) return { ok: children.length === 1 };
      return { ok: true };
    },
    sleepImpl: async () => {},
  });
  host.on("status", (status) => {
    if (status.state === "failed") failedMessages.push(status.message);
  });

  await host.start();
  const exhausted = waitForStatus(host, "failed");
  children[0].exitCode = 1;
  children[0].emit("exit", 1, null);
  await exhausted;

  assert.equal(children.length, 3);
  assert.deepEqual(failedMessages, ["本地服务自动重启次数已用尽，请手动重试"]);
});

test("start requested during stop waits for shutdown and then starts a new service", async () => {
  const children = [];
  let releaseShutdown;
  const shutdownReleased = new Promise((resolveWait) => { releaseShutdown = resolveWait; });
  const host = new PythonServiceHost({
    findOpenPortImpl: async () => 44800 + children.length,
    spawnImpl: () => {
      const child = createFakeChild({ exitOnSignal: "SIGTERM" });
      children.push(child);
      return child;
    },
    fetchImpl: async (url) => {
      if (url.endsWith("/health")) return { ok: true };
      await shutdownReleased;
      const child = children[0];
      child.exitCode = 0;
      queueMicrotask(() => child.emit("exit", 0, null));
      return { ok: true };
    },
  });

  await host.start();
  const stopping = host.stop({ timeoutMs: 20, termTimeoutMs: 20, killTimeoutMs: 20 });
  assert.equal(host.state, "stopping");
  const startingAgain = host.start();
  const duplicateStart = host.start();
  assert.equal(duplicateStart, startingAgain);
  releaseShutdown();
  await stopping;
  const [status, duplicateStatus] = await Promise.all([startingAgain, duplicateStart]);

  assert.equal(status.state, "ready");
  assert.equal(duplicateStatus.state, "ready");
  assert.equal(host.state, "ready");
  assert.equal(children.length, 2);
  await host.stop({ timeoutMs: 1, termTimeoutMs: 20, killTimeoutMs: 20 });
});

test("shutdown during restart backoff cancels the pending respawn", async () => {
  const children = [];
  let releaseBackoff;
  const backoff = new Promise((resolveWait) => { releaseBackoff = resolveWait; });
  const host = new PythonServiceHost({
    maxRestarts: 2,
    findOpenPortImpl: async () => 45000 + children.length,
    spawnImpl: () => {
      const child = createFakeChild({ exitOnSignal: "SIGTERM" });
      children.push(child);
      return child;
    },
    fetchImpl: async () => ({ ok: true }),
    sleepImpl: async (timeoutMs) => {
      if (timeoutMs >= 100) await backoff;
    },
  });

  await host.start();
  const restarting = waitForStatus(host, "restarting");
  const first = children[0];
  first.exitCode = 1;
  first.emit("exit", 1, null);
  await restarting;
  await host.stop();
  releaseBackoff();
  await new Promise((resolveWait) => setImmediate(resolveWait));

  assert.equal(host.state, "stopped");
  assert.equal(children.length, 1);
  assert.equal(host.child, null);
});

test("restart exhaustion stops the loop and keeps the Renderer projection secret-free", async () => {
  const children = [];
  const states = [];
  const host = new PythonServiceHost({
    maxRestarts: 1,
    restartBackoffMs: [0],
    findOpenPortImpl: async () => 46000 + children.length,
    spawnImpl: () => {
      const child = createFakeChild();
      children.push(child);
      return child;
    },
    fetchImpl: async () => ({ ok: true }),
    sleepImpl: async () => {},
  });
  host.on("status", (status) => states.push(status.state));
  await host.start();
  children[0].exitCode = 1;
  children[0].emit("exit", 1, null);
  const firstRestart = waitForStatus(host, "ready");
  await firstRestart;
  const exhausted = waitForStatus(host, "failed");
  children[1].exitCode = 1;
  children[1].emit("exit", 1, null);
  await exhausted;

  const status = host.rendererStatus();
  assert.equal(children.length, 2);
  assert.equal(status.state, "failed");
  assert.equal(Object.hasOwn(status, "port"), false);
  assert.equal(Object.hasOwn(status, "token"), false);
  assert.equal(Object.hasOwn(status, "pythonExecutable"), false);
  assert.equal(Object.hasOwn(status, "serviceEntry"), false);
  assert.equal(states.filter((state) => state === "failed").length, 1);
});
