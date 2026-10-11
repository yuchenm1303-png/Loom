import { app, net, ipcMain, type BrowserWindow } from "electron";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { hostDataPath } from "./hostPaths.js";
import { prepareRendererRelease, readRendererRelease, rendererHash, validateRendererRelease, verifyRendererRelease } from "./rendererUpdateStore.js";

const root = path.join(hostDataPath, "ui-updates");
const bundledRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const channel = "https://raw.githubusercontent.com/yuchenm1303-png/Loom/main/ui-runtime/stable.json";
interface Pointer { active?: number; ready?: number; booting?: number; rejected?: number[] }
async function readPointer(): Promise<Pointer> {
  try {
    const data = JSON.parse(await fs.readFile(path.join(root, "state.json"), "utf8"));
    for (const key of ["active", "ready", "booting"]) if (data[key] !== undefined && (!Number.isSafeInteger(data[key]) || data[key] <= 0)) throw new Error("Invalid pointer");
    if (data.rejected && (!Array.isArray(data.rejected) || !data.rejected.every((n: number) => Number.isSafeInteger(n) && n > 0))) throw new Error("Invalid quarantine");
    return data;
  } catch { return {}; }
}
async function writePointer(value: Pointer) {
  await fs.mkdir(root, { recursive: true });
  const temporary = path.join(root, `state-${process.pid}.tmp`);
  await fs.writeFile(temporary, JSON.stringify(value));
  await fs.rename(temporary, path.join(root, "state.json"));
}
let pointerWork: Promise<unknown> = Promise.resolve();
function updatePointer(change: (current: Pointer) => Pointer): Promise<Pointer> {
  const work = pointerWork.catch(() => {}).then(async () => {
    const next = change(await readPointer());
    await writePointer(next);
    return next;
  });
  pointerWork = work;
  return work;
}
async function fetchBytes(url: string, limit: number) {
  const response = await net.fetch(url, { cache: "no-store", signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw new Error(`UI update HTTP ${response.status}`);
  const reader = response.body!.getReader(), chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) throw new Error("UI update response too large");
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); }
  return Buffer.concat(chunks);
}
let checking: Promise<void> | null = null;
async function check() {
  if (checking) return checking;
  checking = (async () => {
    try {
      const release = validateRendererRelease(JSON.parse((await fetchBytes(channel, 4 * 1024 * 1024)).toString()), app.getVersion());
      const baseline = await fs.readFile(path.join(bundledRoot, "ui-baseline.json"), "utf8").then(JSON.parse).catch(() => null);
      if (baseline && (!release.sourceTime || release.sourceTime < baseline.sourceTime
        || release.sourceSha === baseline.sourceSha)) return;
      const pointer = await readPointer();
      if (release.build <= Math.max(pointer.active || 0, pointer.ready || 0) || pointer.rejected?.includes(release.build)) return;
      await prepareRendererRelease(root, release, async file => {
        try {
          const bytes = await fs.readFile(path.join(bundledRoot, file.path));
          if (bytes.length === file.size && rendererHash(bytes) === file.sha256) return bytes;
        } catch {}
        return fetchBytes(file.url, file.size);
      });
      // Activation deliberately waits until the next window launch. Drafts and
      // executing turns in the current renderer are never interrupted.
      await updatePointer(latest => latest.rejected?.includes(release.build) ? latest : { ...latest, ready: release.build });
    } catch (error) { console.warn("UI update check failed", error instanceof Error ? error.message : "request failed"); }
  })().finally(() => { checking = null; });
  return checking;
}
let scheduled = false;
export async function loadUpdatedRenderer(window: BrowserWindow, bundledIndex: string) {
  const contents = window.webContents;
  if (!app.isPackaged || process.env.LOOM_DISABLE_AUTO_UPDATES === "1") return window.loadFile(bundledIndex);
  if (!scheduled) {
    scheduled = true;
    const timer = setTimeout(() => { void check(); }, 10000); timer.unref();
    const interval = setInterval(() => { void check(); }, 5 * 60000); interval.unref();
    app.on("before-quit", () => { clearTimeout(timer); clearInterval(interval); });
  }
  let pointer = await readPointer();
  // A candidate left booting by a crash must not cause a restart loop.
  if (pointer.booting) {
    pointer = { ...pointer, rejected: [...(pointer.rejected || []), pointer.booting].slice(-20), ready: undefined, booting: undefined };
    await updatePointer(() => pointer);
  }
  const candidate = pointer.ready || pointer.active;
  if (!candidate) return window.loadFile(bundledIndex);
  const directory = path.join(root, String(candidate));
  const release = await readRendererRelease(directory, app.getVersion());
  if (!release) return window.loadFile(bundledIndex);
  const baseline = await fs.readFile(path.join(path.dirname(bundledIndex), "ui-baseline.json"), "utf8").then(JSON.parse).catch(() => null);
  if (baseline && (!release.sourceTime || release.sourceTime < baseline.sourceTime
    || release.sourceSha === baseline.sourceSha)) return window.loadFile(bundledIndex);
  try { await verifyRendererRelease(directory, release); }
  catch { return window.loadFile(bundledIndex); }
  const previous = pointer.active;
  await updatePointer(latest => ({ ...latest, booting: candidate }));
  let settled = false;
  const restore = async () => {
    if (settled) return;
    settled = true; cleanup();
    await updatePointer(latest => ({ ...latest, active: previous === candidate ? undefined : previous,
      ready: latest.ready === candidate ? undefined : latest.ready, booting: undefined,
      rejected: [...(latest.rejected || []), candidate].slice(-20) }));
    if (window.isDestroyed()) return;
    const old = previous && previous !== candidate ? await readRendererRelease(path.join(root, String(previous)), app.getVersion()) : null;
    if (old) {
      try { await verifyRendererRelease(path.join(root, String(previous)), old); await window.loadFile(path.join(root, String(previous), "index.html")); return; } catch {}
    }
    await window.loadFile(bundledIndex);
  };
  const healthy = async (event: Electron.IpcMainEvent) => {
    if (event.sender !== window.webContents || settled || window.webContents.getURL().split("#")[0] !== pathToFileURL(path.join(directory, "index.html")).href) return;
    settled = true; cleanup();
    const latest = await updatePointer(current => ({ ...current, active: candidate,
      ready: current.ready === candidate ? undefined : current.ready, booting: undefined }));
    void prune(new Set([candidate, previous, latest.ready].filter((value): value is number => Boolean(value)))).catch(console.error);
  };
  const fail = () => { void restore().catch(console.error); };
  const timeout = setTimeout(fail, 30000);
  const cleanup = () => {
    clearTimeout(timeout); ipcMain.removeListener("loom:renderer-healthy", onHealthy);
    contents.removeListener("render-process-gone", fail);
    contents.removeListener("did-fail-load", fail);
  };
  const onHealthy = (event: Electron.IpcMainEvent) => { void healthy(event).catch(console.error); };
  ipcMain.on("loom:renderer-healthy", onHealthy);
  window.webContents.once("render-process-gone", fail);
  window.webContents.once("did-fail-load", fail);
  window.once("closed", () => {
    cleanup();
    if (!settled) {
      settled = true;
      // A deliberate close during startup is not a broken update.
      void updatePointer(current => ({ ...current, booting: undefined })).catch(console.error);
    }
  });
  try { await window.loadFile(path.join(directory, "index.html")); } catch { await restore(); }
}

async function prune(keep: Set<number>) {
  const objects = new Set<string>();
  for (const build of keep) {
    const release = await readRendererRelease(path.join(root, String(build)), app.getVersion());
    release?.files.forEach(file => objects.add(file.sha256));
  }
  // Do not race a staged download. Retain only the active and previous builds.
  if (checking) return;
  for (const name of await fs.readdir(root)) {
    if (/^\d+$/.test(name) && !keep.has(Number(name))) await fs.rm(path.join(root, name), { recursive: true, force: true });
  }
  for (const name of await fs.readdir(path.join(root, "objects")).catch(() => [])) {
    if (/^[a-f0-9]{64}$/.test(name) && !objects.has(name)) await fs.unlink(path.join(root, "objects", name));
  }
}
