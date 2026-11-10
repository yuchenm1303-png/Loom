import { app, BrowserWindow, ipcMain } from "electron";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { installRendererContentSecurity, type RendererDocument } from "./contentSecurity.js";
import { fromLoomRenderer, sentByLoomRenderer, trustedRendererWindows, trustRendererDocument } from "./rendererIpc.js";
import { isHostProcess } from "./hostProcess.js";
import { mayReceiveUpdates } from "./updateEligibility.js";
import { readUpdatePreferences } from "./updatePreferences.js";
import { boundedResponse, UI_CHANNEL, UiUpdateStore, validateUiManifest, type UiManifest } from "./uiUpdateStore.js";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundledUrl = pathToFileURL(path.join(desktopRoot, "dist", "index.html")).href;
let contract = "";
try { contract = JSON.parse(fs.readFileSync(path.join(desktopRoot, "dist-electron", "ui-contract.json"), "utf8")).contract; } catch { /* Development and older builds use bundled UI. */ }
const enabled = app.isPackaged && !isHostProcess && process.platform === "win32" && process.arch === "x64"
  && /^[a-f0-9]{64}$/.test(contract) && process.env.LOOM_DISABLE_AUTO_UPDATES !== "1";
const store = new UiUpdateStore(path.join(app.getPath("userData"), "ui-updates", contract || "disabled"), app.getVersion(), contract);
if (enabled) {
  try { store.prune(); } catch (error) { console.warn("UI cache cleanup deferred", error); }
}
const trials = new Map<BrowserWindow, { build: number; notify: boolean; timer?: NodeJS.Timeout }>();
const notices = new WeakMap<BrowserWindow, { phase: "waiting" | "updated"; build: number }>();
const preparations = new Map<string, { window: BrowserWindow; resolve: (safe: boolean) => void }>();
let checking: Promise<void> | null = null;
let candidate: UiManifest | null = null;
let lastCheck = 0;
let interval: NodeJS.Timeout | undefined;
let startup: NodeJS.Timeout | undefined;
let retry: NodeJS.Timeout | undefined;
let offering = false;

export function currentUiDocument(fallback: RendererDocument): RendererDocument {
  if (!enabled || fallback.development) return fallback;
  const index = store.activeIndex();
  return index ? { url: pathToFileURL(index).href, development: false } : fallback;
}

function setDocument(window: BrowserWindow, url: string): void {
  trustRendererDocument(window, url);
  installRendererContentSecurity(window, { url, development: false });
}

/** Returns true when this module owns recovery, so the generic boot-error page stays out of its way. */
export function recoverUiWindow(window: BrowserWindow): boolean {
  const trial = trials.get(window);
  if (!trial) return false;
  if (trial.timer) clearTimeout(trial.timer);
  trials.delete(window);
  try { store.reject(trial.build); } catch (error) { console.warn("Could not persist UI rollback", error); }
  candidate = null;
  const hash = new URL(window.webContents.getURL() || bundledUrl).hash;
  setDocument(window, bundledUrl);
  void window.loadURL(bundledUrl + hash).catch(error => console.error("Bundled UI recovery failed", error));
  return true;
}

export function watchUiWindow(window: BrowserWindow, document: RendererDocument, notify = false): void {
  if (!enabled || document.development) return;
  const prefix = pathToFileURL(store.root + path.sep).href;
  if (!document.url.startsWith(prefix)) return;
  const build = Number(path.relative(store.root, fileURLToPath(document.url)).split(path.sep)[0]);
  const previous = trials.get(window);
  if (previous?.timer) clearTimeout(previous.timer);
  const trial = { build, notify, timer: setTimeout(() => { recoverUiWindow(window); }, 30_000) };
  trial.timer.unref?.();
  trials.set(window, trial);
  window.once("closed", () => {
    const pending = trials.get(window);
    if (pending?.timer) clearTimeout(pending.timer);
    trials.delete(window);
  });
}

function notify(window: BrowserWindow, phase: "waiting" | "updated", build: number): void {
  const status = { phase, build };
  notices.set(window, status);
  if (!window.isDestroyed()) window.webContents.send("loom:ui-update-status", status);
}

function prepare(window: BrowserWindow): Promise<boolean> {
  return new Promise(resolve => {
    const token = crypto.randomUUID();
    const timer = setTimeout(() => { preparations.delete(token); resolve(false); }, 2000);
    timer.unref?.();
    preparations.set(token, { window, resolve: safe => { clearTimeout(timer); preparations.delete(token); resolve(safe); } });
    window.webContents.send("loom:ui-update-prepare", token);
  });
}

async function applyAvailable(manual = false): Promise<void> {
  if (offering || !candidate || (!manual && !readUpdatePreferences().automaticUpdates)) return;
  const manifest = candidate;
  offering = true;
  try {
    store.verify(manifest.build);
    for (const window of trustedRendererWindows()) {
      if (window.isDestroyed() || trials.get(window)?.build === manifest.build) continue;
      if (!await mayReceiveUpdates() || candidate !== manifest || window.isDestroyed()) return;
      // Ask the preload immediately before navigating. Missing, busy or old renderers fail closed.
      if (!await prepare(window)) { notify(window, "waiting", manifest.build); continue; }
      if (window.isDestroyed() || candidate !== manifest) continue;
      if (!manual && !readUpdatePreferences().automaticUpdates) return;
      const url = pathToFileURL(path.join(store.directory(manifest.build), "dist", "index.html")).href;
      const hash = new URL(window.webContents.getURL()).hash;
      setDocument(window, url);
      watchUiWindow(window, { url, development: false }, true);
      await window.loadURL(url + hash).catch(error => { recoverUiWindow(window); console.warn("UI update rolled back", error); });
    }
  } finally { offering = false; }
}

export async function checkUiUpdates(manual = false): Promise<void> {
  if (!enabled || (!manual && !readUpdatePreferences().automaticUpdates)) return;
  if (checking) return checking;
  checking = (async () => {
    lastCheck = Date.now();
    if (!await mayReceiveUpdates()) return;
    const bytes = await boundedResponse(await fetch(UI_CHANNEL, { signal: AbortSignal.timeout(15_000), cache: "no-store" }), 1024 * 1024);
    const raw = JSON.parse(bytes.toString("utf8"));
    // Historical channels are intentionally ignored, never interpreted as compatible updates.
    if (raw.schema !== 2 || raw.bootstrap !== app.getVersion() || raw.contract !== contract) return;
    const manifest = validateUiManifest(raw, app.getVersion(), contract);
    if (store.accepts(manifest.build)) {
      const deadline = AbortSignal.timeout(5 * 60_000);
      await store.stage(manifest, async (url, size) => boundedResponse(
        await fetch(url, { signal: AbortSignal.any([deadline, AbortSignal.timeout(60_000)]) }), size));
      candidate = manifest;
    }
    if (candidate) await applyAvailable(manual);
  })().catch(error => { console.warn("UI update check failed; keeping current UI", error); }).finally(() => { checking = null; });
  return checking;
}

ipcMain.on("loom:renderer-healthy", event => {
  if (!enabled || !sentByLoomRenderer(event as Electron.IpcMainInvokeEvent)) return;
  const window = BrowserWindow.fromWebContents(event.sender);
  const trial = window && trials.get(window);
  if (!window || !trial) return;
  if (trial.timer) clearTimeout(trial.timer);
  trial.timer = undefined;
  try {
    store.confirm(trial.build);
    if (trial.notify) { trial.notify = false; notify(window, "updated", trial.build); }
  } catch (error) { recoverUiWindow(window); console.warn("UI confirmation failed", error); }
});
ipcMain.on("loom:ui-update-prepared", (event, token: unknown, safe: unknown) => {
  if (typeof token !== "string" || !sentByLoomRenderer(event as Electron.IpcMainInvokeEvent)) return;
  const pending = preparations.get(token);
  if (pending?.window.webContents === event.sender) pending.resolve(safe === true);
});
ipcMain.handle("loom:ui-update-status", fromLoomRenderer(event => {
  const window = BrowserWindow.fromWebContents(event.sender);
  return window ? notices.get(window) || null : null;
}));
ipcMain.handle("loom:ui-update-check", fromLoomRenderer(async () => {
  await checkUiUpdates(true);
}));
app.whenReady().then(() => {
  if (!enabled) return;
  startup = setTimeout(() => { void checkUiUpdates(); }, 10_000);
  startup.unref?.();
  interval = setInterval(() => { void checkUiUpdates(); }, 30 * 60_000);
  interval.unref?.();
  retry = setInterval(() => {
    if (readUpdatePreferences().automaticUpdates) void applyAvailable().catch(error => console.warn("UI update deferred", error));
  }, 15_000);
  retry.unref?.();
});
app.on("browser-window-focus", () => {
  if (!enabled) return;
  if (Date.now() - lastCheck > 5 * 60_000) void checkUiUpdates();
  else if (readUpdatePreferences().automaticUpdates) void applyAvailable().catch(error => console.warn("UI update deferred", error));
});
app.on("before-quit", () => {
  clearTimeout(startup); clearInterval(interval); clearInterval(retry);
  for (const trial of trials.values()) clearTimeout(trial.timer);
});
