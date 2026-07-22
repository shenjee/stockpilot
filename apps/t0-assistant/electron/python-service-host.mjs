import { EventEmitter } from "node:events";
import { createServer } from "node:net";
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const moduleDir = dirname(fileURLToPath(import.meta.url));
const backendEntry = resolve(moduleDir, "../backend/fake_service.py");
const sleep = (timeoutMs) => new Promise((resolveWait) => setTimeout(resolveWait, timeoutMs));

async function findOpenPort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => (error ? reject(error) : resolvePort(port)));
    });
  });
}

async function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return true;
  return new Promise((resolveExit) => {
    const onExit = () => {
      clearTimeout(timer);
      resolveExit(true);
    };
    const timer = setTimeout(() => {
      child.removeListener("exit", onExit);
      resolveExit(false);
    }, timeoutMs);
    child.once("exit", onExit);
  });
}

export class PythonServiceHost extends EventEmitter {
  constructor({
    pythonExecutable = process.env.T0_PYTHON || "python",
    generation = 1,
    fetchImpl = globalThis.fetch,
    spawnImpl = spawn,
    findOpenPortImpl = findOpenPort,
    sleepImpl = sleep,
  } = {}) {
    super();
    this.pythonExecutable = pythonExecutable;
    this.generation = generation;
    this.fetchImpl = fetchImpl;
    this.spawnImpl = spawnImpl;
    this.findOpenPortImpl = findOpenPortImpl;
    this.sleepImpl = sleepImpl;
    this.child = null;
    this.port = null;
    this.token = null;
    this.state = "stopped";
  }

  rendererStatus(message = "") {
    return {
      state: this.state,
      service_generation: this.generation,
      message,
    };
  }

  #setState(state, message) {
    this.state = state;
    this.emit("status", this.rendererStatus(message));
  }

  async start({ timeoutMs = 8_000 } = {}) {
    if (this.child) return this.rendererStatus("本地服务已启动");
    this.#setState("starting", "正在启动本地服务…");
    this.port = await this.findOpenPortImpl();
    this.token = randomBytes(32).toString("hex");
    const child = this.spawnImpl(
      this.pythonExecutable,
      [backendEntry, "--host", "127.0.0.1", "--port", String(this.port), "--service-generation", String(this.generation)],
      {
        env: { ...process.env, T0_SERVICE_TOKEN: this.token },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    this.child = child;
    child.stdout.on("data", (chunk) => this.emit("diagnostic", { stream: "stdout", message: String(chunk) }));
    child.stderr.on("data", (chunk) => this.emit("diagnostic", { stream: "stderr", message: String(chunk) }));
    child.once("exit", (code, signal) => {
      if (this.child === child) this.child = null;
      if (this.state !== "stopping" && this.state !== "stopped") {
        this.#setState("failed", `本地服务已退出（code=${code}, signal=${signal}）`);
      }
    });

    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (!this.child) break;
      try {
        const response = await this.fetchImpl(`http://127.0.0.1:${this.port}/health`, {
          headers: { Authorization: `Bearer ${this.token}` },
          signal: AbortSignal.timeout(500),
        });
        if (response.ok) {
          this.#setState("ready", "本地服务已就绪");
          return this.rendererStatus("本地服务已就绪");
        }
      } catch {
        // Connection failures and non-2xx responses share the bounded retry path.
      }
      const remainingMs = deadline - Date.now();
      if (remainingMs > 0 && this.child) await this.sleepImpl(Math.min(50, remainingMs));
    }
    await this.stop();
    this.#setState("failed", "本地服务启动超时");
    throw new Error("Python fake service did not become ready before timeout");
  }

  async stop({ timeoutMs = 2_000, termTimeoutMs = 1_000, killTimeoutMs = 1_000 } = {}) {
    if (!this.child) {
      this.#setState("stopped", "本地服务已停止");
      return;
    }
    this.#setState("stopping", "正在停止本地服务…");
    const child = this.child;
    try {
      await this.fetchImpl(`http://127.0.0.1:${this.port}/shutdown`, {
        method: "POST",
        headers: { Authorization: `Bearer ${this.token}` },
        signal: AbortSignal.timeout(500),
      });
    } catch {
      // The bounded fallback below owns final termination.
    }
    let exited = await waitForExit(child, timeoutMs);
    if (!exited) {
      child.kill("SIGTERM");
      exited = await waitForExit(child, termTimeoutMs);
    }
    if (!exited) {
      child.kill("SIGKILL");
      exited = await waitForExit(child, killTimeoutMs);
    }
    if (!exited) {
      this.#setState("failed", "无法停止本地服务");
      throw new Error("Python service did not exit after SIGKILL");
    }
    if (this.child === child) this.child = null;
    this.port = null;
    this.token = null;
    this.#setState("stopped", "本地服务已停止");
  }
}
