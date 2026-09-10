import { contextBridge, ipcRenderer } from "electron";

type HudPayload = Record<string, unknown>;

const api = {
  onUpdate(listener: (payload: HudPayload) => void) {
    const wrapped = (_event: Electron.IpcRendererEvent, payload: HudPayload) => listener(payload || {});
    ipcRenderer.on("loom:hud-update", wrapped);
    return () => ipcRenderer.removeListener("loom:hud-update", wrapped);
  },
};

contextBridge.exposeInMainWorld("loomHud", api);
