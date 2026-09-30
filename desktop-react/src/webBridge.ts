import type { LoomAccountResult } from "./types/account";
import type { LoomNotification } from "./types/global";

const WEB_PLATFORM_MARKER = "web";
const DEFAULT_WS_PATH = "/api/ws/browser";
const DEVICE_WAIT_MS = 8_000;
const HEARTBEAT_MS = 30_000;
const MAX_STAGED_BYTES = 48 * 1024 * 1024;

type InvokeMessage = {
  type: "invoke";
  id: number;
  operation: string;
  args: unknown[];
};

type RelayMessage =
  | { type: "device_status"; online: boolean; device?: Record<string, unknown> | null }
  | { type: "invoke_result"; id: number; result?: unknown; error?: { message?: string; code?: string } }
  | { type: "notification"; payload?: LoomNotification }
  | { type: "pong" };

type PendingCall = {
  resolve(value: unknown): void;
  reject(error: Error): void;
};

const listeners = new Set<(payload: LoomNotification) => void>();
const pending = new Map<number, PendingCall>();
let socket: WebSocket | null = null;
let socketPromise: Promise<WebSocket> | null = null;
let connectPromise: Promise<unknown> | null = null;
let nextId = 1;
let heartbeatTimer: number | null = null;
let deviceOnline = false;
let deviceStatusWaiter: ((online: boolean) => void) | null = null;
const blobUrls = new Set<string>();

function webSocketUrl(): string {
  const configured = String(import.meta.env.VITE_LOOM_WEB_SOCKET_URL || "").trim();
  if (configured) return configured;
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}${DEFAULT_WS_PATH}`;
}

function dispatchAuthChanged(): void {
  window.dispatchEvent(new CustomEvent("loom:web-auth-changed"));
}

async function accountRequest(path: string, init: RequestInit = {}): Promise<LoomAccountResult> {
  let response: Response;
  try {
    response = await fetch(`/api/auth/${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(init.headers || {}),
      },
    });
  } catch (cause) {
    return {
      ok: false,
      error: {
        code: "ACCOUNT_SERVICE_UNREACHABLE",
        message: cause instanceof Error ? cause.message : "Could not reach the Loom web service.",
      },
    };
  }
  const payload = await response.json().catch(() => ({})) as LoomAccountResult;
  if (payload && typeof payload === "object" && "ok" in payload) return payload;
  return {
    ok: false,
    error: {
      code: "ACCOUNT_RESPONSE_INVALID",
      message: `Loom web service returned an invalid response (${response.status}).`,
      status: response.status,
    },
  };
}

function stopHeartbeat(): void {
  if (heartbeatTimer !== null) window.clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

function failPending(message: string): void {
  for (const call of pending.values()) call.reject(new Error(message));
  pending.clear();
}

function closeSocket(): void {
  const current = socket;
  socket = null;
  socketPromise = null;
  deviceOnline = false;
  deviceStatusWaiter = null;
  stopHeartbeat();
  failPending("Loom Desktop disconnected from the web session.");
  if (current && current.readyState < WebSocket.CLOSING) current.close(1000, "client closed");
}

function handleRelayMessage(raw: string): void {
  let message: RelayMessage;
  try {
    message = JSON.parse(raw) as RelayMessage;
  } catch {
    return;
  }

  if (message.type === "device_status") {
    deviceOnline = Boolean(message.online);
    if (deviceStatusWaiter) {
      const waiter = deviceStatusWaiter;
      deviceStatusWaiter = null;
      waiter(deviceOnline);
    }
    return;
  }
  if (message.type === "invoke_result") {
    const call = pending.get(message.id);
    if (!call) return;
    pending.delete(message.id);
    if (message.error) call.reject(new Error(message.error.message || "Remote Loom operation failed."));
    else call.resolve(message.result);
    return;
  }
  if (message.type === "notification" && message.payload) {
    for (const listener of listeners) {
      try { listener(message.payload); } catch { /* one view listener must not block another */ }
    }
  }
}

async function ensureSocket(): Promise<WebSocket> {
  if (socket?.readyState === WebSocket.OPEN) return socket;
  if (socketPromise) return socketPromise;

  socketPromise = new Promise<WebSocket>((resolve, reject) => {
    const ws = new WebSocket(webSocketUrl());
    socket = ws;
    const timeout = window.setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        ws.close();
        reject(new Error("Could not connect to Loom Web."));
      }
    }, DEVICE_WAIT_MS);

    ws.addEventListener("open", () => {
      window.clearTimeout(timeout);
      heartbeatTimer = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
      }, HEARTBEAT_MS);
      resolve(ws);
    }, { once: true });
    ws.addEventListener("message", (event) => handleRelayMessage(String(event.data || "")));
    ws.addEventListener("close", () => {
      if (socket === ws) {
        socket = null;
        socketPromise = null;
        deviceOnline = false;
        stopHeartbeat();
        failPending("Loom Desktop went offline.");
      }
    });
    ws.addEventListener("error", () => {
      if (ws.readyState !== WebSocket.OPEN) reject(new Error("Could not connect to Loom Web."));
    });
  }).finally(() => {
    socketPromise = null;
  });

  return socketPromise;
}

async function waitForDevice(): Promise<void> {
  if (deviceOnline) return;
  const ws = await ensureSocket();
  if (deviceOnline) return;
  const online = await new Promise<boolean>((resolve) => {
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    deviceStatusWaiter = finish;
    window.setTimeout(() => finish(deviceOnline), DEVICE_WAIT_MS);
    if (ws.readyState !== WebSocket.OPEN) finish(false);
  });
  if (!online) {
    throw new Error("Loom Desktop is offline. Open the latest Loom Desktop, sign in to the same account, then retry.");
  }
}

async function invoke<T = unknown>(operation: string, args: unknown[] = []): Promise<T> {
  await waitForDevice();
  const ws = await ensureSocket();
  const id = nextId++;
  const message: InvokeMessage = { type: "invoke", id, operation, args };
  const result = new Promise<unknown>((resolve, reject) => pending.set(id, { resolve, reject }));
  ws.send(JSON.stringify(message));
  return result as Promise<T>;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + chunk, bytes.length)));
  }
  return btoa(binary);
}

function base64ToBlobUrl(payload: { base64?: string; mimeType?: string }): string {
  const binary = atob(String(payload.base64 || ""));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const url = URL.createObjectURL(new Blob([bytes], { type: String(payload.mimeType || "application/octet-stream") }));
  blobUrls.add(url);
  return url;
}

async function pickAndStageFiles(): Promise<string[]> {
  const files = await new Promise<File[]>((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.style.display = "none";
    document.body.appendChild(input);
    const finish = () => {
      const selected = Array.from(input.files || []);
      input.remove();
      resolve(selected);
    };
    input.addEventListener("change", finish, { once: true });
    input.addEventListener("cancel", finish, { once: true });
    input.click();
  });
  const paths: string[] = [];
  for (const file of files) {
    if (file.size > MAX_STAGED_BYTES) throw new Error(`${file.name} is too large for Loom Web (48 MB max).`);
    const bytes = new Uint8Array(await file.arrayBuffer());
    const path = await invoke<string>("stageTempFile", [file.name || "attachment", bytesToBase64(bytes)]);
    if (path) paths.push(path);
  }
  return paths;
}

function safeExternalUrl(value: string): URL {
  const url = new URL(String(value || "").trim(), window.location.href);
  if (!["http:", "https:", "mailto:"].includes(url.protocol)) throw new Error("Unsupported external URL.");
  return url;
}

export function isLoomWebRuntime(): boolean {
  return document.documentElement.dataset.loomPlatform === WEB_PLATFORM_MARKER;
}

export function installWebBridge(): void {
  if (Reflect.get(window, "loom")) return;
  document.documentElement.dataset.loomPlatform = WEB_PLATFORM_MARKER;

  const bridge: Window["loom"] = {
    connect: async () => {
      if (connectPromise) return connectPromise;
      connectPromise = (async () => {
        const account = await accountRequest("status");
        if (!account.ok || !account.snapshot.authenticated) {
          throw new Error("Sign in to Loom Web before connecting to your desktop.");
        }
        await waitForDevice();
        return invoke("connect", []);
      })();
      try { return await connectPromise; } finally { connectPromise = null; }
    },
    call: (method, params = {}) => invoke("call", [method, params]),
    disconnect: async () => closeSocket(),
    setNativeTheme: async (source) => {
      if (source === "dark" || source === "light") return source;
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    },
    accountStatus: () => accountRequest("status"),
    accountLogin: async (email, password) => {
      const result = await accountRequest("login", { method: "POST", body: JSON.stringify({ email, password }) });
      if (result.ok) dispatchAuthChanged();
      return result;
    },
    accountRegister: async (email, password) => {
      const result = await accountRequest("register", { method: "POST", body: JSON.stringify({ email, password }) });
      if (result.ok) dispatchAuthChanged();
      return result;
    },
    accountLogout: async () => {
      closeSocket();
      const result = await accountRequest("logout", { method: "POST", body: "{}" });
      dispatchAuthChanged();
      return result;
    },
    listModels: (forceRefresh = false) => invoke("listModels", [Boolean(forceRefresh)]),
    setModelProviderKey: (provider, apiKey) => invoke("setModelProviderKey", [provider, apiKey]),
    switchModelProfile: (first: string, second?: string) => invoke("switchModelProfile", second === undefined ? [first] : [first, second]),
    switchCurrentModel: (first: string, second?: string, third?: string) => invoke("switchCurrentModel", third === undefined ? [first] : [first, second, third]),
    addModel: (first: string | Record<string, unknown>, second?: Record<string, unknown>) => invoke("addModel", typeof first === "string" ? [first, second || {}] : [first]),
    updateModel: (input) => invoke("updateModel", [input]),
    testModel: (selection) => invoke("testModel", [selection]),
    deleteModel: (selection) => invoke("deleteModel", [selection]),
    setReasoning: (...args: string[]) => invoke("setReasoning", args),
    exportComputerLogs: () => invoke("exportComputerLogs", []),
    exportBrowserLogs: () => invoke("exportBrowserLogs", []),
    setupBrowserExtension: (browser = "edge", extensionConnected = false) => invoke("setupBrowserExtension", [browser, extensionConnected]),
    revealPath: (targetPath) => invoke("revealPath", [targetPath]),
    copyImageSource: (source) => invoke("copyImageSource", [source]),
    readClipboardText: async () => navigator.clipboard?.readText?.() ?? "",
    writeClipboardText: async (value) => {
      await navigator.clipboard.writeText(String(value || ""));
      return true;
    },
    openExternal: async (value) => {
      const url = safeExternalUrl(value);
      window.open(url.toString(), "_blank", "noopener,noreferrer");
      return true;
    },
    openLocalArtifact: async (targetPath, workspaceRoot) => {
      const url = await invoke<{ base64?: string; mimeType?: string }>("readLocalArtifact", [targetPath, workspaceRoot]);
      const blobUrl = base64ToBlobUrl(url);
      window.open(blobUrl, "_blank", "noopener,noreferrer");
      return true;
    },
    localArtifactPreviewUrl: async (targetPath, workspaceRoot) => {
      const payload = await invoke<{ base64?: string; mimeType?: string }>("readLocalArtifact", [targetPath, workspaceRoot]);
      return base64ToBlobUrl(payload);
    },
    readLocalImage: (targetPath, workspaceRoot) => invoke("readLocalImage", [targetPath, workspaceRoot]),
    readLocalMedia: (targetPath, workspaceRoot) => invoke("readLocalMedia", [targetPath, workspaceRoot]),
    pickDirectory: async () => "",
    pickFiles: pickAndStageFiles,
    setZoomFactor: (factor) => {
      const numeric = Number(factor);
      const safe = Number.isFinite(numeric) ? Math.min(1.3, Math.max(0.9, numeric)) : 1;
      document.documentElement.style.zoom = String(safe);
      return safe;
    },
    filePathFor: () => "",
    stageTempFile: async (name, bytes) => {
      if (bytes.byteLength > MAX_STAGED_BYTES) throw new Error("Attachment is too large for Loom Web (48 MB max).");
      return invoke("stageTempFile", [name, bytesToBase64(bytes)]);
    },
    onNotification: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };

  Reflect.set(window, "loom", bridge);
  window.addEventListener("beforeunload", () => {
    closeSocket();
    for (const url of blobUrls) URL.revokeObjectURL(url);
    blobUrls.clear();
  });
}
