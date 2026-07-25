import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld(
  "stockpilotLogReview",
  Object.freeze({
    read: () => ipcRenderer.invoke("log-review:read"),
  }),
);
