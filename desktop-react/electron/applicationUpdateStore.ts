import fs from "node:fs/promises";
import path from "node:path";
import { prepareRendererRelease, rendererHash, verifyRendererRelease, type RendererRelease } from "./rendererUpdateStore.js";

export interface ApplicationRelease extends RendererRelease { kind: "application"; sourceSha: string; sourceTime: number }
export function validateApplicationRelease(raw: unknown, bootstrap: string): ApplicationRelease {
  const release = raw as ApplicationRelease;
  if (!release || release.schema !== 1 || release.kind !== "application" || release.bootstrap !== bootstrap
    || !Number.isSafeInteger(release.build) || release.build <= 0 || !Array.isArray(release.files)
    || !release.files.length || release.files.length > 10000 || !/^[a-f0-9]{40}$/.test(release.sourceSha)
    || !Number.isSafeInteger(release.sourceTime) || release.sourceTime < 0) throw new Error("Incompatible application update");
  const seen = new Set<string>();
  let total = 0;
  for (const file of release.files) {
    if (!file || !/^(dist\/|dist-electron\/|package\.json$)/.test(file.path)
      || !/^[a-zA-Z0-9_@./-]+$/.test(file.path)
      || file.path.split("/").some(part => !part || part === "." || part === ".." || /^(con|prn|aux|nul|com\d|lpt\d)(\.|$)/i.test(part))
      || seen.has(file.path.toLowerCase()) || !Number.isSafeInteger(file.size) || file.size < 0
      || !/^[a-f0-9]{64}$/.test(file.sha256)
      || !new RegExp(`^https://github\\.com/yuchenm1303-png/Loom/releases/download/app-\\d+/${file.sha256}$`).test(file.url)) {
      throw new Error("Invalid application update file");
    }
    seen.add(file.path.toLowerCase()); total += file.size;
  }
  for (const required of ["package.json", "dist/index.html", "dist/ui-baseline.json", "dist-electron/applicationEntry.js", "dist-electron/main.js", "dist-electron/preload.cjs", "dist-electron/hudPreload.cjs"]) {
    if (!seen.has(required.toLowerCase())) throw new Error("Incomplete application update");
  }
  if (total > 256 * 1024 * 1024) throw new Error("Application update too large");
  return release;
}
export { prepareRendererRelease as prepareApplicationRelease, verifyRendererRelease as verifyApplicationRelease, rendererHash as applicationHash };
export async function readApplicationRelease(directory: string, bootstrap: string): Promise<ApplicationRelease | null> {
  try { return validateApplicationRelease(JSON.parse(await fs.readFile(path.join(directory, "release.json"), "utf8")), bootstrap); }
  catch { return null; }
}
export interface ApplicationPointer {
  active?: number; previous?: number; ready?: number;
  booting?: { build: number; pid: number };
  session?: { build: number; pid: number };
  rejected?: number[];
}
export async function readApplicationPointer(root: string): Promise<ApplicationPointer> {
  try {
    const value = JSON.parse(await fs.readFile(path.join(root, "state.json"), "utf8"));
    for (const key of ["active", "previous", "ready"]) {
      if (value[key] !== undefined && (!Number.isSafeInteger(value[key]) || value[key] <= 0)) throw new Error("Invalid application pointer");
    }
    for (const key of ["booting", "session"]) {
      if (value[key] && (!Number.isSafeInteger(value[key].build) || value[key].build < 0
        || !Number.isSafeInteger(value[key].pid) || value[key].pid <= 0)) throw new Error("Invalid application session");
    }
    if (value.rejected && (!Array.isArray(value.rejected) || !value.rejected.every((build: number) => Number.isSafeInteger(build) && build > 0))) throw new Error("Invalid application quarantine");
    return value;
  } catch { return {}; }
}
let work: Promise<unknown> = Promise.resolve();
export function updateApplicationPointer(root: string, change: (value: ApplicationPointer) => ApplicationPointer): Promise<ApplicationPointer> {
  // Only the Host writes the pointer; the desktop reads its live session.
  const next = work.catch(() => {}).then(async () => {
    const value = change(await readApplicationPointer(root));
    await fs.mkdir(root, { recursive: true });
    const temporary = path.join(root, `state-${process.pid}.tmp`);
    await fs.writeFile(temporary, JSON.stringify(value));
    await fs.rename(temporary, path.join(root, "state.json"));
    return value;
  });
  work = next; return next;
}
export async function pruneApplicationReleases(root: string, pointer: ApplicationPointer) {
  const keep = new Set([pointer.active, pointer.previous, pointer.ready, pointer.booting?.build, pointer.session?.build].filter(Boolean));
  const objects = new Set<string>();
  for (const id of keep) {
    try {
      const release = JSON.parse(await fs.readFile(path.join(root, String(id), "release.json"), "utf8"));
      for (const file of release.files) if (/^[a-f0-9]{64}$/.test(file.sha256)) objects.add(file.sha256);
    } catch { return; } // Preserve cached data if a retained manifest is damaged.
  }
  for (const name of await fs.readdir(root)) {
    if (/^\d+$/.test(name) && !keep.has(Number(name))) await fs.rm(path.join(root, name), { recursive: true, force: true });
  }
  for (const hash of await fs.readdir(path.join(root, "objects")).catch(() => [])) {
    if (/^[a-f0-9]{64}$/.test(hash) && !objects.has(hash)) await fs.unlink(path.join(root, "objects", hash));
  }
}
