import { ipcRenderer } from "electron";
import WebSocket from "ws";

const DEFAULT_RELAY_URL = "wss://loom.smirel.com/api/ws/device";
const HEARTBEAT_MS = 30_000;
const RETRY_MIN_MS = 4_000;
const RETRY_MAX_MS = 30_000;
const MAX_ARTIFACT_BYTES = 48 * 1024 * 1024;

type RelayAuth = {
  accessToken: string;
  userId: number;
  email: string;
  deviceId: string;
  deviceName: string;
  platform: string;
  appVersion: string;
};

type InvokeFrame = {
  type: "invoke";
  browserId: string;
  id: number;
  operation: string;
  args?: unknown[];
};

let ws: WebSocket | null = null;
let retryMs = RETRY_MIN_MS;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;

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
  if (reconnectTimer) return;
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

async function readArtifact(targetPath: string, workspaceRoot: string): Promise<Record<string, unknown>> {
  const url = await ipcRenderer.invoke("loom:local-artifact-preview-url", targetPath, workspaceRoot) as string;
  if (!url) throw new Error("Artifact preview is unavailable.");
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Artifact preview failed (${response.status}).`);
  const buffer = Buffer.from(await response.arrayBuffer());
  if (buffer.byteLength > MAX_ARTIFACT_BYTES) throw new Error("Artifact is too large for Loom Web preview (48 MB max).");
  return {
    base64: buffer.toString("base64"),
    mimeType: response.headers.get("content-type") || "application/octet-stream",
  };
}

async function invokeDesktop(operation: string, args: unknown[]): Promise<unknown> {
  switch (operation) {
    case "connect": return ipcRenderer.invoke("loom:connect");
    case "call": return ipcRenderer.invoke("loom:call", String(args[0] || ""), (args[1] || {}) as Record<string, unknown>);
    case "listModels": return ipcRenderer.invoke("loom:model-list", Boolean(args[0]));
    case "setModelProviderKey": return ipcRenderer.invoke("loom:model-provider-key", String(args[0] || ""), String(args[1] || ""));
    case "switchModelProfile": return args.length > 1
      ? ipcRenderer.invoke("loom:model-switch", String(args[0] || ""), String(args[1] || ""))
      : ipcRenderer.invoke("loom:model-switch", String(args[0] || ""));
    case "switchCurrentModel": return args.length > 2
      ? ipcRenderer.invoke("loom:model-switch-current", String(args[0] || ""), String(args[1] || ""), String(args[2] || ""))
      : ipcRenderer.invoke("loom:model-switch-current", String(args[0] || ""));
    case "addModel": return args.length > 1
      ? ipcRenderer.invoke("loom:model-add", String(args[0] || ""), args[1] || {})
      : ipcRenderer.invoke("loom:model-add", args[0] || {});
    case "updateModel": return ipcRenderer.invoke("loom:model-update", args[0] || {});
    case "testModel": return ipcRenderer.invoke("loom:model-test", String(args[0] || ""));
    case "deleteModel": return ipcRenderer.invoke("loom:model-delete", String(args[0] || ""));
    case "setReasoning": return ipcRenderer.invoke("loom:reasoning-set", ...args.map((value) => String(value || "")));
    case "exportComputerLogs": return ipcRenderer.invoke("loom:export-computer-logs");
    case "exportBrowserLogs": return ipcRenderer.invoke("loom:export-browser-logs");
    case "setupBrowserExtension": return ipcRenderer.invoke("loom:setup-browser-extension", args[0] || "edge", Boolean(args[1]));
    case "revealPath": return ipcRenderer.invoke("loom:reveal-path", String(args[0] || ""));
    case "copyImageSource": return ipcRenderer.invoke("loom:copy-image-source", String(args[0] || ""));
    case "readClipboardText": return ipcRenderer.invoke("loom:clipboard-read-text");
    case "writeClipboardText": return ipcRenderer.invoke("loom:clipboard-write-text", String(args[0] || ""));
    case "openExternal": return ipcRenderer.invoke("loom:open-external", String(args[0] || ""));
    case "readLocalImage": return ipcRenderer.invoke("loom:read-local-image", String(args[0] || ""), String(args[1] || ""));
    case "readLocalMedia": return ipcRenderer.invoke("loom:read-local-media", String(args[0] || ""), String(args[1] || ""));
    case "stageTempFile": {
      const bytes = Buffer.from(String(args[1] || ""), "base64");
      if (bytes.byteLength > MAX_ARTIFACT_BYTES) throw new Error("Attachment is too large for Loom Web (48 MB max).");
      return ipcRenderer.invoke("loom:stage-temp-file", String(args[0] || "attachment"), new Uint8Array(bytes));
    }
    case "readLocalArtifact": return readArtifact(String(args[0] || ""), String(args[1] || ""));
    default: throw new Error(`Unsupported Loom Web operation: ${operation}`);
  }
}

async function handleInvoke(frame: InvokeFrame): Promise<void> {
  try {
    const result = await invokeDesktop(frame.operation, Array.isArray(frame.args) ? frame.args : []);
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
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  let auth: RelayAuth;
  try {
    auth = await ipcRenderer.invoke("loom:web-relay-auth") as RelayAuth;
  } catch {
    scheduleReconnect();
    return;
  }

  const candidate = new WebSocket(relayUrl(), {
    headers: { Authorization: `Bearer ${auth.accessToken}` },
    maxPayload: 64 * 1024 * 1024,
  });
  ws = candidate;

  candidate.on("open", () => {
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

ipcRenderer.on("loom:notification", (_event, payload) => {
  send({ type: "notification", payload });
});

void connectRelay();
window.addEventListener?.("beforeunload", () => {
  clearTimers();
  ws?.close();
  ws = null;
});
