import { app, BrowserWindow, ipcMain, Menu } from "electron";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { PythonServiceHost } from "./python-service-host.mjs";
import { ALLOWED_COMMANDS, BackendGateway } from "./backend-gateway.mjs";
import { RotatingTechnicalLog } from "./technical-log.mjs";
import { showLogReviewWindow } from "./log-review-window.mjs";
import { retryDesktopService } from "./service-retry.mjs";

const moduleDir = dirname(fileURLToPath(import.meta.url));
const serviceHost = new PythonServiceHost({
  runtimeDir: resolve(app.getPath("userData"), "stockpilot"),
});
const gateway = new BackendGateway({
  commandTimeouts: { get_historical_snapshot: 10_000 },
});
let mainWindow = null;
let quitting = false;
let technicalLog = null;

function send(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    webPreferences: {
      preload: resolve(moduleDir, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  void mainWindow.loadFile(resolve(moduleDir, "../dist/index.html"));
  mainWindow.on("closed", () => { mainWindow = null; });

  // 窗口生命周期信号转发（Issue #146 第 4 步）：渲染进程需要区分"仅失焦"和
  // "最小化/隐藏"两类后台场景，以正确保存/恢复视口状态。main 进程转发 blur/focus
  // 和 minimize/restore 事件，渲染进程据此执行 pre-background 快照保存和恢复
  // 后的主动右对齐。
  const sendLifecycle = (phase) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      send("bridge:window-lifecycle", { phase });
    }
  };
  mainWindow.on("blur", () => sendLifecycle("background"));
  mainWindow.on("focus", () => sendLifecycle("foreground"));
  mainWindow.on("minimize", () => sendLifecycle("background"));
  mainWindow.on("restore", () => sendLifecycle("foreground"));
}

serviceHost.on("status", (status) => {
  send("bridge:service-status", status);
  if (status.state === "ready") {
    const connection = serviceHost.connectionInfo();
    if (connection) gateway.start(connection);
  } else if (status.state === "restarting" || status.state === "failed" || status.state === "stopped") {
    gateway.close();
  }
});
function recordDiagnostic(stream, message) {
  console[stream === "stderr" ? "error" : "log"](message.trim());
  void technicalLog?.write(stream, message).catch((error) => {
    console.error("Unable to write technical log", error);
  });
}

serviceHost.on("diagnostic", ({ stream, message }) => recordDiagnostic(stream, message));
gateway.on("service-status", (status) => send("bridge:service-status", status));
gateway.on("app-event", (event) => send("bridge:app-event", event));
gateway.on("replay-event", (event) => send("bridge:replay-event", event));
gateway.on("replay-snapshot", (snapshot) => send("bridge:replay-snapshot", snapshot));
gateway.on("diagnostic", ({ stream, message }) => recordDiagnostic(stream, message));

ipcMain.handle("bridge:invoke", (_event, command, request) => {
  if (command === "get_service_status") return serviceHost.rendererStatus();
  if (command === "retry_service") {
    return retryDesktopService(serviceHost, gateway);
  }
  if (!ALLOWED_COMMANDS.has(command)) throw new Error(`Safe Bridge command is not allowed: ${command}`);
  return gateway.invoke(command, request);
});

app.whenReady().then(async () => {
  technicalLog = new RotatingTechnicalLog(
    resolve(app.getPath("userData"), "logs", "technical.log"),
  );
  ipcMain.handle("log-review:read", () => technicalLog.read());
  Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenu()));
  createWindow();
  try {
    await serviceHost.start();
  } catch (error) {
    recordDiagnostic("stderr", `本地服务启动失败: ${error}`);
  }
});

function applicationMenu() {
  const template = [];
  if (process.platform === "darwin") {
    template.push({ label: app.name, submenu: [{ role: "about" }, { type: "separator" }, { role: "quit" }] });
  }
  template.push(
    { label: "File", submenu: [{ role: "close" }] },
    { label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [{ role: "reload" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }] },
    { label: "Window", submenu: [{ role: "minimize" }, { role: "zoom" }] },
    {
      label: "Help",
      submenu: [
        {
          label: "Log Review",
          click: () => showLogReviewWindow(moduleDir, mainWindow),
        },
      ],
    },
  );
  return template;
}

app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("before-quit", (event) => {
  if (quitting || serviceHost.state === "stopped") return;
  event.preventDefault();
  quitting = true;
  gateway.close();
  void serviceHost.stop()
    .then(() => app.quit())
    .catch((error) => {
      quitting = true;
      recordDiagnostic(
        "stderr",
        `Unable to stop the Python service cleanly; closing the App. ${error}`,
      );
      app.quit();
    });
});
