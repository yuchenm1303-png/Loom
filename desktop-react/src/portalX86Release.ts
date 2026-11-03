/**
 * Independent Windows x86 release channel. The existing x64 stable release
 * parser, URL and updater are intentionally untouched.
 *
 * Windows 32-bit installers are published as explicitly marked prereleases
 * tagged win32-vX.Y.Z. /releases/latest must always remain x64-only.
 * Do not advertise an unverified Actions artifact as a permanent download.
 */
import { RELEASES_URL } from "./portalRelease";

export const X86_RELEASE_API_URL = "https://api.github.com/repos/yuchenm1303-png/Loom/releases?per_page=50";
export const X86_RELEASES_URL = `${RELEASES_URL}?q=win32-v`;

export type PortalX86Release = {
  version: string;
  package: string;
  download: string;
  notes: string;
};

export function parsePortalX86Release(payload: unknown): PortalX86Release | null {
  if (!Array.isArray(payload)) throw new Error("Invalid GitHub release list");
  for (const item of payload) {
    if (!item || typeof item !== "object") continue;
    const release = item as Record<string, unknown>;
    if (release.draft || release.prerelease !== true || typeof release.tag_name !== "string"
        || !/^win32-v\d+\.\d+\.\d+$/.test(release.tag_name) || !Array.isArray(release.assets)) continue;
    const asset = release.assets.find((entry) => entry && typeof entry === "object"
      && (entry as Record<string, unknown>).name === "Loom-Setup-x86.exe") as Record<string, unknown> | undefined;
    const expected = `${RELEASES_URL}/download/${release.tag_name}/Loom-Setup-x86.exe`;
    if (!asset || asset.browser_download_url !== expected
        || !Number.isSafeInteger(asset.size) || (asset.size as number) <= 1_000_000) continue;
    return {
      version: release.tag_name.replace(/^win32-/, ""),
      package: `${((asset.size as number) / 1024 / 1024).toFixed(1)} MiB`,
      download: expected,
      notes: `${RELEASES_URL}/tag/${release.tag_name}`,
    };
  }
  return null;
}

export async function fetchPortalX86Release(signal: AbortSignal): Promise<PortalX86Release | null> {
  const response = await fetch(X86_RELEASE_API_URL, {
    signal,
    cache: "no-store",
    headers: { Accept: "application/vnd.github+json" },
  });
  if (!response.ok) throw new Error(`x86 release lookup failed: ${response.status}`);
  return parsePortalX86Release(await response.json());
}
