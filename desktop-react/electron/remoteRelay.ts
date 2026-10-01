import { app, BrowserWindow, dialog, Menu, nativeImage, shell, Tray } from "electron";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import WebSocket from "ws";
import { LoomAccountClient } from "./accountClient.js";
import { webRelayDeviceIdentity } from "./webRelayAuth.js";

// The Loom Web device relay runs inside the same Electron main process that
// owns Loom Desktop's App Server. The browser is only another client of that
// local Host; this module never starts a second Agent Runtime.

const DEFAULT_RELAY_URL = "wss://loom.smirel.com/api/ws/device";
const DEFAULT_WEB_URL = "https://loom.smirel.com";
const LOCAL_DISCOVERY_HOST = "127.0.0.1";
const LOCAL_DISCOVERY_PORT = 39223;
const LOCAL_STATUS_PATH = "/loom/status";
const LOCAL_OPEN_PATH = "/loom/open";
const LOCAL_PAIR_PATH = "/loom/pair";
const LOCAL_BODY_LIMIT = 4 * 1024;
const HEARTBEAT_MS = 30_000;
const RETRY_MIN_MS = 4_000;
const RETRY_MAX_MS = 30_000;
const BACKGROUND_HOST_ARG = "--loom-background-host";

export type WebRelayAuth = {
  accessToken: string;
  deviceId: string;
  deviceName: string;
  platform: string;
  appVersion: string;
};

export type WebRelayOperation = (args: unknown[]) => Promise<unknown>;
export type WebRelayOperations = Record<string, WebRelayOperation>;

export type WebRelayOptions = {
  /** Re-read per connect attempt so a refreshed token is picked up on reconnect. */
  auth: () => Promise<WebRelayAuth>;
  operations: WebRelayOperations;
};

type InvokeFrame = {
  type: "invoke";
  browserId: string;
  id: number;
  operation: string;
  args?: unknown[];
};

let ws: WebSocket | null = null;
let options: WebRelayOptions | null = null;
let stopped = true;
let retryMs = RETRY_MIN_MS;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let localDiscoveryServer: Server | null = null;

// Desktop's visible window is only a UI client. Keep the Electron main process
// alive as Loom Host when that window is closed, so loom.smirel.com keeps using
// the exact same App Server, model registry, conversations, approvals and tools.
const backgroundHostLaunch = process.argv.includes(BACKGROUND_HOST_ARG);
const primaryInstance = app.requestSingleInstanceLock();
let allowHostQuit = false;
let uiRequested = !backgroundHostLaunch;
let hostTray: Tray | null = null;

function loomMainWindow(): BrowserWindow | null {
  return BrowserWindow.getAllWindows().find((window) => !window.isDestroyed() && window.getTitle() === "Loom") ?? null;
}

function showLoomWindow(): void {
  uiRequested = true;
  const window = loomMainWindow();
  if (!window) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

function normalizedOrigin(value: string): string {
  try { return new URL(value).origin; } catch { return ""; }
}

function allowedLocalOrigin(request: IncomingMessage): string {
  const origin = normalizedOrigin(String(request.headers.origin || ""));
  if (!origin) return "";
  const production = normalizedOrigin(String(process.env.LOOM_WEB_ORIGIN || DEFAULT_WEB_URL));
  const allowed = new Set([
    production,
    "http://127.0.0.1:5173",
    "http://localhost:5173",
  ].filter(Boolean));
  return allowed.has(origin) ? origin : "";
}

function writeLocalJson(
  response: ServerResponse,
  statusCode: number,
  payload: Record<string, unknown>,
  origin = "",
): void {
  const body = Buffer.from(JSON.stringify(payload));
  response.statusCode = statusCode;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("X-Content-Type-Options", "nosniff");
  response.setHeader("Content-Length", String(body.byteLength));
  if (origin) {
    response.setHeader("Access-Control-Allow-Origin", origin);
    response.setHeader("Vary", "Origin");
  }
  response.end(body);
}

async function readLocalJson(request: IncomingMessage): Promise<Record<string, unknown>> {
  const declared = Number(request.headers["content-length"] || 0);
  if (!Number.isFinite(declared) || declared < 0 || declared > LOCAL_BODY_LIMIT) throw new Error("request_too_large");
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const part of request) {
    const chunk = Buffer.isBuffer(part) ? part : Buffer.from(part);
    total += chunk.byteLength;
    if (total > LOCAL_BODY_LIMIT) throw new Error("request_too_large");
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString("utf8") || "{}";
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid_json");
  return parsed as Record<string, unknown>;
}

function reconnectRelayNow(): void {
  retryMs = RETRY_MIN_MS;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  const current = ws;
  ws = null;
  try { current?.close(); } catch {}
  void connectRelay();
}

async function handleLocalDiscovery(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const origin = allowedLocalOrigin(request);
  const suppliedOrigin = Boolean(String(request.headers.origin || "").trim());

  if (request.method === "OPTIONS") {
    if (!origin) {
      writeLocalJson(response, 403, { ok: false, error: "origin_not_allowed" });
      return;
    }
    response.statusCode = 204;
    response.setHeader("Access-Control-Allow-Origin", origin);
    response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    response.setHeader("Access-Control-Allow-Headers", "Content-Type");
    response.setHeader("Access-Control-Allow-Private-Network", "true");
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("Vary", "Origin");
    response.end();
    return;
  }

  if (suppliedOrigin && !origin) {
    writeLocalJson(response, 403, { ok: false, error: "origin_not_allowed" });
    return;
  }

  const url = new URL(request.url || "/", `http://${LOCAL_DISCOVERY_HOST}:${LOCAL_DISCOVERY_PORT}`);
  if (request.method === "GET" && url.pathname === LOCAL_STATUS_PATH) {
    const identity = await webRelayDeviceIdentity();
    writeLocalJson(response, 200, {
      ok: true,
      ...identity,
      relayReady: ws?.readyState === WebSocket.OPEN,
    }, origin);
    return;
  }

  if (request.method === "POST" && url.pathname === LOCAL_PAIR_PATH) {
    if (!origin) {
      writeLocalJson(response, 403, { ok: false, error: "origin_required" });
      return;
    }
    try {
      const body = await readLocalJson(request);
      const pairingTicket = String(body.pairing_ticket || "").trim();
      if (!pairingTicket) {
        writeLocalJson(response, 400, { ok: false, error: "pairing_ticket_required" }, origin);
        return;
      }
      const snapshot = await new LoomAccountClient().pairDevice(pairingTicket);
      if (!snapshot.authenticated) throw new Error("pairing_failed");
      reconnectRelayNow();
      writeLocalJson(response, 200, { ok: true }, origin);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      writeLocalJson(response, 401, { ok: false, error: "pairing_failed", message }, origin);
    }
    return;
  }

  if (request.method === "POST" && url.pathname === LOCAL_OPEN_PATH) {
    if (!origin) {
      writeLocalJson(response, 403, { ok: false, error: "origin_required" });
      return;
    }
    showLoomWindow();
    writeLocalJson(response, 200, { ok: true }, origin);
    return;
  }

  writeLocalJson(response, 404, { ok: false, error: "not_found" }, origin);
}

function startLocalDiscovery(): void {
  if (localDiscoveryServer || !primaryInstance) return;
  const server = createServer((request, response) => {
    void handleLocalDiscovery(request, response).catch(() => {
      if (!response.headersSent) writeLocalJson(response, 500, { ok: false, error: "internal_error" });
      else response.end();
    });
  });
  localDiscoveryServer = server;
  server.on("error", (error) => {
    if (localDiscoveryServer === server) localDiscoveryServer = null;
    console.warn("Loom local Web discovery is unavailable", error instanceof Error ? error.message : String(error));
  });
  server.listen(LOCAL_DISCOVERY_PORT, LOCAL_DISCOVERY_HOST);
}

function stopLocalDiscovery(): void {
  const server = localDiscoveryServer;
  localDiscoveryServer = null;
  server?.close();
}

async function openLocalLoomWeb(): Promise<void> {
  const target = new URL(DEFAULT_WEB_URL);
  try {
    const identity = await webRelayDeviceIdentity();
    if (identity.deviceId) target.searchParams.set("local_device", identity.deviceId);
  } catch {
    // The loopback discovery path is the normal path now. The query marker is a
    // backwards-compatible accelerator for browsers that block loopback access.
  }
  await shell.openExternal(target.toString());
}

async function ensureHostTray(): Promise<void> {
  if (hostTray || process.platform === "darwin") return;
  let icon = nativeImage.createEmpty();
  try { icon = await app.getFileIcon(process.execPath, { size: "small" }); } catch {}
  hostTray = new Tray(icon);
  hostTray.setToolTip("Loom Host · local Web access available in background");
  hostTray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Loom", click: () => showLoomWindow() },
    { label: "Open Loom Web", click: () => void openLocalLoomWeb() },
    { type: "separator" },
    {
      label: "Quit Loom Host",
      click: () => {
        allowHostQuit = true;
        app.quit();
      },
    },
  ]));
  hostTray.on("double-click", () => showLoomWindow());
}

function configureBackgroundHostStartup(): void {
  if (process.platform !== "win32" || !app.isPackaged) return;
  try {
    app.setLoginItemSettings({
      openAtLogin: true,
      path: process.execPath,
      args: [BACKGROUND_HOST_ARG],
    });
  } catch (error) {
    console.warn("Could not configure Loom Host login startup", error);
  }
}

app.on("browser-window-created", (_event, window) => {
  if (window.getTitle() !== "Loom") return;
  window.on("close", (event) => {
    if (allowHostQuit) return;
    event.preventDefault();
    window.hide();
  });

  if (backgroundHostLaunch && !uiRequested) {
    const keepHidden = () => {
      if (!uiRequested && !window.isDestroyed()) setImmediate(() => {
        if (!uiRequested && !window.isDestroyed()) window.hide();
      });
    };
    window.on("show", keepHidden);
    window.once("ready-to-show", keepHidden);
  }
});

if (!primaryInstance) {
  app.quit();
} else {
  app.on("second-instance", (_event, argv) => {
    if (argv.includes(BACKGROUND_HOST_ARG)) return;
    if (app.isReady()) showLoomWindow();
    else void app.whenReady().then(() => showLoomWindow());
  });
}

app.whenReady().then(async () => {
  if (!primaryInstance) return;
  configureBackgroundHostStartup();
  startLocalDiscovery();
  await ensureHostTray();
});

app.on("before-quit", () => {
  allowHostQuit = true;
  stopLocalDiscovery();
  hostTray?.destroy();
  hostTray = null;
});

function clearTimers(): void {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  reconnectTimer = null;
  heartbeatTimer = null;
}

function relayUrl(): string {
  return String(process.env.LOOM_WEB_RELAY_URL || DEFAULT_RELAY_URL).trim() || DEFAULT_RELAY_URL;
}

function scheduleReconnect(): void {
  if (stopped || reconnectTimer) return;
  const delay = retryMs;
  retryMs = Math.min(RETRY_MAX_MS, Math.round(retryMs * 1.6));
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connectRelay();
  }, delay);
}

function send(frame: unknown): void {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame));
}

async function handleInvoke(frame: InvokeFrame): Promise<void> {
  const args = Array.isArray(frame.args) ? frame.args : [];
  const operation = options?.operations[frame.operation];
  try {
    let result: unknown;
    if (operation) {
      result = await operation(args);
    } else if (frame.operation === "pickDirectory") {
      const selection = await dialog.showOpenDialog({
        title: "Add project folder",
        properties: ["openDirectory", "createDirectory"],
      });
      result = selection.canceled || !selection.filePaths.length ? "" : selection.filePaths[0];
    } else {
      throw new Error(`Unsupported Loom Web operation: ${frame.operation}`);
    }
    send({ type: "invoke_result", browserId: frame.browserId, id: frame.id, result });
  } catch (cause) {
    send({
      type: "invoke_result",
      browserId: frame.browserId,
      id: frame.id,
      error: { message: cause instanceof Error ? cause.message : String(cause) },
    });
  }
}

async function connectRelay(): Promise<void> {
  if (stopped || !options || !primaryInstance) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  let auth: WebRelayAuth;
  try {
    auth = await options.auth();
  } catch {
    scheduleReconnect();
    return;
  }
  if (stopped || !options) return;

  const candidate = new WebSocket(relayUrl(), {
    headers: { Authorization: `Bearer ${auth.accessToken}` },
    maxPayload: 64 * 1024 * 1024,
  });
  ws = candidate;

  candidate.on("open", () => {
    if (ws !== candidate) return;
    retryMs = RETRY_MIN_MS;
    send({
      type: "device_hello",
      device: {
        id: auth.deviceId,
        name: auth.deviceName,
        platform: auth.platform,
        version: auth.appVersion,
      },
    });
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => send({ type: "ping" }), HEARTBEAT_MS);
  });
  candidate.on("message", (data) => {
    let frame: { type?: string } & Partial<InvokeFrame>;
    try { frame = JSON.parse(data.toString()) as typeof frame; } catch { return; }
    if (frame.type === "invoke" && typeof frame.id === "number" && frame.browserId && frame.operation) {
      void handleInvoke(frame as InvokeFrame);
    }
  });
  candidate.on("close", () => {
    if (ws === candidate) ws = null;
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    scheduleReconnect();
  });
  candidate.on("error", () => {
    // The close event owns reconnect/backoff. Keep auth tokens and payloads out of logs.
  });
}

export function startWebRelay(next: WebRelayOptions): void {
  options = next;
  stopped = false;
  retryMs = RETRY_MIN_MS;
  void connectRelay();
}

export function stopWebRelay(): void {
  stopped = true;
  options = null;
  clearTimers();
  const current = ws;
  ws = null;
  current?.close();
}

/** Forwards a runtime notification to whichever Loom Web clients are attached. */
export function sendRelayNotification(payload: unknown): void {
  send({ type: "notification", payload });
}
