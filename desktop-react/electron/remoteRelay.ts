import { app, BrowserWindow, dialog, Menu, nativeImage, shell, Tray } from "electron";
import WebSocket from "ws";

// The Loom Web device relay runs inside the same Electron main process that
// owns Loom Desktop's App Server. The browser is only another client of that
// local Host; this module never starts a second Agent Runtime.

const DEFAULT_RELAY_URL = "wss://loom.smirel.com/api/ws/device";
const DEFAULT_WEB_URL = "https://loom.smirel.com";
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

async function openLocalLoomWeb(): Promise<void> {
  const target = new URL(DEFAULT_WEB_URL);
  try {
    const auth = await options?.auth();
    if (auth?.deviceId) target.searchParams.set("local_device", auth.deviceId);
  } catch {
    // The web sign-in screen is still useful when Desktop is signed out. It will
    // not auto-select any remote Host because no local device marker is present.
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

// Register before main.ts creates its BrowserWindow. Preventing the close keeps
// main.ts's existing `closed -> app.quit()` path from firing; the window simply
// becomes a hidden client while the Host and relay remain alive.
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
  await ensureHostTray();
});

app.on("before-quit", () => {
  allowHostQuit = true;
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
      // Native directory selection is a Host shell capability. Desktop already
      // exposes the same dialog through IPC; Web reaches it through the relay.
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