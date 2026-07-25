import { BrowserWindow } from "electron";
import { resolve } from "node:path";

let logWindow = null;

export function showLogReviewWindow(moduleDir, parent = null) {
  if (logWindow && !logWindow.isDestroyed()) {
    logWindow.focus();
    return logWindow;
  }
  logWindow = new BrowserWindow({
    width: 860,
    height: 620,
    minWidth: 620,
    minHeight: 420,
    title: "Log Review",
    parent: parent && !parent.isDestroyed() ? parent : undefined,
    webPreferences: {
      preload: resolve(moduleDir, "log-review-preload.mjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  void logWindow.loadFile(resolve(moduleDir, "log-review.html"));
  logWindow.on("closed", () => {
    logWindow = null;
  });
  return logWindow;
}
