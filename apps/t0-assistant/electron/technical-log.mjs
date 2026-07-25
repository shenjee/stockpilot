import { appendFile, mkdir, readFile, rename, stat } from "node:fs/promises";
import { dirname } from "node:path";

export class RotatingTechnicalLog {
  constructor(path, { maxBytes = 1_000_000, maxFiles = 3 } = {}) {
    if (!path || maxBytes < 1 || maxFiles < 1) {
      throw new TypeError("A path and positive rotation limits are required");
    }
    this.path = path;
    this.maxBytes = maxBytes;
    this.maxFiles = maxFiles;
    this.pending = Promise.resolve();
  }

  write(stream, message, timestamp = new Date()) {
    const line = `${timestamp.toISOString()} [${stream}] ${redact(message).trim()}\n`;
    this.pending = this.pending
      .catch(() => undefined)
      .then(async () => {
        await mkdir(dirname(this.path), { recursive: true });
        await this.#rotateIfNeeded(Buffer.byteLength(line));
        await appendFile(this.path, line, { encoding: "utf8", mode: 0o600 });
      });
    return this.pending;
  }

  async read() {
    await this.pending;
    const files = [];
    for (let index = this.maxFiles; index >= 1; index -= 1) {
      files.push(`${this.path}.${index}`);
    }
    files.push(this.path);
    const contents = [];
    for (const path of files) {
      try {
        contents.push(await readFile(path, "utf8"));
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }
    }
    return contents.join("");
  }

  async #rotateIfNeeded(incomingBytes) {
    let size = 0;
    try {
      size = (await stat(this.path)).size;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    if (size + incomingBytes <= this.maxBytes) return;
    for (let index = this.maxFiles; index >= 1; index -= 1) {
      const source = index === 1 ? this.path : `${this.path}.${index - 1}`;
      const target = `${this.path}.${index}`;
      try {
        await rename(source, target);
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }
    }
  }
}

function redact(value) {
  return String(value)
    .replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]")
    .replace(/stockpilot-auth\.\S+/gi, "stockpilot-auth.[REDACTED]")
    .replace(
      /((?:token|credential|password|secret|api[_-]?key)\s*["']?\s*[:=]\s*["']?)[^"',}\s]+/gi,
      "$1[REDACTED]",
    );
}
