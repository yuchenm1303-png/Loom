const LOCAL_HOST_ORIGIN = "http://127.0.0.1:39223";
const LOCAL_HOST_STATUS_URL = `${LOCAL_HOST_ORIGIN}/loom/status`;
const LOCAL_HOST_PAIR_URL = `${LOCAL_HOST_ORIGIN}/loom/pair`;
const LOCAL_HOST_UPDATE_URL = `${LOCAL_HOST_ORIGIN}/loom/update`;
const LOCAL_DEVICE_STORAGE_KEY = "loom.web.localDeviceId";

export const LOOM_WINDOWS_INSTALLER_URL = "https://github.com/yuchenm1303-png/Loom/releases/latest/download/Loom-Setup-x64.exe";
export const LOOM_WEB_REQUIRED_HOST_PROTOCOL = 1;

export type LocalHostUpdateState = {
  enabled?: boolean;
  phase?: string;
  currentVersion?: string;
  availableVersion?: string;
  percent?: number;
  error?: string;
};

export type LocalLoomHost = {
  deviceId: string;
  deviceName: string;
  platform: string;
  appVersion: string;
  hostVersion: string;
  hostMode: "background" | "desktop" | string;
  hostProtocol: number;
  relayReady: boolean;
  update?: LocalHostUpdateState | null;
};

export type LocalPairResult = {
  ok: boolean;
  error: string;
};

type TargetAddressSpaceRequestInit = RequestInit & {
  targetAddressSpace?: "loopback";
};

type NavigatorWithUAData = Navigator & {
  userAgentData?: { platform?: string };
};

function requestInit(init: RequestInit = {}): TargetAddressSpaceRequestInit {
  return {
    ...init,
    cache: "no-store",
    credentials: "omit",
    mode: "cors",
    targetAddressSpace: "loopback",
  };
}

function normalizeDeviceId(value: unknown): string {
  const candidate = String(value || "").trim();
  if (!candidate || candidate.length > 128) return "";
  return /^[A-Za-z0-9_.:-]+$/.test(candidate) ? candidate : "";
}

function messageFromPayload(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") return fallback;
  const body = payload as { message?: unknown; error?: unknown };
  if (typeof body.message === "string" && body.message.trim()) return body.message.trim();
  if (body.error && typeof body.error === "object") {
    const error = body.error as { message?: unknown };
    if (typeof error.message === "string" && error.message.trim()) return error.message.trim();
  }
  if (typeof body.error === "string" && body.error.trim()) return body.error.trim();
  return fallback;
}

/**
 * Read-only loopback discovery. The local endpoint never exposes App Server
 * operations or credentials; it only tells loom.smirel.com which Host is on
 * this physical computer.
 */
export async function discoverLocalLoomHost(signal?: AbortSignal): Promise<LocalLoomHost | null> {
  try {
    const response = await fetch(LOCAL_HOST_STATUS_URL, requestInit({ method: "GET", signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(5000)]) : AbortSignal.timeout(5000) }));
    if (!response.ok) return null;
    const payload = await response.json() as Partial<LocalLoomHost> & { ok?: boolean };
    const deviceId = normalizeDeviceId(payload.deviceId);
    if (!payload.ok || !deviceId) return null;
    return {
      deviceId,
      deviceName: String(payload.deviceName || "This computer"),
      platform: String(payload.platform || ""),
      appVersion: String(payload.appVersion || ""),
      hostVersion: String(payload.hostVersion || payload.appVersion || ""),
      hostMode: String(payload.hostMode || "desktop"),
      hostProtocol: Math.max(0, Number(payload.hostProtocol || 0) || 0),
      relayReady: Boolean(payload.relayReady),
      update: payload.update && typeof payload.update === "object" ? payload.update as LocalHostUpdateState : null,
    };
  } catch {
    return null;
  }
}

export type LocalHostCompatibilityResult = {
  ok: boolean;
  compatible: boolean;
  hostProtocol: number;
  requiredProtocol: number;
  update?: LocalHostUpdateState | null;
  error: string;
};

export function localHostNeedsProtocolUpdate(host: Pick<LocalLoomHost, "hostProtocol">): boolean {
  // Protocol 0 is a pre-handshake Host. Keep it usable during the first rollout;
  // its existing desktop updater will move it onto protocol-aware builds.
  return host.hostProtocol > 0 && host.hostProtocol < LOOM_WEB_REQUIRED_HOST_PROTOCOL;
}

export async function ensureLocalLoomHostCompatibility(
  requiredProtocol = LOOM_WEB_REQUIRED_HOST_PROTOCOL,
): Promise<LocalHostCompatibilityResult> {
  try {
    const response = await fetch(LOCAL_HOST_UPDATE_URL, requestInit({
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ required_protocol: requiredProtocol }),
    }));
    const payload = await response.json().catch(() => ({})) as {
      ok?: boolean; compatible?: boolean; hostProtocol?: number; requiredProtocol?: number; update?: LocalHostUpdateState; error?: unknown; message?: unknown;
    };
    if (!response.ok || !payload.ok) {
      return {
        ok: false, compatible: false, hostProtocol: Number(payload.hostProtocol || 0), requiredProtocol,
        update: payload.update || null, error: messageFromPayload(payload, "Could not update Loom Host."),
      };
    }
    return {
      ok: true, compatible: Boolean(payload.compatible),
      hostProtocol: Math.max(0, Number(payload.hostProtocol || 0) || 0),
      requiredProtocol: Math.max(0, Number(payload.requiredProtocol || requiredProtocol) || requiredProtocol),
      update: payload.update || null, error: "",
    };
  } catch {
    return { ok: false, compatible: false, hostProtocol: 0, requiredProtocol, update: null, error: "Could not ask Loom Host to update." };
  }
}

export function rememberLocalLoomHost(deviceId: string): void {
  const normalized = normalizeDeviceId(deviceId);
  if (!normalized) return;
  try { window.localStorage.setItem(LOCAL_DEVICE_STORAGE_KEY, normalized); } catch {}
}

/**
 * Pair the already signed-in website with the Host on this computer without
 * exposing the browser's HttpOnly credentials to localhost. The gateway mints
 * a 120-second one-time ticket; the Host exchanges it for its own independent
 * refresh session and reconnects the authenticated WSS relay.
 */
export async function pairLocalLoomHost(signal?: AbortSignal): Promise<LocalPairResult> {
  try {
    const boundedSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000);
    const issueResponse = await fetch("/api/auth/device-pair", {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" }, signal: boundedSignal,
    });
    const issued = await issueResponse.json().catch(() => ({})) as {
      ok?: boolean;
      pairing_ticket?: string;
      error?: unknown;
    };
    const pairingTicket = String(issued.pairing_ticket || "").trim();
    if (!issueResponse.ok || !issued.ok || !pairingTicket) {
      return { ok: false, error: messageFromPayload(issued, "Could not authorize Loom Host pairing.") };
    }

    const pairResponse = await fetch(LOCAL_HOST_PAIR_URL, requestInit({
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ pairing_ticket: pairingTicket }), signal: boundedSignal,
    }));
    const paired = await pairResponse.json().catch(() => ({})) as { ok?: boolean; error?: unknown; message?: unknown };
    if (!pairResponse.ok || !paired.ok) {
      return { ok: false, error: messageFromPayload(paired, "Loom Host could not finish pairing.") };
    }
    return { ok: true, error: "" };
  } catch {
    return { ok: false, error: "Could not securely pair Loom Host on this computer." };
  }
}

export function isWindowsBrowser(): boolean {
  const nav = navigator as NavigatorWithUAData;
  const platform = String(nav.userAgentData?.platform || nav.platform || nav.userAgent || "").toLowerCase();
  return platform.includes("win");
}
