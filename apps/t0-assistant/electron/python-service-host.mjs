import { EventEmitter } from "node:events";
import { createServer } from "node:net";
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const moduleDir = dirname(fileURLToPath(import.meta.url));
const backendEntry = resolve(moduleDir, "../backend/service.py");
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
    pythonExecutable = process.env.T0_PYTHON || "python3",
    serviceEntry = process.env.T0_BACKEND_ENTRY || backendEntry,
    generation = 1,
    maxRestarts = 3,
    restartBackoffMs = [100, 250, 500],
    fetchImpl = globalThis.fetch,
    spawnImpl = spawn,
    findOpenPortImpl = findOpenPort,
    sleepImpl = sleep,
  } = {}) {
    super();
    this.pythonExecutable = pythonExecutable;
    this.serviceEntry = serviceEntry;
    this.generation = generation;
    this.maxRestarts = maxRestarts;
    this.restartBackoffMs = restartBackoffMs;
    this.fetchImpl = fetchImpl;
    this.spawnImpl = spawnImpl;
    this.findOpenPortImpl = findOpenPortImpl;
    this.sleepImpl = sleepImpl;
    this.child = null;
    this.port = null;
    this.token = null;
    this.state = "stopped";
    this.restartCount = 0;
    this.stopping = false;
    this.restartEpoch = 0;
    this.startInFlight = null;
    this.stopInFlight = null;
  }

  rendererStatus(message = "") {
    return {
      state: this.state,
      service_generation: this.generation,
      message,
    };
  }

  connectionInfo() {
    if (this.state !== "ready" || !this.port || !this.token) return null;
    return Object.freeze({
      host: "127.0.0.1",
      port: this.port,
      token: this.token,
      service_generation: this.generation,
    });
  }

  #setState(state, message) {
    this.state = state;
    this.emit("status", this.rendererStatus(message));
  }

  start(options = {}) {
    if (this.startInFlight) return this.startInFlight;
    const startInFlight = this.#start(options);
    this.startInFlight = startInFlight;
    const clearStart = () => {
      if (this.startInFlight === startInFlight) this.startInFlight = null;
    };
    startInFlight.then(clearStart, clearStart);
    return startInFlight;
  }

  async #start({ timeoutMs = 8_000, resetRestartCount = true } = {}) {
    if (this.stopInFlight) await this.stopInFlight;
    if (this.child) return this.rendererStatus("本地服务已启动");
    if (resetRestartCount) this.restartCount = 0;
    this.stopping = false;
    const epoch = ++this.restartEpoch;
    return this.#startOnce({ timeoutMs, epoch, publishState: true });
  }

  async #startOnce({ timeoutMs, epoch, publishState }) {
    if (publishState) this.#setState("starting", "正在启动本地服务…");
    let child = null;
    let failureMessage = "本地服务启动失败";
    let terminationAttempted = false;
    try {
      this.port = await this.findOpenPortImpl();
      if (this.stopping || epoch !== this.restartEpoch) {
        throw new Error("Python service start was cancelled");
      }
      this.token = randomBytes(32).toString("hex");
      child = this.spawnImpl(
        this.pythonExecutable,
        [this.serviceEntry, "--host", "127.0.0.1", "--port", String(this.port), "--service-generation", String(this.generation)],
        {
          env: { ...process.env, T0_SERVICE_TOKEN: this.token },
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
      this.child = child;
      child.stdout?.on("data", (chunk) => this.emit("diagnostic", { stream: "stdout", message: String(chunk) }));
      child.stderr?.on("data", (chunk) => this.emit("diagnostic", { stream: "stderr", message: String(chunk) }));
      child.once("error", (error) => this.#handleProcessError(child, error));
      child.once("exit", (code, signal) => this.#handleExit(child, code, signal));

      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline && epoch === this.restartEpoch && !this.stopping) {
        if (this.child !== child) break;
        try {
          const response = await this.fetchImpl(`http://127.0.0.1:${this.port}/health`, {
            headers: { Authorization: `Bearer ${this.token}` },
            signal: AbortSignal.timeout(500),
          });
          if (response.ok && this.child === child && !this.stopping) {
            this.#setState("ready", "本地服务已就绪");
            return this.rendererStatus("本地服务已就绪");
          }
        } catch {
          // Connection failures and non-2xx responses share the bounded retry path.
        }
        const remainingMs = deadline - Date.now();
        if (remainingMs > 0 && this.child === child) {
          await this.sleepImpl(Math.min(50, remainingMs));
        }
      }

      const exitedBeforeReady = this.child !== child;
      failureMessage = exitedBeforeReady ? "本地服务启动失败" : "本地服务启动超时";
      if (!exitedBeforeReady) {
        terminationAttempted = true;
        await this.#terminateChild(child);
      }
      throw new Error("Python service did not become ready before timeout");
    } catch (error) {
      if (child && this.child === child && !terminationAttempted) {
        try {
          await this.#terminateChild(child);
        } catch (terminationError) {
          this.emit("diagnostic", { stream: "stderr", message: String(terminationError) });
        }
      }
      if (!this.child || this.child === child) {
        this.child = null;
        this.port = null;
        this.token = null;
      }
      if (!this.stopping && epoch === this.restartEpoch && publishState) {
        this.#setState("failed", failureMessage);
      }
      throw error;
    }
  }

  #handleProcessError(child, error) {
    this.emit("diagnostic", {
      stream: "stderr",
      message: `本地服务进程错误: ${error instanceof Error ? error.message : String(error)}`,
    });
    if (this.child !== child) return;
    const previousState = this.state;
    this.child = null;
    this.port = null;
    this.token = null;
    if (this.stopping || previousState === "stopping" || previousState === "stopped") return;
    if (previousState === "ready") {
      void this.#restartAfterCrash(null, "process-error");
    }
  }

  #handleExit(child, code, signal) {
    if (this.child !== child) return;
    const previousState = this.state;
    this.child = null;
    this.port = null;
    this.token = null;
    if (this.stopping || previousState === "stopping" || previousState === "stopped") return;
    if (previousState === "starting" || previousState === "restarting") return;
    if (previousState === "ready") {
      void this.#restartAfterCrash(code, signal);
      return;
    }
    this.#setState("failed", `本地服务已退出（code=${code}, signal=${signal}）`);
  }

  async #restartAfterCrash(code, signal) {
    if (this.restartCount >= this.maxRestarts) {
      this.#setState("failed", "本地服务自动重启次数已用尽，请手动重试");
      return;
    }
    this.restartCount += 1;
    this.generation += 1;
    const epoch = ++this.restartEpoch;
    this.#setState(
      "restarting",
      `本地服务已断开，正在重启（${this.restartCount}/${this.maxRestarts}）`,
    );
    const delay = this.restartBackoffMs[Math.min(this.restartCount - 1, this.restartBackoffMs.length - 1)] ?? 0;
    await this.sleepImpl(delay);
    if (this.stopping || epoch !== this.restartEpoch) return;
    try {
      await this.#startOnce({ timeoutMs: 8_000, epoch, publishState: false });
    } catch (error) {
      if (!this.stopping && epoch === this.restartEpoch) {
        this.emit("diagnostic", { stream: "stderr", message: String(error) });
        await this.#restartAfterCrash(code, signal);
      }
    }
  }

  async #terminateChild(child, { timeoutMs = 2_000, termTimeoutMs = 1_000, killTimeoutMs = 1_000 } = {}) {
    try {
      if (this.port && this.token) {
        await this.fetchImpl(`http://127.0.0.1:${this.port}/shutdown`, {
          method: "POST",
          headers: { Authorization: `Bearer ${this.token}` },
          signal: AbortSignal.timeout(500),
        });
      }
    } catch {
      // The bounded signal fallback owns final termination.
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
    if (!exited) throw new Error("Python service did not exit after SIGKILL");
    if (this.child === child) this.child = null;
  }

  stop(options = {}) {
    if (this.stopInFlight) return this.stopInFlight;
    const stopInFlight = this.#stopOnce(options);
    this.stopInFlight = stopInFlight;
    const clearStop = () => {
      if (this.stopInFlight === stopInFlight) this.stopInFlight = null;
    };
    stopInFlight.then(clearStop, clearStop);
    return stopInFlight;
  }

  async #stopOnce(options) {
    this.stopping = true;
    this.restartEpoch += 1;
    if (!this.child) {
      this.port = null;
      this.token = null;
      this.#setState("stopped", "本地服务已停止");
      return;
    }
    this.#setState("stopping", "正在停止本地服务…");
    const child = this.child;
    try {
      await this.#terminateChild(child, options);
    } catch (error) {
      if (this.child === child) this.child = null;
      this.port = null;
      this.token = null;
      this.emit("diagnostic", { stream: "stderr", message: String(error) });
      this.#setState("failed", "无法停止本地服务");
      throw error;
    }
    this.port = null;
    this.token = null;
    this.#setState("stopped", "本地服务已停止");
  }
}
