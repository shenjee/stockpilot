import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("stockpilot", {
  getServiceStatus: () => ipcRenderer.invoke("service:get-status"),
  onServiceStatus: (listener) => {
    const handler = (_event, status) => listener(status);
    ipcRenderer.on("service:status", handler);
    return () => ipcRenderer.removeListener("service:status", handler);
  },
});
