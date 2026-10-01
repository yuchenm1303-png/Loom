const LOCAL_HOST_ORIGIN = "http://127.0.0.1:39223";
const LOCAL_HOST_STATUS_URL = `${LOCAL_HOST_ORIGIN}/loom/status`;
const LOCAL_HOST_OPEN_URL = `${LOCAL_HOST_ORIGIN}/loom/open`;
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

/**
 * Read-only loopback discovery. The local endpoint never exposes App Server
 * operations or credentials; it only tells loom.smirel.com which Host is on
 * this physical computer.
 */
export async function discoverLocalLoomHost(signal?: AbortSignal): Promise<LocalLoomHost | null> {
  try {
    const response = await fetch(LOCAL_HOST_STATUS_URL, requestInit({ method: "GET", signal }));
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
 * Give a freshly installed Host its own account session without exposing the
 * browser's HttpOnly access/refresh cookies to JavaScript or localhost. The
 * gateway only returns a short-lived, one-time pairing ticket.
 */
export async function pairLocalLoomHost(): Promise<void> {
  const issue = await fetch("/api/auth/device-pair", {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const issued = await issue.json().catch(() => ({})) as {
    ok?: boolean;
    pairing_ticket?: string;
    error?: { message?: string };
  };
  const pairingTicket = String(issued.pairing_ticket || "").trim();
  if (!issue.ok || !issued.ok || !pairingTicket) {
    throw new Error(String(issued.error?.message || "Could not authorize Loom Host on this computer."));
  }

  const exchange = await fetch(LOCAL_HOST_PAIR_URL, requestInit({
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ pairing_ticket: pairingTicket }),
  }));
  const exchanged = await exchange.json().catch(() => ({})) as {
    ok?: boolean;
    message?: string;
  };
  if (!exchange.ok || !exchanged.ok) {
    throw new Error(String(exchanged.message || "Loom Host could not finish secure pairing."));
  }
}

export async function openLocalLoomHost(): Promise<boolean> {
  try {
    const response = await fetch(LOCAL_HOST_OPEN_URL, requestInit({ method: "POST" }));
    return response.ok;
  } catch {
    return false;
  }
}

export function isWindowsBrowser(): boolean {
  const nav = navigator as NavigatorWithUAData;
  const platform = String(nav.userAgentData?.platform || nav.platform || nav.userAgent || "").toLowerCase();
  return platform.includes("win");
}
