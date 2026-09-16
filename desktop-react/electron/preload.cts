import { contextBridge, ipcRenderer, webFrame, webUtils } from "electron";

export interface LoomNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

const FRAME_BATCH_MS = 16;
const NETWORK_RECOVERY_DELAY_MS = 1500;
const BATCHED_ITEM_METHODS = new Set(["item/started", "item/delta", "item/completed"]);
const ACTIVE_RECOVERABLE_THREAD_STATUSES = new Set(["running", "waiting_approval"]);
const automaticNetworkRecoveryAttempts = new Set<string>();

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

function isTransportFailure(error: unknown): boolean {
  return String(error ?? "").startsWith("AITransportError:");
}

function retryableTransportFailure(result: Record<string, unknown>, expectedTurnId = ""): boolean {
  const thread = result.thread as Record<string, unknown> | undefined;
  const status = String(thread?.status ?? "").trim();
  const turnId = String(expectedTurnId || thread?.currentTurnId || "").trim();
  if (status !== "failed" || !turnId || !isTransportFailure(result.error)) return false;

  const events = Array.isArray(result.events) ? result.events : [];
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index] as Record<string, unknown> | undefined;
    if (!event || String(event.turnId ?? "").trim() !== turnId) continue;
    if (String(event.kind ?? "").trim() !== "turn_failed") continue;
    const data = event.data as Record<string, unknown> | undefined;
    return data?.retryable_transport === true;
  }
  return false;
}

function threadNeedsRecovery(result: Record<string, unknown>): boolean {
  const thread = result.thread as Record<string, unknown> | undefined;
  const status = String(thread?.status ?? "").trim();
  return ACTIVE_RECOVERABLE_THREAD_STATUSES.has(status) || retryableTransportFailure(result);
}

async function call(method: string, params: Record<string, unknown> = {}) {
  const result = await ipcRenderer.invoke("loom:call", method, params);
  if (method !== "thread/read" || !result || typeof result !== "object") return result;

  const record = result as Record<string, unknown>;
  const thread = record.thread as Record<string, unknown> | undefined;
  const threadId = String(thread?.id ?? "").trim();
  const turnId = String(thread?.currentTurnId ?? "").trim();
  if (!threadId || !turnId || !threadNeedsRecovery(record)) return result;

  // A read is observational on the backend. When the desktop actually opens an
  // unfinished/retryable thread, explicitly request a safe handoff. The durable
  // runtime decides whether recovery is legal; the client never guesses from an
  // exception name alone.
  return ipcRenderer.invoke("loom:call", "thread/resume", {
    threadId,
    recoverTurnId: turnId,
  });
}

const api = {
  connect: () => ipcRenderer.invoke("loom:connect"),
  call,
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
    const recoveryTimers = new Set<ReturnType<typeof setTimeout>>();

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

    const scheduleNetworkRecovery = (payload: LoomNotification) => {
      if (payload.method !== "turn/completed") return;
      const params = payload.params ?? {};
      const turn = params.turn as Record<string, unknown> | undefined;
      const status = String(turn?.status ?? "").trim();
      const error = turn?.error;
      const threadId = String(params.threadId ?? turn?.threadId ?? "").trim();
      const turnId = String(turn?.id ?? "").trim();
      if (status !== "failed" || !isTransportFailure(error) || !threadId || !turnId) return;

      const key = `${threadId}:${turnId}`;
      if (automaticNetworkRecoveryAttempts.has(key)) return;
      automaticNetworkRecoveryAttempts.add(key);

      const recoveryTimer = setTimeout(() => {
        recoveryTimers.delete(recoveryTimer);
        void (async () => {
          try {
            // Confirm the durable failure metadata before retrying. This filters
            // non-retryable provider/credential failures that share the transport
            // exception class and avoids inventing a client-side running state.
            const snapshot = await ipcRenderer.invoke("loom:call", "thread/read", { threadId });
            if (!snapshot || typeof snapshot !== "object"
              || !retryableTransportFailure(snapshot as Record<string, unknown>, turnId)) {
              listener({ jsonrpc: "2.0", method: "thread/resync", params: { threadId } });
              return;
            }
            await ipcRenderer.invoke("loom:call", "thread/resume", {
              threadId,
              recoverTurnId: turnId,
            });
          } catch {
            listener({ jsonrpc: "2.0", method: "thread/resync", params: { threadId } });
          }
        })();
      }, NETWORK_RECOVERY_DELAY_MS);
      recoveryTimers.add(recoveryTimer);
    };

    const wrapped = (_event: Electron.IpcRendererEvent, payload: LoomNotification) => {
      if (!BATCHED_ITEM_METHODS.has(payload.method)) {
        flush();
        listener(payload);
        scheduleNetworkRecovery(payload);
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
      for (const recoveryTimer of recoveryTimers) clearTimeout(recoveryTimer);
      recoveryTimers.clear();
      timer = null;
      queue = [];
    };
  },
};

contextBridge.exposeInMainWorld("loom", api);
