import { app, BrowserWindow, ipcMain } from "electron";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { PythonServiceHost } from "./python-service-host.mjs";

const moduleDir = dirname(fileURLToPath(import.meta.url));
const serviceHost = new PythonServiceHost();
let mainWindow = null;
let quitting = false;

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

serviceHost.on("status", (status) => mainWindow?.webContents.send("service:status", status));
serviceHost.on("diagnostic", ({ stream, message }) => console[stream === "stderr" ? "error" : "log"](message.trim()));
ipcMain.handle("service:get-status", () => serviceHost.rendererStatus());

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
  void serviceHost.stop()
    .then(() => app.quit())
    .catch((error) => {
      quitting = false;
      console.error("Unable to stop the Python service; Electron will remain open.", error);
    });
});
