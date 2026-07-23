import { app, BrowserWindow, ipcMain } from "electron";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { PythonServiceHost } from "./python-service-host.mjs";
import { ALLOWED_COMMANDS, BackendGateway } from "./backend-gateway.mjs";

const moduleDir = dirname(fileURLToPath(import.meta.url));
const serviceHost = new PythonServiceHost();
const gateway = new BackendGateway();
let mainWindow = null;
let quitting = false;

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
      preload: resolve(moduleDir, "preload.mjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  void mainWindow.loadFile(resolve(moduleDir, "../dist/index.html"));
  mainWindow.on("closed", () => { mainWindow = null; });
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
serviceHost.on("diagnostic", ({ stream, message }) => console[stream === "stderr" ? "error" : "log"](message.trim()));
gateway.on("service-status", (status) => send("bridge:service-status", status));
gateway.on("app-event", (event) => send("bridge:app-event", event));
gateway.on("replay-event", (event) => send("bridge:replay-event", event));
gateway.on("replay-snapshot", (snapshot) => send("bridge:replay-snapshot", snapshot));
gateway.on("diagnostic", ({ stream, message }) => console[stream === "stderr" ? "error" : "log"](message));

ipcMain.handle("bridge:invoke", (_event, command, request) => {
  if (command === "get_service_status") return serviceHost.rendererStatus();
  if (!ALLOWED_COMMANDS.has(command)) throw new Error(`Safe Bridge command is not allowed: ${command}`);
  return gateway.invoke(command, request);
});

app.whenReady().then(async () => {
  createWindow();
  try {
    await serviceHost.start();
  } catch (error) {
    console.error(error);
  }
});

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
      quitting = false;
      console.error("Unable to stop the Python service; Electron will remain open.", error);
    });
});
