import { contextBridge, ipcRenderer } from "electron";
import { buildSafeBridge } from "./safe-bridge.mjs";

const CHANNELS = Object.freeze({
  service_status: "bridge:service-status",
  app_event: "bridge:app-event",
  replay_event: "bridge:replay-event",
  replay_snapshot: "bridge:replay-snapshot",
});

const bridge = buildSafeBridge({
  invoke: (command, request) => ipcRenderer.invoke("bridge:invoke", command, request),
  subscribe: (channel, listener) => {
    const ipcChannel = CHANNELS[channel];
    if (!ipcChannel) throw new Error(`Safe Bridge subscription is not allowed: ${channel}`);
    const handler = (_event, payload) => listener(payload);
    ipcRenderer.on(ipcChannel, handler);
    return () => ipcRenderer.removeListener(ipcChannel, handler);
  },
});

contextBridge.exposeInMainWorld("stockpilot", bridge);
