import fs from "node:fs";

export function validateUpdateSource(value: string): string {
  if (!value.trim()) return "";
  const url = new URL(value);
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash
    || url.port || /^(localhost|127\.|\[|0\.)/i.test(url.hostname)) throw new Error("Update source must be a public HTTPS URL without credentials, port or query");
  return url.href.replace(/\/+$/, "");
}

export function configuredUpdateSource(): string {
  if (process.env.LOOM_UPDATE_SOURCE_URL !== undefined) return validateUpdateSource(process.env.LOOM_UPDATE_SOURCE_URL);
  // Contract tests use synthetic network peers, never a packaged production CDN.
  if (process.env.NODE_TEST_CONTEXT) return "";
  let config: { url?: string };
  try { config = JSON.parse(fs.readFileSync(new URL("./update-source.json", import.meta.url), "utf8")); }
  catch { return ""; }
  return validateUpdateSource(config.url || "");
}

// Keep canonical GitHub URLs in manifests for old-client compatibility. Only
// locally configured, narrowly mapped release/channel paths use the mirror.
export function mirroredUpdateUrl(original: string, source = configuredUpdateSource()): string | null {
  if (!source) return null;
  const root = validateUpdateSource(source), url = new URL(original);
  if (url.username || url.password || url.port || url.hash) return null;
  if (url.origin === "https://github.com") {
    const match = /^\/yuchenm1303-png\/Loom\/releases\/download\/([\w.-]+)\/([\w.-]+)$/.exec(url.pathname);
    if (match) return `${root}/releases/${match[1]}/${match[2]}`;
  }
  if (url.origin === "https://raw.githubusercontent.com") {
    const match = /^\/yuchenm1303-png\/Loom\/main\/((?:application-runtime|ui-runtime|host-runtime)\/stable\.json)$/.exec(url.pathname);
    if (match) return `${root}/channels/${match[1]}${url.search}`;
  }
  return null;
}

export async function fetchUpdateSource(
  original: string, init: RequestInit, fetcher: (url: string, init: RequestInit) => Promise<Response>,
): Promise<Response> {
  const source = configuredUpdateSource();
  const mirror = mirroredUpdateUrl(original, source);
  const configuredReleasePrefix = source ? `${source}/releases/` : "";
  const releasePath = configuredReleasePrefix && original.startsWith(configuredReleasePrefix)
    ? original.slice(configuredReleasePrefix.length) : "";
  const canonical = /^[\w.-]+\/[\w.-]+$/.test(releasePath)
    ? `https://github.com/yuchenm1303-png/Loom/releases/download/${releasePath}` : original;
  if (mirror || canonical !== original) {
    const controller = new AbortController();
    // A dead mirror must not consume the caller's entire transfer deadline.
    const timer = setTimeout(() => controller.abort(), 8000);
    timer.unref?.();
    const headers = new Headers(init.headers);
    for (const key of ["authorization", "cookie", "proxy-authorization"]) headers.delete(key);
    try {
      const response = await fetcher(mirror || original, { ...init, headers,
        signal: init.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal });
      if (response.ok) return response;
      await response.body?.cancel();
    } catch {
      init.signal?.throwIfAborted();
    } finally { clearTimeout(timer); }
  }
  if (canonical !== original) {
    const headers = new Headers(init.headers);
    for (const key of ["authorization", "cookie", "proxy-authorization"]) headers.delete(key);
    return fetcher(canonical, { ...init, headers });
  }
  return fetcher(original, init);
}
