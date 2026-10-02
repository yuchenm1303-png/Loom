import type { LoomAccountResult, LoomAuthCapabilitiesResult, LoomAuthChallengeResult } from "./types/account";
import type { LoomNotification } from "./types/global";
import { LOOM_WEB_REQUIRED_HOST_PROTOCOL, type LocalHostUpdateState } from "./localHostDiscovery";

const WEB_PLATFORM_MARKER = "web";
const DEFAULT_WS_PATH = "/api/ws/browser";
const SOCKET_WAIT_MS = 8_000;
const CONNECT_INVOKE_TIMEOUT_MS = 40_000;
const INVOKE_TIMEOUT_MS = 120_000;
const HEARTBEAT_MS = 30_000;
const MAX_STAGED_BYTES = 48 * 1024 * 1024;
const LOCAL_DEVICE_QUERY = "local_device";
const LOCAL_DEVICE_STORAGE_KEY = "loom.web.localDeviceId";
const REMOTE_DEVICE_SESSION_KEY = "loom.web.remoteDeviceId";

type InvokeMessage = {
  type: "invoke";
  id: number;
  operation: string;
  args: unknown[];
};

export type WebRelayDevice = Record<string, unknown> & { id?: string; name?: string; platform?: string; version?: string; hostProtocol?: number };

export type WebDeviceStatus = {
  type: "device_status";
  online: boolean;
  device?: WebRelayDevice | null;
  selectedDeviceId?: string | null;
  devices?: WebRelayDevice[];
};

export type WebHostCompatibility = {
  compatible: boolean;
  legacy: boolean;
  online: boolean;
  hostProtocol: number;
  requiredProtocol: number;
  update?: LocalHostUpdateState | null;
  error?: string;
};

function relayHostProtocol(device: WebRelayDevice | null | undefined): number {
  return Math.max(0, Number(device?.hostProtocol || 0) || 0);
}

export function webHostNeedsProtocolUpdate(status: WebDeviceStatus | null | undefined): boolean {
  const protocol = relayHostProtocol(status?.device);
  // Protocol 0 predates the handshake. Allow it during the migration release.
  return Boolean(status?.online && protocol > 0 && protocol < LOOM_WEB_REQUIRED_HOST_PROTOCOL);
}

type RelayMessage =
  | WebDeviceStatus
  | { type: "invoke_result"; id: number; result?: unknown; error?: { message?: string; code?: string } }
  | { type: "notification"; payload?: LoomNotification }
  | { type: "pong" };

type PendingCall = {
  resolve(value: unknown): void;
  reject(error: Error): void;
  timeout: number;
};

const listeners = new Set<(payload: LoomNotification) => void>();
const pending = new Map<number, PendingCall>();
let socket: WebSocket | null = null;
let socketPromise: Promise<WebSocket> | null = null;
let connectPromise: Promise<unknown> | null = null;
let nextId = 1;
let heartbeatTimer: number | null = null;
let localDeviceId = "";
let lastDeviceStatus: WebDeviceStatus | null = null;
const blobUrls = new Set<string>();

function normalizeDeviceId(value: unknown): string {
  const candidate = String(value || "").trim();
  if (!candidate || candidate.length > 128) return "";
  return /^[A-Za-z0-9_.:-]+$/.test(candidate) ? candidate : "";
}

function captureLocalDeviceBinding(): string {
  let queryDeviceId = "";
  try {
    const url = new URL(window.location.href);
    queryDeviceId = normalizeDeviceId(url.searchParams.get(LOCAL_DEVICE_QUERY));
    if (queryDeviceId) {
      window.localStorage.setItem(LOCAL_DEVICE_STORAGE_KEY, queryDeviceId);
      try { window.sessionStorage.removeItem(REMOTE_DEVICE_SESSION_KEY); } catch {}
      url.searchParams.delete(LOCAL_DEVICE_QUERY);
      window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
    }
  } catch {
    // Storage can be unavailable in hardened/private browser profiles. In that
    // case the current navigation still keeps the device id in memory.
  }

  if (queryDeviceId) {
    localDeviceId = queryDeviceId;
    return localDeviceId;
  }
  if (localDeviceId) return localDeviceId;
  try {
    localDeviceId = normalizeDeviceId(window.localStorage.getItem(LOCAL_DEVICE_STORAGE_KEY));
  } catch {
    localDeviceId = "";
  }
  return localDeviceId;
}

function remoteWebDeviceId(): string {
  try {
    return normalizeDeviceId(window.sessionStorage.getItem(REMOTE_DEVICE_SESSION_KEY));
  } catch {
    return "";
  }
}

export function webExecutionMode(): "local" | "remote" {
  const remote = remoteWebDeviceId();
  const local = captureLocalDeviceBinding();
  return remote && remote !== local ? "remote" : "local";
}

export function selectedWebDeviceId(): string {
  const remote = remoteWebDeviceId();
  return remote || captureLocalDeviceBinding();
}

function webSocketUrl(): string {
  const configured = String(import.meta.env.VITE_LOOM_WEB_SOCKET_URL || "").trim();
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  const target = new URL(configured || `${scheme}//${window.location.host}${DEFAULT_WS_PATH}`, window.location.href);
  const selectedDeviceId = selectedWebDeviceId();
  if (selectedDeviceId) target.searchParams.set("device", selectedDeviceId);
  else target.searchParams.delete("device");
  return target.toString();
}

function dispatchAuthChanged(): void {
  window.dispatchEvent(new CustomEvent("loom:web-auth-changed"));
}

type AccountBridgeResult = LoomAccountResult | LoomAuthCapabilitiesResult | LoomAuthChallengeResult;

async function accountRequest<T extends AccountBridgeResult = LoomAccountResult>(path: string, init: RequestInit = {}): Promise<T> {
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
    } as T;
  }
  const payload = await response.json().catch(() => ({})) as AccountBridgeResult;
  if (payload && typeof payload === "object" && "ok" in payload) return payload as T;
  return {
    ok: false,
    error: {
      code: "ACCOUNT_RESPONSE_INVALID",
      message: `Loom web service returned an invalid response (${response.status}).`,
      status: response.status,
    },
  } as T;
}

function stopHeartbeat(): void {
  if (heartbeatTimer !== null) window.clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

function failPending(message: string): void {
  for (const call of pending.values()) {
    window.clearTimeout(call.timeout);
    call.reject(new Error(message));
  }
  pending.clear();
}

function closeSocket(): void {
  const current = socket;
  socket = null;
  socketPromise = null;
  lastDeviceStatus = null;
  stopHeartbeat();
  failPending("Loom Web connection closed.");
  if (current && current.readyState < WebSocket.CLOSING) current.close(1000, "client closed");
}

function relayError(payload: { message?: string; code?: string }): Error {
  const error = new Error(payload.message || "Remote Loom operation failed.") as Error & { code?: string };
  error.code = payload.code;
  return error;
}

function handleRelayMessage(raw: string): void {
  let message: RelayMessage;
  try {
    message = JSON.parse(raw) as RelayMessage;
  } catch {
    return;
  }

  if (message.type === "device_status") {
    lastDeviceStatus = message;
    window.dispatchEvent(new CustomEvent("loom:web-device-status", { detail: message }));
    return;
  }
  if (message.type === "invoke_result") {
    const call = pending.get(message.id);
    if (!call) return;
    pending.delete(message.id);
    window.clearTimeout(call.timeout);
    if (message.error) call.reject(relayError(message.error));
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

  const account = await accountRequest("status");
  if (!account.ok || !account.snapshot.authenticated) {
    throw new Error("Sign in to Loom Web before connecting.");
  }

  socketPromise = new Promise<WebSocket>((resolve, reject) => {
    const ws = new WebSocket(webSocketUrl());
    socket = ws;
    const timeout = window.setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        ws.close();
        reject(new Error("Could not connect to Loom Web."));
      }
    }, SOCKET_WAIT_MS);

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
        stopHeartbeat();
        failPending("Loom Web connection closed.");
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

async function invoke<T = unknown>(operation: string, args: unknown[] = []): Promise<T> {
  let ws = await ensureSocket();
  if (ws.readyState !== WebSocket.OPEN) {
    if (socket === ws) closeSocket();
    ws = await ensureSocket();
  }
  const id = nextId++;
  const message: InvokeMessage = { type: "invoke", id, operation, args };
  const timeoutMs = operation === "connect" ? CONNECT_INVOKE_TIMEOUT_MS : INVOKE_TIMEOUT_MS;
  const result = new Promise<unknown>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      const call = pending.get(id);
      if (!call) return;
      pending.delete(id);
      reject(new Error(operation === "connect"
        ? "Loom Host runtime did not become ready in time. Retry the connection; Loom will recover the local runtime automatically."
        : `Loom Host did not respond in time (${operation}).`));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timeout });
  });
  try {
    if (ws.readyState !== WebSocket.OPEN) throw new Error("Loom Web connection closed before the request was sent.");
    ws.send(JSON.stringify(message));
  } catch (cause) {
    const call = pending.get(id);
    if (call) window.clearTimeout(call.timeout);
    pending.delete(id);
    if (socket === ws) closeSocket();
    throw cause instanceof Error ? cause : new Error("Could not send the Loom Web request.");
  }
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

export function localWebDeviceId(): string {
  return captureLocalDeviceBinding();
}

export function currentWebDeviceStatus(): WebDeviceStatus | null {
  return lastDeviceStatus ? { ...lastDeviceStatus, devices: [...(lastDeviceStatus.devices || [])] } : null;
}

export async function getWebDeviceStatus(): Promise<WebDeviceStatus> {
  const existing = currentWebDeviceStatus();
  if (existing) return existing;
  await ensureSocket();
  const afterConnect = currentWebDeviceStatus();
  if (afterConnect) return afterConnect;
  return new Promise<WebDeviceStatus>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      window.removeEventListener("loom:web-device-status", onStatus);
      reject(new Error("Could not load Loom devices."));
    }, 2_500);
    const onStatus = (event: Event) => {
      window.clearTimeout(timeout);
      window.removeEventListener("loom:web-device-status", onStatus);
      resolve((event as CustomEvent<WebDeviceStatus>).detail);
    };
    window.addEventListener("loom:web-device-status", onStatus);
  });
}

export async function ensureWebHostCompatibility(
  status?: WebDeviceStatus | null,
): Promise<WebHostCompatibility> {
  const current = status || await getWebDeviceStatus();
  const hostProtocol = relayHostProtocol(current.device);
  if (!current.online) {
    return { compatible: false, legacy: false, online: false, hostProtocol, requiredProtocol: LOOM_WEB_REQUIRED_HOST_PROTOCOL };
  }
  if (hostProtocol <= 0) {
    return { compatible: true, legacy: true, online: true, hostProtocol, requiredProtocol: LOOM_WEB_REQUIRED_HOST_PROTOCOL };
  }
  if (hostProtocol >= LOOM_WEB_REQUIRED_HOST_PROTOCOL) {
    return { compatible: true, legacy: false, online: true, hostProtocol, requiredProtocol: LOOM_WEB_REQUIRED_HOST_PROTOCOL };
  }
  try {
    const result = await invoke<{ hostProtocol?: number; requiredProtocol?: number; compatible?: boolean; update?: LocalHostUpdateState }>(
      "hostUpdateEnsure",
      [LOOM_WEB_REQUIRED_HOST_PROTOCOL],
    );
    return {
      compatible: Boolean(result?.compatible),
      legacy: false,
      online: true,
      hostProtocol: Math.max(0, Number(result?.hostProtocol || hostProtocol) || hostProtocol),
      requiredProtocol: Math.max(0, Number(result?.requiredProtocol || LOOM_WEB_REQUIRED_HOST_PROTOCOL) || LOOM_WEB_REQUIRED_HOST_PROTOCOL),
      update: result?.update || null,
    };
  } catch (cause) {
    return {
      compatible: false, legacy: false, online: true, hostProtocol, requiredProtocol: LOOM_WEB_REQUIRED_HOST_PROTOCOL,
      error: cause instanceof Error ? cause.message : String(cause),
    };
  }
}

export async function selectWebDevice(_deviceId: string): Promise<void> {
  // Compatibility shim for older UI modules. Routing is account-scoped:
  // there is exactly one current Host, so there is nothing to select.
  await ensureSocket();
}

export function activateWebRemoteDevice(deviceId: string): void {
  const target = normalizeDeviceId(deviceId);
  if (!target) return;
  const local = captureLocalDeviceBinding();
  try {
    if (target === local) window.sessionStorage.removeItem(REMOTE_DEVICE_SESSION_KEY);
    else window.sessionStorage.setItem(REMOTE_DEVICE_SESSION_KEY, target);
  } catch {
    // Remote mode is intentionally session-scoped. If storage is unavailable,
    // the browser simply remains on its current execution target.
    return;
  }
  closeSocket();
  window.location.reload();
}

export function activateLocalWebDevice(): void {
  try { window.sessionStorage.removeItem(REMOTE_DEVICE_SESSION_KEY); } catch {}
  closeSocket();
  window.location.reload();
}

export function installWebBridge(): void {
  if (Reflect.get(window, "loom")) return;
  document.documentElement.dataset.loomPlatform = WEB_PLATFORM_MARKER;
  captureLocalDeviceBinding();

  const bridge: Window["loom"] = {
    connect: async () => {
      if (connectPromise) return connectPromise;
      connectPromise = (async () => {
        const account = await accountRequest("status");
        if (!account.ok || !account.snapshot.authenticated) {
          throw new Error("Sign in to Loom Web before connecting.");
        }
        const deviceStatus = await getWebDeviceStatus();
        const compatibility = await ensureWebHostCompatibility(deviceStatus);
        if (!compatibility.compatible) {
          const phase = String(compatibility.update?.phase || "");
          const error = new Error(phase === "downloaded"
            ? "Loom Host update is ready. Restart Loom on the host computer to finish updating."
            : "Loom Host is updating to match Loom Web. It will reconnect automatically when ready.") as Error & { code?: string };
          error.code = "HOST_UPDATE_REQUIRED";
          throw error;
        }
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
    accountStatus: () => accountRequest<LoomAccountResult>("status"),
    accountCapabilities: () => accountRequest<LoomAuthCapabilitiesResult>("capabilities"),
    accountLogin: async (email, password) => {
      const result = await accountRequest<LoomAccountResult>("login", { method: "POST", body: JSON.stringify({ email, password }) });
      if (result.ok) dispatchAuthChanged();
      return result;
    },
    accountRegister: async (email, password) => {
      const result = await accountRequest<LoomAccountResult>("register", { method: "POST", body: JSON.stringify({ email, password }) });
      if (result.ok) dispatchAuthChanged();
      return result;
    },
    accountRegisterStart: (email, password) => accountRequest<LoomAuthChallengeResult>("register/start", { method: "POST", body: JSON.stringify({ email, password }) }),
    accountVerifyEmail: async (challengeId, code) => {
      const result = await accountRequest<LoomAccountResult>("verify-email", { method: "POST", body: JSON.stringify({ challenge_id: challengeId, code }) });
      if (result.ok) dispatchAuthChanged();
      return result;
    },
    accountResendEmail: (challengeId) => accountRequest<LoomAuthChallengeResult>("resend-email", { method: "POST", body: JSON.stringify({ challenge_id: challengeId }) }),
    accountForgotPassword: (email) => accountRequest<LoomAuthChallengeResult>("forgot-password", { method: "POST", body: JSON.stringify({ email }) }),
    accountResetPassword: async (challengeId, code, password) => {
      const result = await accountRequest<LoomAccountResult>("reset-password", { method: "POST", body: JSON.stringify({ challenge_id: challengeId, code, password }) });
      if (result.ok) dispatchAuthChanged();
      return result;
    },
    accountOAuthExchange: async (code) => {
      const result = await accountRequest<LoomAccountResult>("oauth/exchange", { method: "POST", body: JSON.stringify({ code }) });
      if (result.ok) dispatchAuthChanged();
      return result;
    },
    accountLogout: async () => {
      try { window.sessionStorage.removeItem(REMOTE_DEVICE_SESSION_KEY); } catch {}
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
    readClipboardText: () => invoke("readClipboardText", []),
    writeClipboardText: (value) => invoke("writeClipboardText", [String(value || "")]),
    openExternal: (value) => {
      const url = safeExternalUrl(value);
      return invoke("openExternal", [url.toString()]);
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
    pickDirectory: () => invoke("pickDirectory", []),
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