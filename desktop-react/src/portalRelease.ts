export const RELEASES_URL = "https://github.com/yuchenm1303-png/Loom/releases";
export const LATEST_INSTALLER_URL = `${RELEASES_URL}/latest/download/Loom-Setup-x64.exe`;
export const RELEASE_API_URL = "https://api.github.com/repos/yuchenm1303-png/Loom/releases/latest";

export type PortalRelease = { version: string; package: string; download: string; notes: string };

// Do not present an old build version as the latest release when GitHub is unavailable.
export const FALLBACK_RELEASE: PortalRelease = {
  version: "", package: "", download: LATEST_INSTALLER_URL, notes: `${RELEASES_URL}/latest`,
};

export function parsePortalRelease(payload: unknown): PortalRelease {
  if (!payload || typeof payload !== "object") throw new Error("Invalid release");
  const release = payload as Record<string, unknown>;
  if (release.draft || release.prerelease || typeof release.tag_name !== "string"
    || !/^v?\d+\.\d+\.\d+$/.test(release.tag_name) || !Array.isArray(release.assets)) {
    throw new Error("Invalid stable release");
  }
  const asset = release.assets.find((item) => item?.name === "Loom-Setup-x64.exe");
  const download = `${RELEASES_URL}/download/${release.tag_name}/Loom-Setup-x64.exe`;
  if (!asset || asset.browser_download_url !== download || !Number.isSafeInteger(asset.size) || asset.size <= 0) {
    throw new Error("Windows installer unavailable");
  }
  return {
    version: release.tag_name.startsWith("v") ? release.tag_name : `v${release.tag_name}`,
    package: `${(asset.size / 1024 / 1024).toFixed(1)} MiB`,
    download, notes: `${RELEASES_URL}/tag/${release.tag_name}`,
  };
}

export async function fetchPortalRelease(signal: AbortSignal): Promise<PortalRelease> {
  const response = await fetch(RELEASE_API_URL, {
    signal, cache: "no-store", headers: { Accept: "application/vnd.github+json" },
  });
  if (!response.ok) throw new Error(`Release request failed: ${response.status}`);
  return parsePortalRelease(await response.json());
}
