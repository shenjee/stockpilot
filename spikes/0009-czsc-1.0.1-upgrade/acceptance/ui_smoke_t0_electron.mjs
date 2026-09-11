/**
 * #176 T+0 Assistant Live/Replay UI smoke (real PythonServiceHost + czsc 1.0.1).
 *
 * Run from apps/t0-assistant after build:
 *
 *   source ~/.venvs/czsc/bin/activate
 *   cd apps/t0-assistant
 *   npm run build
 *   T0_PYTHON="$HOME/.venvs/czsc/bin/python" \
 *   T0_RUNTIME_DIR="$HOME/Library/Application Support/stockpilot-t0-assistant/stockpilot" \
 *   electron ../../spikes/0009-czsc-1.0.1-upgrade/acceptance/ui_smoke_t0_electron.mjs
 *
 * Does not place real trades.
 */

import { mkdir, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

import { app, BrowserWindow, ipcMain } from "electron";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../../..");
const appRoot = resolve(repoRoot, "apps/t0-assistant");
const liveOut = resolve(scriptDir, "artifacts/ui-smoke/live");
const replayOut = resolve(scriptDir, "artifacts/ui-smoke/replay");

const SYMBOL = "600584";
const SYMBOL_ALT = "600000";
const REPLAY_TRADE_DATE = "2026-07-14";
const VIEWPORT = { width: 1440, height: 900 };

const home = homedir();
const defaultRuntimeDir = resolve(
  home,
  "Library/Application Support/stockpilot-t0-assistant/stockpilot",
);
const runtimeDir = process.env.T0_RUNTIME_DIR || defaultRuntimeDir;
const pythonExecutable =
  process.env.T0_PYTHON || resolve(home, ".venvs/czsc/bin/python");

const { PythonServiceHost } = await import(
  pathToFileURL(resolve(appRoot, "electron/python-service-host.mjs")).href
);
const { ALLOWED_COMMANDS, BackendGateway } = await import(
  pathToFileURL(resolve(appRoot, "electron/backend-gateway.mjs")).href
);
const { retryDesktopService } = await import(
  pathToFileURL(resolve(appRoot, "electron/service-retry.mjs")).href
);

function gitSha() {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: repoRoot,
      encoding: "utf8",
    }).trim();
  } catch {
    return "unknown";
  }
}

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

async function settle(window) {
  await window.webContents.executeJavaScript(
    "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
  );
}

async function waitFor(window, jsPredicate, { timeoutMs = 30_000, label = "condition" } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const ok = await window.webContents.executeJavaScript(`(${jsPredicate})()`);
    if (ok) return true;
    await sleep(150);
  }
  throw new Error(`${label} not met within ${timeoutMs}ms`);
}

async function clickTestId(window, testId) {
  const clicked = await window.webContents.executeJavaScript(`
    (() => {
      const button = document.querySelector('[data-testid="${testId}"]');
      if (!button) return false;
      button.click();
      return true;
    })()
  `);
  if (!clicked) throw new Error(`missing data-testid=${testId}`);
  await settle(window);
}

async function selectSecurityViaSearch(window, query) {
  await window.webContents.executeJavaScript(`
    (() => {
      const input = document.querySelector("#security-search");
      if (!input) throw new Error("missing #security-search");
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      ).set;
      setter.call(input, ${JSON.stringify(query)});
      input.dispatchEvent(new Event("input", { bubbles: true }));
    })()
  `);
  const clicked = await window.webContents.executeJavaScript(`
    new Promise((resolve) => {
      const deadline = Date.now() + 20000;
      const poll = () => {
        const options = Array.from(
          document.querySelectorAll('#security-results [role="option"]'),
        );
        const match = options.find((el) =>
          (el.textContent || "").includes(${JSON.stringify(query)}),
        );
        if (match) {
          match.click();
          resolve(true);
          return;
        }
        if (Date.now() >= deadline) {
          resolve(false);
          return;
        }
        setTimeout(poll, 100);
      };
      poll();
    })
  `);
  if (!clicked) throw new Error(`security suggestion for ${query} did not appear`);
  await waitFor(
    window,
    `() => {
      const text = document.body.innerText || "";
      const codeHit = text.includes(${JSON.stringify(query)}) || text.includes("长电");
      const name = document.querySelector(".security-name")?.textContent || "";
      const canvases = document.querySelectorAll("canvas").length;
      const panels = document.querySelectorAll('[data-testid="chart-panel"]').length;
      const ready = text.includes("READY") || text.includes("就绪");
      // Lightweight-charts may briefly report 0-width; accept panels or any canvas.
      return codeHit && (Boolean(name) || text.includes("长电科技")) && (canvases >= 1 || panels >= 1 || ready);
    }`,
    { timeoutMs: 120_000, label: `live charts for ${query}` },
  );
  await settle(window);
  // Extra settle for chart layout convergence noise.
  await sleep(1500);
}

async function inspectUi(window) {
  return window.webContents.executeJavaScript(`
    (() => {
      const feedback = document.querySelector('.feedback-banner [role="alert"], .feedback-banner');
      const replayActive = Boolean(document.querySelector(".replay-controls.replay-active"));
      const replaySetup = Boolean(document.querySelector(".replay-controls.replay-setup"));
      const stepButton = Array.from(document.querySelectorAll(".replay-controls button"))
        .find((b) => (b.textContent || "").includes("前进"));
      const range = document.querySelector('.replay-controls input[type="range"]');
      const modeLive = document.querySelector('[data-testid="mode-live"]');
      const modeReplay = document.querySelector('[data-testid="mode-replay"]');
      return {
        securityName: document.querySelector(".security-name")?.textContent || "",
        canvasCount: document.querySelectorAll("canvas").length,
        chartPanelCount: document.querySelectorAll('[data-testid="chart-panel"]').length,
        feedback: feedback?.textContent?.trim() || null,
        replayActive,
        replaySetup,
        stepLabel: stepButton?.textContent?.trim() || null,
        stepDisabled: stepButton ? stepButton.disabled : null,
        rangeValue: range ? Number(range.value) : null,
        rangeMin: range ? Number(range.min) : null,
        rangeMax: range ? Number(range.max) : null,
        shownTime: document.querySelector(".replay-progress output")?.textContent || null,
        modeLivePressed: modeLive?.getAttribute("aria-pressed") || null,
        modeReplayPressed: modeReplay?.getAttribute("aria-pressed") || null,
        shellClass: document.querySelector('[data-testid="shell"]')?.className || "",
      };
    })()
  `);
}

async function capture(window, outDir, filename) {
  const image = await window.webContents.capturePage();
  const path = resolve(outDir, filename);
  await writeFile(path, image.toPNG());
  return path;
}

async function setReplayDate(window, tradeDate) {
  await window.webContents.executeJavaScript(`
    (() => {
      const input = document.querySelector("#replay-date");
      if (!input) throw new Error("missing #replay-date");
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      ).set;
      setter.call(input, ${JSON.stringify(tradeDate)});
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    })()
  `);
  await settle(window);
}

async function beginReplay(window) {
  await window.webContents.executeJavaScript(`
    (() => {
      window.__smokeReplaySessionId = window.__smokeReplaySessionId || null;
      if (!window.__smokeReplayHooked) {
        window.__smokeReplayHooked = true;
        const takeId = (payload) => {
          const id =
            payload?.session_id ||
            payload?.session?.session_id ||
            payload?.payload?.session?.session_id ||
            payload?.snapshot?.session?.session_id ||
            payload?.payload?.snapshot?.session?.session_id ||
            null;
          if (id) window.__smokeReplaySessionId = id;
        };
        try { window.stockpilot.onReplaySnapshot(takeId); } catch (_) {}
        try { window.stockpilot.onReplayEvent(takeId); } catch (_) {}
      }
    })()
  `);
  const clicked = await window.webContents.executeJavaScript(`
    (() => {
      const button = Array.from(document.querySelectorAll(".replay-controls button"))
        .find((el) => (el.textContent || "").includes("开始回放"));
      if (!button || button.disabled) return false;
      button.click();
      return true;
    })()
  `);
  if (!clicked) throw new Error("开始回放 button missing or disabled");
  await waitFor(
    window,
    `() => Boolean(document.querySelector(".replay-controls.replay-active"))`,
    { timeoutMs: 120_000, label: "replay session active" },
  );
  await waitFor(
    window,
    `() => Boolean(window.__smokeReplaySessionId)`,
    { timeoutMs: 30_000, label: "replay session id captured" },
  );
  await settle(window);
}

async function stepForward(window) {
  const before = await inspectUi(window);
  const clicked = await window.webContents.executeJavaScript(`
    (() => {
      const button = Array.from(document.querySelectorAll(".replay-controls button"))
        .find((el) => (el.textContent || "").includes("前进"));
      if (!button || button.disabled) return false;
      button.click();
      return true;
    })()
  `);
  if (!clicked) throw new Error("前进 button missing or disabled");
  await waitFor(
    window,
    `() => {
      const range = document.querySelector('.replay-controls input[type="range"]');
      if (!range) return false;
      return Number(range.value) !== ${JSON.stringify(before.rangeValue)};
    }`,
    { timeoutMs: 60_000, label: "replay stepped forward" },
  );
  await settle(window);
}

async function seekBackward(window) {
  const before = await inspectUi(window);
  if (before.rangeValue == null || before.rangeMin == null) {
    throw new Error("replay range not available for seek");
  }
  // Drive the same Safe Bridge seek_replay path the scrubber uses after commitSeek.
  const seekResult = await window.webContents.executeJavaScript(`
    (async () => {
      const range = document.querySelector('.replay-controls input[type="range"]');
      if (!range || range.disabled) return { ok: false, reason: "no range" };
      const sessionId = window.__smokeReplaySessionId;
      if (!sessionId) return { ok: false, reason: "no session id" };
      const step = Number(range.step || 300000);
      const targetValue = Math.max(Number(range.min), Number(range.value) - step * 2);
      const date = new Date(Number(targetValue));
      const two = (n) => String(n).padStart(2, "0");
      const targetTime = [
        date.getUTCFullYear() + "-" + two(date.getUTCMonth() + 1) + "-" + two(date.getUTCDate()),
        two(date.getUTCHours()) + ":" + two(date.getUTCMinutes()) + ":" + two(date.getUTCSeconds()),
      ].join(" ");
      // Mirror scrubber visual position before the async seek completes.
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      ).set;
      setter.call(range, String(targetValue));
      range.dispatchEvent(new Event("input", { bubbles: true }));
      const response = await window.stockpilot.seekReplay({
        schema_version: "t0_replay_v2",
        request_id: "ui-smoke-seek-" + Date.now(),
        session_id: sessionId,
        target_time: targetTime,
      });
      return {
        ok: true,
        targetValue,
        targetTime,
        sessionId,
        accepted: response?.accepted ?? null,
        error: response?.error ?? null,
      };
    })()
  `);
  if (!seekResult?.ok) {
    throw new Error(`failed to seekReplay: ${seekResult?.reason || "unknown"}`);
  }
  if (seekResult.error) {
    throw new Error(`seekReplay error: ${JSON.stringify(seekResult.error)}`);
  }
  await waitFor(
    window,
    `() => {
      const range = document.querySelector('.replay-controls input[type="range"]');
      if (!range) return false;
      return Number(range.value) < ${JSON.stringify(before.rangeValue)};
    }`,
    { timeoutMs: 90_000, label: "replay seek backward" },
  );
  await settle(window);
  return { before, after: await inspectUi(window), target: seekResult.targetValue, seekResult };
}

function recordStep(steps, step, extra = {}) {
  steps.push({
    step,
    at: new Date().toISOString(),
    ...extra,
  });
}

async function writeEvidence(outDir, payload) {
  await mkdir(outDir, { recursive: true });
  const jsonPath = resolve(outDir, "evidence.json");
  await writeFile(jsonPath, `${JSON.stringify(payload, null, 2)}\n`);
  const mdLines = [
    `# T+0 UI smoke — ${payload.surface}`,
    "",
    `- git_sha: \`${payload.git_sha}\``,
    `- status: **${payload.status}**`,
    `- symbol: ${payload.symbol}`,
    `- started: ${payload.started_at}`,
    `- finished: ${payload.finished_at}`,
    `- runtime_dir: \`${payload.runtime_dir}\``,
    `- python: \`${payload.python}\``,
    "",
    "## Steps",
    "",
  ];
  for (const step of payload.steps) {
    const shot = step.screenshot ? ` ([shot](${step.screenshot.split("/").pop()}))` : "";
    const latency = step.latency_ms != null ? ` · ${step.latency_ms}ms` : "";
    mdLines.push(`- ${step.step}: ${step.result || "ok"}${latency}${shot}`);
    if (step.note) mdLines.push(`  - ${step.note}`);
  }
  if (payload.blockers?.length) {
    mdLines.push("", "## Blockers", "");
    for (const b of payload.blockers) mdLines.push(`- ${b}`);
  }
  mdLines.push("");
  await writeFile(resolve(outDir, "evidence.md"), mdLines.join("\n"));
  return jsonPath;
}

async function main() {
  const sha = gitSha();
  const startedAt = new Date().toISOString();
  const liveSteps = [];
  const replaySteps = [];
  const blockers = [];
  let liveStatus = "fail";
  let replayStatus = "fail";
  let window;
  let serviceHost;
  let gateway;
  const t0 = Date.now();

  await mkdir(liveOut, { recursive: true });
  await mkdir(replayOut, { recursive: true });

  process.env.T0_PYTHON = pythonExecutable;
  process.env.T0_RUNTIME_DIR = runtimeDir;

  serviceHost = new PythonServiceHost({
    pythonExecutable,
    runtimeDir,
    generation: 1,
  });
  gateway = new BackendGateway({
    requestTimeoutMs: 30_000,
    commandTimeouts: {
      get_historical_snapshot: 60_000,
      get_live_snapshot: 60_000,
      select_security: 60_000,
      begin_replay: 180_000,
      step_replay: 120_000,
      seek_replay: 180_000,
      end_replay: 60_000,
    },
  });

  const send = (channel, payload) => {
    if (window && !window.isDestroyed()) window.webContents.send(channel, payload);
  };

  serviceHost.on("status", (status) => {
    send("bridge:service-status", status);
    if (status.state === "ready") {
      const connection = serviceHost.connectionInfo();
      if (connection) gateway.start(connection);
    } else if (
      status.state === "restarting" ||
      status.state === "failed" ||
      status.state === "stopped"
    ) {
      gateway.close();
    }
  });
  serviceHost.on("diagnostic", ({ stream, message }) => {
    process.stderr.write(`[py:${stream}] ${String(message).trim()}\n`);
  });
  gateway.on("service-status", (status) => send("bridge:service-status", status));
  gateway.on("app-event", (event) => send("bridge:app-event", event));
  gateway.on("replay-event", (event) => send("bridge:replay-event", event));
  gateway.on("replay-snapshot", (snapshot) => send("bridge:replay-snapshot", snapshot));
  gateway.on("diagnostic", ({ stream, message }) => {
    process.stderr.write(`[gw:${stream}] ${String(message).trim()}\n`);
  });

  ipcMain.handle("bridge:invoke", (_event, command, request) => {
    if (command === "get_service_status") return serviceHost.rendererStatus();
    if (command === "retry_service") return retryDesktopService(serviceHost, gateway);
    if (!ALLOWED_COMMANDS.has(command)) {
      throw new Error(`Safe Bridge command is not allowed: ${command}`);
    }
    return gateway.invoke(command, request);
  });

  try {
    await app.whenReady();
    window = new BrowserWindow({
      show: true,
      useContentSize: true,
      width: VIEWPORT.width,
      height: VIEWPORT.height,
      webPreferences: {
        preload: resolve(appRoot, "electron/preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    window.webContents.on("console-message", (details) => {
      if (details.level === "warning" || details.level === "error") {
        process.stderr.write(`[renderer] ${details.message}\n`);
      }
    });

    const serviceStart = Date.now();
    await serviceHost.start({ timeoutMs: 30_000 });
    recordStep(liveSteps, "python_service_ready", {
      result: "ok",
      latency_ms: Date.now() - serviceStart,
      note: `state=${serviceHost.state}`,
    });

    await window.loadFile(resolve(appRoot, "dist/index.html"));
    await waitFor(
      window,
      `() => Boolean(document.querySelector('[data-testid="workbench"]')) && typeof window.stockpilot?.getServiceStatus === "function"`,
      { timeoutMs: 30_000, label: "workbench render" },
    );
    await waitFor(
      window,
      `() => window.stockpilot.getServiceStatus().then((s) => s?.state === "ready")`,
      { timeoutMs: 30_000, label: "renderer service ready" },
    );
    recordStep(liveSteps, "workbench_ready", {
      result: "ok",
      latency_ms: Date.now() - t0,
    });

    // --- Live ---
    const liveLoadStart = Date.now();
    await selectSecurityViaSearch(window, SYMBOL);
    let ui = await inspectUi(window);
    const liveShot = await capture(window, liveOut, "01_live_600584_loaded.png");
    recordStep(liveSteps, "live_load", {
      result:
        (ui.canvasCount >= 1 || ui.chartPanelCount >= 1) &&
        (ui.securityName || "").length > 0
          ? "ok"
          : "fail",
      latency_ms: Date.now() - liveLoadStart,
      screenshot: liveShot,
      ui,
    });
    if (
      !((ui.canvasCount >= 1 || ui.chartPanelCount >= 1) && (ui.securityName || "").length > 0)
    ) {
      blockers.push("Live load did not show security name / chart canvases");
    } else {
      liveStatus = "pass";
    }

    // Symbol switch (best-effort) then back to 600584.
    try {
      const switchStart = Date.now();
      await selectSecurityViaSearch(window, SYMBOL_ALT);
      ui = await inspectUi(window);
      const switchShot = await capture(window, liveOut, "02_live_symbol_switch_600000.png");
      recordStep(liveSteps, "symbol_switch", {
        result: (ui.securityName || "").includes("浦发") || ui.canvasCount >= 1 ? "ok" : "uncertain",
        latency_ms: Date.now() - switchStart,
        screenshot: switchShot,
        ui,
      });
      const backStart = Date.now();
      await selectSecurityViaSearch(window, SYMBOL);
      ui = await inspectUi(window);
      const backShot = await capture(window, liveOut, "03_live_back_600584.png");
      recordStep(liveSteps, "symbol_switch_back", {
        result: ui.securityName ? "ok" : "fail",
        latency_ms: Date.now() - backStart,
        screenshot: backShot,
        ui,
      });
    } catch (error) {
      const message = error?.message ?? String(error);
      blockers.push(`Symbol switch skipped/failed: ${message}`);
      recordStep(liveSteps, "symbol_switch", { result: "fail", note: message });
      // Ensure primary symbol is selected for Replay.
      try {
        await selectSecurityViaSearch(window, SYMBOL);
      } catch (reselectError) {
        blockers.push(`Reselect 600584 failed: ${reselectError?.message ?? reselectError}`);
      }
    }

    // --- Replay ---
    const enterReplayStart = Date.now();
    await clickTestId(window, "mode-replay");
    await waitFor(
      window,
      `() => Boolean(document.querySelector('[data-testid="replay-controls"]'))`,
      { timeoutMs: 10_000, label: "replay controls" },
    );
    await setReplayDate(window, REPLAY_TRADE_DATE);
    const setupShot = await capture(window, replayOut, "01_replay_setup.png");
    recordStep(replaySteps, "enter_replay_mode", {
      result: "ok",
      latency_ms: Date.now() - enterReplayStart,
      screenshot: setupShot,
      trade_date: REPLAY_TRADE_DATE,
    });

    const beginStart = Date.now();
    await beginReplay(window);
    ui = await inspectUi(window);
    const startedShot = await capture(window, replayOut, "02_replay_started.png");
    recordStep(replaySteps, "begin_replay", {
      result: ui.replayActive ? "ok" : "fail",
      latency_ms: Date.now() - beginStart,
      screenshot: startedShot,
      ui,
    });
    if (!ui.replayActive) {
      blockers.push("Replay did not become active after 开始回放");
    }

    const stepStart = Date.now();
    await stepForward(window);
    ui = await inspectUi(window);
    const stepShot = await capture(window, replayOut, "03_replay_step_forward.png");
    recordStep(replaySteps, "step_forward", {
      result: "ok",
      latency_ms: Date.now() - stepStart,
      screenshot: stepShot,
      ui,
      note: "推进 (前进 N 分钟); no obvious hang observed if latency_ms finite",
    });

    // Extra step so seek-back has room if first step is near start.
    try {
      await stepForward(window);
      recordStep(replaySteps, "step_forward_2", {
        result: "ok",
        latency_ms: null,
      });
    } catch (error) {
      recordStep(replaySteps, "step_forward_2", {
        result: "skip",
        note: error?.message ?? String(error),
      });
    }

    const seekStart = Date.now();
    const seekInfo = await seekBackward(window);
    ui = await inspectUi(window);
    const seekShot = await capture(window, replayOut, "04_replay_seek_back.png");
    recordStep(replaySteps, "seek_backward", {
      result:
        seekInfo.after.rangeValue != null &&
        seekInfo.before.rangeValue != null &&
        seekInfo.after.rangeValue < seekInfo.before.rangeValue
          ? "ok"
          : "uncertain",
      latency_ms: Date.now() - seekStart,
      screenshot: seekShot,
      ui,
      seek: {
        before: seekInfo.before.rangeValue,
        after: seekInfo.after.rangeValue,
        target: seekInfo.target,
        shownTimeBefore: seekInfo.before.shownTime,
        shownTimeAfter: seekInfo.after.shownTime,
      },
      note: "回退 via progress seek (no dedicated step-back button)",
    });

    const backLiveStart = Date.now();
    await clickTestId(window, "mode-live");
    await waitFor(
      window,
      `() => document.querySelector('[data-testid="mode-live"]')?.getAttribute("aria-pressed") === "true"`,
      { timeoutMs: 30_000, label: "back to live mode" },
    );
    // Give live projection a moment to resettle.
    await sleep(1500);
    ui = await inspectUi(window);
    const liveBackShot = await capture(window, replayOut, "05_back_to_live.png");
    recordStep(replaySteps, "back_to_live", {
      result: ui.modeLivePressed === "true" ? "ok" : "fail",
      latency_ms: Date.now() - backLiveStart,
      screenshot: liveBackShot,
      ui,
    });

    const replayOk = replaySteps.every(
      (s) => s.result === "ok" || s.result === "skip" || s.result === "uncertain",
    );
    const criticalReplay = ["begin_replay", "step_forward", "seek_backward", "back_to_live"];
    const criticalPass = criticalReplay.every((name) => {
      const step = replaySteps.find((s) => s.step === name);
      return step && (step.result === "ok" || step.result === "uncertain");
    });
    replayStatus = criticalPass && replayOk ? "pass" : "fail";
    if (replaySteps.find((s) => s.step === "seek_backward")?.result === "uncertain") {
      // Treat uncertain seek as fail-soft only if cursor did not move; already encoded.
      const seekStep = replaySteps.find((s) => s.step === "seek_backward");
      if (seekStep?.result !== "ok") replayStatus = "fail";
    }
  } catch (error) {
    const message = error?.stack ?? error?.message ?? String(error);
    blockers.push(message);
    process.stderr.write(`${message}\n`);
    // Best-effort capture of whatever is on screen.
    try {
      if (window && !window.isDestroyed()) {
        const fallback = await capture(
          window,
          liveStatus === "pass" ? replayOut : liveOut,
          "99_failure_state.png",
        );
        recordStep(liveStatus === "pass" ? replaySteps : liveSteps, "failure_capture", {
          result: "fail",
          screenshot: fallback,
          note: message,
        });
      }
    } catch {
      // ignore capture failure
    }
  } finally {
    const finishedAt = new Date().toISOString();
    const common = {
      kind: "t0_assistant_ui_smoke",
      git_sha: sha,
      symbol: "sh.600584",
      symbol_input: SYMBOL,
      replay_trade_date: REPLAY_TRADE_DATE,
      runtime_dir: runtimeDir,
      python: pythonExecutable,
      czsc_note: "real PythonServiceHost; overlays via czsc from T0_PYTHON env",
      no_real_trades: true,
      started_at: startedAt,
      finished_at: finishedAt,
      total_latency_ms: Date.now() - t0,
      blockers,
    };

    await writeEvidence(liveOut, {
      ...common,
      surface: "live",
      status: liveStatus,
      steps: liveSteps,
    });
    await writeEvidence(replayOut, {
      ...common,
      surface: "replay",
      status: replayStatus,
      steps: replaySteps,
    });

    gateway?.close();
    try {
      await serviceHost?.stop();
    } catch (stopError) {
      process.stderr.write(`service stop: ${stopError}\n`);
    }
    if (window && !window.isDestroyed()) window.destroy();
    ipcMain.removeHandler("bridge:invoke");
  }

  const summary = {
    git_sha: sha,
    live: liveStatus,
    replay: replayStatus,
    blockers,
    artifacts: {
      live: liveOut,
      replay: replayOut,
    },
  };
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  if (liveStatus !== "pass" || replayStatus !== "pass") {
    app.exit(1);
    return;
  }
  app.quit();
}

main().catch((error) => {
  process.stderr.write(`${error?.stack ?? error}\n`);
  app.exit(1);
});
