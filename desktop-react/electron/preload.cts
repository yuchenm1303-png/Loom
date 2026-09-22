import { contextBridge, ipcRenderer, webFrame, webUtils } from "electron";

export interface LoomNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

const FRAME_BATCH_MS = 16;
const BATCHED_ITEM_METHODS = new Set(["item/started", "item/delta", "item/completed"]);

function mergeDeltaParams(
  previous: Record<string, unknown> | undefined,
  incoming: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const next = { ...(previous ?? {}) };
  for (const [key, value] of Object.entries(incoming ?? {})) {
    if (["text", "stdout", "stderr"].includes(key) && typeof value === "string") {
      next[key] = `${String(next[key] ?? "")}${value}`;
    } else {
      next[key] = value;
    }
  }
  return next;
}

const api = {
  connect: () => ipcRenderer.invoke("loom:connect"),
  call: (method: string, params: Record<string, unknown> = {}) => ipcRenderer.invoke("loom:call", method, params),
  disconnect: () => ipcRenderer.invoke("loom:disconnect"),
  setNativeTheme: (source: "system" | "light" | "dark") => ipcRenderer.invoke("loom:set-native-theme", source),
  accountStatus: () => ipcRenderer.invoke("loom:account-status"),
  accountLogin: (email: string, password: string) => ipcRenderer.invoke("loom:account-login", email, password),
  accountRegister: (email: string, password: string) => ipcRenderer.invoke("loom:account-register", email, password),
  accountLogout: () => ipcRenderer.invoke("loom:account-logout"),
  listModels: () => ipcRenderer.invoke("loom:model-list"),
  setModelProviderKey: (provider: string, apiKey: string) => ipcRenderer.invoke("loom:model-provider-key", provider, apiKey),
  switchModelProfile: (first: string, second?: string) =>
    second === undefined ? ipcRenderer.invoke("loom:model-switch", first) : ipcRenderer.invoke("loom:model-switch", first, second),
  switchCurrentModel: (first: string, second?: string, third?: string) =>
    third === undefined ? ipcRenderer.invoke("loom:model-switch-current", first) : ipcRenderer.invoke("loom:model-switch-current", first, second, third),
  addModel: (first: string | Record<string, unknown>, second?: Record<string, unknown>) =>
    typeof first === "string" ? ipcRenderer.invoke("loom:model-add", first, second) : ipcRenderer.invoke("loom:model-add", first),
  updateModel: (input: Record<string, unknown>) => ipcRenderer.invoke("loom:model-update", input),
  testModel: (selection: string) => ipcRenderer.invoke("loom:model-test", selection),
  deleteModel: (selection: string) => ipcRenderer.invoke("loom:model-delete", selection),
  setReasoning: (...args: string[]) => ipcRenderer.invoke("loom:reasoning-set", ...args),
  exportComputerLogs: () => ipcRenderer.invoke("loom:export-computer-logs"),
  exportBrowserLogs: () => ipcRenderer.invoke("loom:export-browser-logs"),
  setupBrowserExtension: (browser: "edge" | "chrome" = "edge", extensionConnected = false) => ipcRenderer.invoke("loom:setup-browser-extension", browser, extensionConnected),
  revealPath: (targetPath: string) => ipcRenderer.invoke("loom:reveal-path", targetPath),
  readLocalImage: (targetPath: string, workspaceRoot: string) =>
    ipcRenderer.invoke("loom:read-local-image", targetPath, workspaceRoot),
  readLocalMedia: (targetPath: string, workspaceRoot: string) =>
    ipcRenderer.invoke("loom:read-local-media", targetPath, workspaceRoot),
  pickDirectory: () => ipcRenderer.invoke("loom:pick-directory"),
  pickFiles: () => ipcRenderer.invoke("loom:pick-files"),
  setZoomFactor: (factor: number) => {
    const numeric = Number(factor);
    const safe = Number.isFinite(numeric) ? Math.min(1.3, Math.max(0.9, numeric)) : 1;
    webFrame.setZoomFactor(safe);
    return safe;
  },
  filePathFor: (file: File) => {
    try {
      return webUtils.getPathForFile(file);
    } catch {
      return "";
    }
  },
  stageTempFile: (name: string, bytes: Uint8Array) =>
    ipcRenderer.invoke("loom:stage-temp-file", name, bytes),
  onNotification: (listener: (payload: LoomNotification) => void) => {
    let queue: LoomNotification[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;

    const flush = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      if (!queue.length) return;
      const pending = queue;
      queue = [];
      for (const payload of pending) listener(payload);
    };

    const schedule = () => {
      if (timer !== null) return;
      timer = setTimeout(flush, FRAME_BATCH_MS);
    };

    const wrapped = (_event: Electron.IpcRendererEvent, payload: LoomNotification) => {
      if (!BATCHED_ITEM_METHODS.has(payload.method)) {
        flush();
        listener(payload);
        return;
      }

      if (payload.method === "item/delta") {
        const itemId = String(payload.params?.itemId ?? "");
        const last = queue[queue.length - 1];
        if (
          last?.method === "item/delta"
          && String(last.params?.itemId ?? "") === itemId
        ) {
          last.params = {
            ...(last.params ?? {}),
            ...(payload.params ?? {}),
            delta: mergeDeltaParams(
              last.params?.delta as Record<string, unknown> | undefined,
              payload.params?.delta as Record<string, unknown> | undefined,
            ),
          };
          schedule();
          return;
        }
      }

      queue.push(payload);
      schedule();
    };

    ipcRenderer.on("loom:notification", wrapped);
    return () => {
      ipcRenderer.removeListener("loom:notification", wrapped);
      if (timer !== null) clearTimeout(timer);
      timer = null;
      queue = [];
    };
  },
};

contextBridge.exposeInMainWorld("loom", api);
