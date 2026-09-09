import { contextBridge, ipcRenderer } from "electron";

export interface LoomNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

const api = {
  connect: () => ipcRenderer.invoke("loom:connect"),
  call: (method: string, params: Record<string, unknown> = {}) => ipcRenderer.invoke("loom:call", method, params),
  disconnect: () => ipcRenderer.invoke("loom:disconnect"),
  onNotification: (listener: (payload: LoomNotification) => void) => {
    const wrapped = (_event: Electron.IpcRendererEvent, payload: LoomNotification) => listener(payload);
    ipcRenderer.on("loom:notification", wrapped);
    return () => ipcRenderer.removeListener("loom:notification", wrapped);
  },
};

contextBridge.exposeInMainWorld("loom", api);
