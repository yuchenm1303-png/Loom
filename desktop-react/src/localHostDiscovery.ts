const LOCAL_HOST_ORIGIN = "http://127.0.0.1:39223";
const LOCAL_HOST_STATUS_URL = `${LOCAL_HOST_ORIGIN}/loom/status`;
const LOCAL_HOST_OPEN_URL = `${LOCAL_HOST_ORIGIN}/loom/open`;
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

export async function openLocalLoomHost(): Promise<boolean> {
  try {
    const response = await fetch(LOCAL_HOST_OPEN_URL, requestInit({ method: "POST" }));
    return response.ok;
  } catch {
    return false;
  }
}

export function isWindowsBrowser(): boolean {
  const platform = String(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || "").toLowerCase();
  return platform.includes("win");
}
