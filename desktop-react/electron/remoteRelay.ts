import WebSocket from "ws";

// The Loom Web device relay. This runs in the Electron *main* process, not in a
// renderer or preload: the gateway's device socket authenticates the access
// token from an `Authorization: Bearer` header, and only the `ws` package can
// set WebSocket headers. A sandboxed preload has no access to `ws` at all.
//
// Transport only. The operations table and the auth lookup are injected by
// main.ts, which keeps this module free of any import back into main.ts and
// gives the IPC handlers and the relay one shared implementation.

const DEFAULT_RELAY_URL = "wss://loom.smirel.com/api/ws/device";
const HEARTBEAT_MS = 30_000;
const RETRY_MIN_MS = 4_000;
const RETRY_MAX_MS = 30_000;

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
  const operation = options?.operations[frame.operation];
  try {
    if (!operation) throw new Error(`Unsupported Loom Web operation: ${frame.operation}`);
    const result = await operation(Array.isArray(frame.args) ? frame.args : []);
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
  if (stopped || !options) return;
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
