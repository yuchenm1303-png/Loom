const LOCAL_HOST_ORIGIN = "http://127.0.0.1:39223";
const LOCAL_HOST_STATUS_URL = `${LOCAL_HOST_ORIGIN}/loom/status`;
const LOCAL_HOST_PAIR_URL = `${LOCAL_HOST_ORIGIN}/loom/pair`;
const LOCAL_DEVICE_STORAGE_KEY = "loom.web.localDeviceId";

export const LOOM_WINDOWS_INSTALLER_URL = "https://github.com/yuchenm1303-png/Loom/releases/latest/download/Loom-Setup-x64.exe";

export type LocalLoomHost = {
  deviceId: string;
  deviceName: string;
  platform: string;
  appVersion: string;
  relayReady: boolean;
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
    const response = await fetch(LOCAL_HOST_STATUS_URL, requestInit({ method: "GET",
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(5000)]) : AbortSignal.timeout(5000),
    }));
    if (!response.ok) return null;
    const payload = await response.json() as Partial<LocalLoomHost> & { ok?: boolean };
    const deviceId = normalizeDeviceId(payload.deviceId);
    if (!payload.ok || !deviceId) return null;
    return {
      deviceId,
      deviceName: String(payload.deviceName || "This computer"),
      platform: String(payload.platform || ""),
      appVersion: String(payload.appVersion || ""),
      relayReady: Boolean(payload.relayReady),
    };
  } catch {
    return null;
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
      headers: { Accept: "application/json" },
      signal: boundedSignal,
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
      body: JSON.stringify({ pairing_ticket: pairingTicket }),
      signal: boundedSignal,
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
