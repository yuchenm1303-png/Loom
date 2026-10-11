import { app, net } from "electron";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { broadcastHostEvent, hostHasDesktopClients, isHostProcess } from "./hostProcess.js";
import { hostDataPath } from "./hostPaths.js";
import { applicationHash, prepareApplicationRelease, readApplicationPointer, readApplicationRelease, updateApplicationPointer, validateApplicationRelease } from "./applicationUpdateStore.js";

const root = path.join(hostDataPath, "application-updates");
const bundled = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const channel = "https://raw.githubusercontent.com/yuchenm1303-png/Loom/main/application-runtime/stable.json";
const enabled = isHostProcess && app.isPackaged && process.env.LOOM_DISABLE_AUTO_UPDATES !== "1";
export interface ApplicationUpdateStatus {
  phase: "idle" | "checking" | "downloading" | "downloaded" | "error";
  availableVersion?: string; releaseName?: string; percent?: number; transferred?: number; total?: number; error?: string;
}
let status: ApplicationUpdateStatus = { phase: "idle" };
let changed = () => {};
let safeToRestart: (() => boolean | Promise<boolean>) | undefined;
let checking: Promise<void> | null = null;
let restarting = false;
export function applicationUpdateState() { return { ...status }; }
export function onApplicationUpdateStatus(callback: () => void) { changed = callback; }
export function registerApplicationRestartGuard(guard: () => boolean | Promise<boolean>) { safeToRestart = guard; }
function setStatus(next: ApplicationUpdateStatus) { status = next; changed(); }
async function fetchBytes(url: string, limit: number) {
  const response = await net.fetch(url, { cache: "no-store", signal: AbortSignal.timeout(60000) });
  if (!response.ok || !response.body) throw new Error(`Application update HTTP ${response.status}`);
  const reader = response.body.getReader(), chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.length; if (size > limit) throw new Error("Application update response too large");
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); }
  return Buffer.concat(chunks);
}
export async function checkApplicationUpdates() {
  if (!enabled || checking || status.phase === "downloaded") return checking;
  checking = (async () => {
    if (globalThis.loomApplicationBootstrap) globalThis.loomApplicationBootstrap.downloading = true;
    setStatus({ phase: "checking" });
    try {
      const response = await fetchBytes(channel, 4 * 1024 * 1024);
      const raw = JSON.parse(response.toString());
      // A newer dependency baseline belongs to the installer updater.
      if (raw.bootstrap !== app.getVersion()) { setStatus({ phase: "idle" }); return; }
      const release = validateApplicationRelease(raw, app.getVersion());
      const baseline = await fs.readFile(path.join(bundled, "dist/ui-baseline.json"), "utf8").then(JSON.parse).catch(() => null);
      if (baseline && (release.sourceTime < baseline.sourceTime || release.sourceSha === baseline.sourceSha)) {
        setStatus({ phase: "idle" }); return;
      }
      const pointer = await readApplicationPointer(root);
      if (release.build <= Math.max(pointer.active || 0, pointer.ready || 0) || pointer.rejected?.includes(release.build)) {
        const pending = pointer.ready && await readApplicationRelease(path.join(root, String(pointer.ready)), app.getVersion());
        setStatus(pending ? { phase: "downloaded", availableVersion: `${app.getVersion()}+${pending.build}`, releaseName: "Loom application update", percent: 100 }
          : { phase: "idle" }); return;
      }
      const total = release.files.reduce((sum, file) => sum + file.size, 0);
      let transferred = 0;
      setStatus({ phase: "downloading", availableVersion: `${app.getVersion()}+${release.build}`, releaseName: "Loom application update", percent: 0, total, transferred });
      await prepareApplicationRelease(root, release, async file => {
        let bytes: Buffer | undefined;
        try {
          const local = await fs.readFile(path.join(bundled, file.path));
          if (local.length === file.size && applicationHash(local) === file.sha256) bytes = local;
        } catch {}
        bytes ??= await fetchBytes(file.url, file.size);
        transferred += bytes.length;
        setStatus({ ...status, transferred, percent: Math.min(99, transferred / total * 100) });
        return bytes;
      });
      await updateApplicationPointer(root, current => ({ ...current, ready: release.build }));
      setStatus({ ...status, phase: "downloaded", percent: 100, transferred: total });
      if (!hostHasDesktopClients()) await installApplicationUpdate();
    } catch (error) {
      setStatus({ phase: "error", error: error instanceof Error ? error.message : "Application update failed" });
    }
  })().finally(() => {
    if (globalThis.loomApplicationBootstrap) globalThis.loomApplicationBootstrap.downloading = false;
    checking = null;
  });
  return checking;
}
export async function installApplicationUpdate(): Promise<boolean> {
  if (!enabled || restarting || status.phase !== "downloaded" || !safeToRestart) return false;
  try { if (!await safeToRestart()) return false; } catch { return false; }
  restarting = true;
  const reopen = hostHasDesktopClients();
  broadcastHostEvent("loom:host-updating", null);
  // The new Host selects the staged release; UI children then follow its build.
  setTimeout(() => {
    app.relaunch({ args: ["--loom-host", ...(reopen ? ["--loom-open-desktop-after-update"] : [])] });
    app.quit();
  }, 500);
  return true;
}
if (enabled) app.whenReady().then(() => {
  const startup = setTimeout(() => { void checkApplicationUpdates(); }, 5000); startup.unref();
  const poll = setInterval(() => { void checkApplicationUpdates(); }, 30000); poll.unref();
  const idle = setInterval(() => { if (!hostHasDesktopClients()) void installApplicationUpdate(); }, 30000); idle.unref();
  app.once("before-quit", () => { clearTimeout(startup); clearInterval(poll); clearInterval(idle); });
});
