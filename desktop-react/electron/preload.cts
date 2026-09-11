import { contextBridge, ipcRenderer, webUtils } from "electron";

export interface LoomNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

const api = {
  connect: () => ipcRenderer.invoke("loom:connect"),
  call: (method: string, params: Record<string, unknown> = {}) => ipcRenderer.invoke("loom:call", method, params),
  disconnect: () => ipcRenderer.invoke("loom:disconnect"),
  listModels: () => ipcRenderer.invoke("loom:model-list"),
  switchModelProfile: (selection: string) => ipcRenderer.invoke("loom:model-switch", selection),
  switchCurrentModel: (model: string) => ipcRenderer.invoke("loom:model-switch-current", model),
  addModel: (input: Record<string, unknown>) => ipcRenderer.invoke("loom:model-add", input),
  updateModel: (input: Record<string, unknown>) => ipcRenderer.invoke("loom:model-update", input),
  testModel: (selection: string) => ipcRenderer.invoke("loom:model-test", selection),
  deleteModel: (selection: string) => ipcRenderer.invoke("loom:model-delete", selection),
  setReasoning: (kind: string, value: string) => ipcRenderer.invoke("loom:reasoning-set", kind, value),
  exportComputerLogs: () => ipcRenderer.invoke("loom:export-computer-logs"),
  exportBrowserLogs: () => ipcRenderer.invoke("loom:export-browser-logs"),
  revealPath: (targetPath: string) => ipcRenderer.invoke("loom:reveal-path", targetPath),
  pickDirectory: () => ipcRenderer.invoke("loom:pick-directory"),
  pickFiles: () => ipcRenderer.invoke("loom:pick-files"),
  // Electron 32 removed File.path, so the real path has to come from webUtils
  // in the preload. Attachments travel as paths, never as bytes over the RPC.
  filePathFor: (file: File) => {
    try {
      return webUtils.getPathForFile(file);
    } catch {
      return "";
    }
  },
  // A pasted image has no file behind it, so it gets written to a temp file
  // first rather than becoming the one input with its own transport.
  stageTempFile: (name: string, bytes: Uint8Array) =>
    ipcRenderer.invoke("loom:stage-temp-file", name, bytes),
  onNotification: (listener: (payload: LoomNotification) => void) => {
    const wrapped = (_event: Electron.IpcRendererEvent, payload: LoomNotification) => listener(payload);
    ipcRenderer.on("loom:notification", wrapped);
    return () => ipcRenderer.removeListener("loom:notification", wrapped);
  },
};

contextBridge.exposeInMainWorld("loom", api);
