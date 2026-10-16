import { readUpdatePreferences, writeUpdatePreferences } from "./updatePreferences.js";
import { mayReceiveUpdates } from "./updateEligibility.js";
import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import fs from "node:fs";
import path from "node:path";
import electronUpdater, {
  type AppUpdater,
  type ProgressInfo,
  type UpdateInfo,
} from "electron-updater";
import { broadcastHostEvent, callHost, handleHostChannel, hostHasDesktopClients, isHostProcess } from "./hostProcess.js";
import { OptimizedNsisUpdater } from "./optimizedUpdater.js";
import { configuredUpdateSource } from "./updateSource.js";

export type SoftwareUpdatePhase =
  | "disabled"
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "downloaded"
  | "up-to-date"
  | "error";

export interface SoftwareUpdateState {
  enabled: boolean;
  accountUpdatesAllowed?: boolean;
  automaticUpdates?: boolean;
  silentUpdates?: boolean;
  autoRestartAfterUpdate?: boolean;
  phase: SoftwareUpdatePhase;
  currentVersion: string;
  availableVersion?: string;
  releaseName?: string | null;
  releaseDate?: string;
  percent?: number;
  transferred?: number;
  total?: number;
  bytesPerSecond?: number;
  checkedAt?: string;
  error?: string;
}

// Check quickly after launch, then re-check during the first few minutes so a
// release published just after Loom starts is still discovered promptly. After
// that, keep a modest background cadence and opportunistically refresh when the
// user returns to Loom after being away.
const STARTUP_CHECK_DELAY_MS = 5_000;
const EARLY_RECHECK_DELAYS_MS = [2 * 60_000, 10 * 60_000];
const PERIODIC_CHECK_INTERVAL_MS = 30 * 60_000;
const FOCUS_RECHECK_MIN_AGE_MS = 5 * 60_000;
const ERROR_RETRY_DELAY_MS = 60_000;
const HEADLESS_INSTALL_RETRY_MS = 30_000;
const STATUS_CHANNEL = "loom:update-status-changed";
const RELEASE_BASE_URL = "https://github.com/yuchenm1303-png/Loom/releases/tag";
const HEADLESS_RESTART_MARKER = "loom-headless-update-restart";

function getAutoUpdater(): AppUpdater {
  if (process.platform === "win32") return new OptimizedNsisUpdater();
  // electron-updater is CommonJS. Destructuring the default import keeps the
  // NodeNext/ESM build compatible with the package's CJS export shape.
  const { autoUpdater } = electronUpdater;
  return autoUpdater;
}

const autoUpdater = getAutoUpdater();
const updateSource = configuredUpdateSource();
let sourceFailed = false;
const githubFeed = { provider: "github" as const, owner: "yuchenm1303-png", repo: "Loom" };
const updateEnabled = isHostProcess && app.isPackaged && process.platform === "win32"
  && process.env.LOOM_DISABLE_AUTO_UPDATES !== "1";
let state: SoftwareUpdateState = {
  enabled: updateEnabled,
  phase: updateEnabled ? "idle" : "disabled",
  currentVersion: app.getVersion(),
};
let checkPromise: Promise<SoftwareUpdateState> | null = null;
let availablePromptPromise: Promise<void> | null = null;
let announcedVersion: string | null = null;
let promptedDownloadedVersion: string | null = null;
let lastCheckStartedAt = 0;
let startupTimer: NodeJS.Timeout | null = null;
let periodicTimer: NodeJS.Timeout | null = null;
let errorRetryTimer: NodeJS.Timeout | null = null;
let headlessInstallGuard: (() => boolean | Promise<boolean>) | null = null;
let headlessInstallRetryTimer: NodeJS.Timeout | null = null;
let headlessInstallAttempt: Promise<boolean> | null = null;
let bootstrapAutoInstallRequested = false;
let bootstrapRestarting = false;
const earlyRecheckTimers: NodeJS.Timeout[] = [];

export function softwareUpdateState(): SoftwareUpdateState {
  // Keep manual checks available after a transient eligibility failure.
  // Download/install still perform fresh server-side eligibility checks.
  return { ...state, ...readUpdatePreferences(), enabled: updateEnabled, phase: state.accountUpdatesAllowed === false ? "disabled" : state.phase };
}

function broadcastState(): void {
  const payload = softwareUpdateState();
  broadcastHostEvent(STATUS_CHANNEL, payload);
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
      window.webContents.send(STATUS_CHANNEL, payload);
    }
  }
}

function setState(patch: Partial<SoftwareUpdateState>): void {
  state = {
    ...state,
    ...patch,
    enabled: updateEnabled,
    currentVersion: app.getVersion(),
  };
  broadcastState();
}

function updateInfoPatch(info: UpdateInfo): Partial<SoftwareUpdateState> {
  return {
    availableVersion: info.version,
    releaseName: info.releaseName ?? null,
    releaseDate: info.releaseDate,
  };
}

function progressPatch(progress: ProgressInfo): Partial<SoftwareUpdateState> {
  return {
    percent: Number.isFinite(progress.percent) ? Math.max(0, Math.min(100, progress.percent)) : undefined,
    transferred: progress.transferred,
    total: progress.total,
    bytesPerSecond: progress.bytesPerSecond,
  };
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message || error.name;
  return String(error || "Unknown update error");
}

function updateInFlight(): boolean {
  return state.phase === "available" || state.phase === "downloading" || state.phase === "downloaded";
}

function activeWindow(): BrowserWindow | undefined {
  if (isHostProcess) return undefined;
  return BrowserWindow.getFocusedWindow()
    ?? BrowserWindow.getAllWindows().find((window) => !window.isDestroyed());
}

function hasVisibleWindow(): boolean {
  if (isHostProcess) return hostHasDesktopClients();
  return BrowserWindow.getAllWindows().some((window) => !window.isDestroyed() && window.isVisible());
}

function headlessInstallWanted(): boolean {
  const preferences = readUpdatePreferences();
  if (!preferences.automaticUpdates || !preferences.autoRestartAfterUpdate) return false;
  return state.phase === "downloaded";
}

function headlessRestartMarkerPath(): string {
  return path.join(app.getPath("userData"), HEADLESS_RESTART_MARKER);
}

function markHeadlessUpdateRestart(): void {
  try {
    fs.writeFileSync(headlessRestartMarkerPath(), "background\n", { encoding: "utf8", mode: 0o600 });
  } catch (error) {
    console.warn("Could not persist Loom Host restart mode", error);
  }
}

export function consumeHeadlessUpdateRestart(): boolean {
  try {
    const marker = headlessRestartMarkerPath();
    if (!fs.existsSync(marker)) return false;
    fs.unlinkSync(marker);
    return true;
  } catch {
    return false;
  }
}

function scheduleHeadlessInstallRetry(): void {
  if (headlessInstallRetryTimer || !headlessInstallWanted()) return;
  headlessInstallRetryTimer = setTimeout(() => {
    headlessInstallRetryTimer = null;
    void maybeInstallHeadless();
  }, HEADLESS_INSTALL_RETRY_MS);
  headlessInstallRetryTimer.unref?.();
}

async function maybeInstallHeadless(): Promise<boolean> {
  if (headlessInstallAttempt) return headlessInstallAttempt;
  if (!headlessInstallWanted()) return false;
  headlessInstallAttempt = (async () => {
    if (!await mayReceiveUpdates()) return false;
    if (!headlessInstallGuard) {
      scheduleHeadlessInstallRetry();
      return false;
    }
    {
      let safe = false;
      try { safe = await headlessInstallGuard(); } catch { safe = false; }
      if (!safe) {
        scheduleHeadlessInstallRetry();
        return false;
      }
    }
    if (headlessInstallRetryTimer) {
      clearTimeout(headlessInstallRetryTimer);
      headlessInstallRetryTimer = null;
    }
    if (!await mayReceiveUpdates()) return false;
    // Account verification can be slow; task state may have changed while it
    // was in flight. Recheck immediately before closing desktop clients.
    try {
      if (!await headlessInstallGuard()) { scheduleHeadlessInstallRetry(); return false; }
    } catch { scheduleHeadlessInstallRetry(); return false; }
    // Silent updates install whenever Agent work is idle, including with a
    // connected Desktop. The restart marker reopens the Desktop afterwards.
    return restartForUpdate();
  })().finally(() => {
    headlessInstallAttempt = null;
  });
  return headlessInstallAttempt;
}

export function registerHeadlessUpdateGuard(guard: () => boolean | Promise<boolean>): void {
  headlessInstallGuard = guard;
}

export async function ensureBootstrapUpdate(): Promise<SoftwareUpdateState> {
  if (!updateEnabled) return softwareUpdateState();
  bootstrapAutoInstallRequested = true;
  if (state.phase === "downloaded") {
    void maybeInstallHeadless();
    return softwareUpdateState();
  }
  const next = await checkForUpdates();
  void maybeInstallHeadless();
  return next;
}

// Backwards-compatible export. Runtime and UI changes use this same installer.
export const ensureWebHostUpdate = ensureBootstrapUpdate;

function clearErrorRetry(): void {
  if (!errorRetryTimer) return;
  clearTimeout(errorRetryTimer);
  errorRetryTimer = null;
}

function scheduleErrorRetry(): void {
  if (!readUpdatePreferences().automaticUpdates || !updateEnabled || errorRetryTimer || updateInFlight()) return;
  errorRetryTimer = setTimeout(() => {
    errorRetryTimer = null;
    if (readUpdatePreferences().automaticUpdates) void checkForUpdates();
  }, ERROR_RETRY_DELAY_MS);
  errorRetryTimer.unref?.();
}

async function offerAvailableUpdate(info: UpdateInfo): Promise<void> {
  if (!await mayReceiveUpdates() || readUpdatePreferences().silentUpdates) return;
  if (!updateEnabled || announcedVersion === info.version) return;
  announcedVersion = info.version;

  const options = {
    type: "info" as const,
    title: "Loom update available",
    message: `Loom ${info.version} is available.`,
    detail: "Loom is downloading the update in the background. You can keep working, and Loom will ask again when it is ready to install.",
    buttons: ["Download in background", "View release"],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
  };

  const parent = activeWindow();
  const result = parent
    ? await dialog.showMessageBox(parent, options)
    : await dialog.showMessageBox(options);

  if (result.response === 1) {
    await shell.openExternal(`${RELEASE_BASE_URL}/v${encodeURIComponent(info.version)}`);
  }
}

async function offerDownloadedUpdate(info: UpdateInfo): Promise<void> {
  if (!await mayReceiveUpdates() || readUpdatePreferences().silentUpdates) return;
  if (!updateEnabled || promptedDownloadedVersion === info.version) return;
  promptedDownloadedVersion = info.version;

  // Never stack the install prompt on top of the just-discovered prompt. If the
  // update is tiny or the connection is fast, wait until the first dialog is
  // dismissed before asking whether to restart.
  if (availablePromptPromise) {
    await availablePromptPromise.catch(() => undefined);
  }

  const options = {
    type: "info" as const,
    title: "Loom update ready",
    message: `Loom ${info.version} is ready to install.`,
    detail: "Restart Loom now to finish the update, or choose Later to keep working. The downloaded update will stay ready.",
    buttons: ["Restart and update", "Later"],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  };

  const parent = activeWindow();
  const result = parent
    ? await dialog.showMessageBox(parent, options)
    : await dialog.showMessageBox(options);

  if (result.response === 0 && state.phase === "downloaded" && await mayReceiveUpdates()) {
    await installDownloadedUpdate();
  }
}

export async function checkForUpdates(): Promise<SoftwareUpdateState> {
  if (!updateEnabled) return softwareUpdateState();
  if (checkPromise) return checkPromise;
  if (bootstrapRestarting || (updateInFlight() && state.accountUpdatesAllowed !== false)) return softwareUpdateState();
  // Cover eligibility and both channels with one operation. Repeated clicks,
  // startup and focus checks must not race or schedule competing upgrades.
  checkPromise = (async () => {
    if (!updateInFlight()) setState({ phase: "checking", error: undefined });
    if (!await mayReceiveUpdates()) {
      setState({ accountUpdatesAllowed: false, error: undefined });
      scheduleErrorRetry();
      return softwareUpdateState();
    }
    if (state.accountUpdatesAllowed !== true) {
      setState({ accountUpdatesAllowed: true, phase: state.phase === "disabled" ? "idle" : state.phase });
    }
    if (updateInFlight()) return softwareUpdateState();

    clearErrorRetry();
    lastCheckStartedAt = Date.now();
    setState({
      phase: "checking",
      error: undefined,
      checkedAt: new Date().toISOString(),
      percent: undefined,
      transferred: undefined,
      total: undefined,
      bytesPerSecond: undefined,
    });

    const tryingMirror = Boolean(updateSource && !sourceFailed);
    try {
      if (updateSource) autoUpdater.setFeedURL(sourceFailed ? githubFeed : {
        provider: "generic", url: `${updateSource}/installer/`, useMultipleRangeRequest: false,
      });
      await autoUpdater.checkForUpdates();
    } catch (error) {
      if (tryingMirror) {
        sourceFailed = true;
        autoUpdater.setFeedURL(githubFeed);
        try { await autoUpdater.checkForUpdates(); }
        catch (fallbackError) { setState({ phase: "error", error: errorText(fallbackError) }); scheduleErrorRetry(); }
      } else {
        setState({ phase: "error", error: errorText(error) });
        scheduleErrorRetry();
      }
    }


    return softwareUpdateState();
  })().finally(() => {
    checkPromise = null;
  });

  return checkPromise;
}

export async function installDownloadedUpdate(): Promise<{ accepted: boolean; state: SoftwareUpdateState }> {
  if (!await mayReceiveUpdates()) {
    setState({ accountUpdatesAllowed: false });
    return { accepted: false, state: softwareUpdateState() };
  }
  if (state.accountUpdatesAllowed !== true) setState({ accountUpdatesAllowed: true });
  if (!updateEnabled || state.phase !== "downloaded") {
    return { accepted: false, state: softwareUpdateState() };
  }

  // Manual installation must protect active Agent work just like automatic
  // installation. Keep the verified download available for a later retry.
  try {
    if (!headlessInstallGuard || !await headlessInstallGuard()) {
      setState({ error: "Finish or stop running tasks before restarting to install this update." });
      return { accepted: false, state: softwareUpdateState() };
    }
  } catch {
    setState({ error: "Could not confirm that running tasks are idle. Try installing again shortly." });
    return { accepted: false, state: softwareUpdateState() };
  }
  if (!await mayReceiveUpdates()) return { accepted: false, state: softwareUpdateState() };
  try {
    if (!await headlessInstallGuard()) {
      setState({ error: "Finish or stop running tasks before restarting to install this update." });
      return { accepted: false, state: softwareUpdateState() };
    }
  } catch {
    setState({ error: "Could not confirm that running tasks are idle. Try installing again shortly." });
    return { accepted: false, state: softwareUpdateState() };
  }
  return { accepted: restartForUpdate(), state: softwareUpdateState() };
}

function maybeCheckAfterFocus(): void {
  if (!readUpdatePreferences().automaticUpdates || !updateEnabled || !lastCheckStartedAt || checkPromise || updateInFlight()) return;
  if (Date.now() - lastCheckStartedAt < FOCUS_RECHECK_MIN_AGE_MS) return;
  void checkForUpdates();
}

function restartForUpdate(): boolean {
  if (bootstrapRestarting) return false;
  bootstrapRestarting = true;
  markHeadlessUpdateRestart();
  // Close desktop clients before the installer replaces the shared executable.
  broadcastHostEvent("loom:host-updating", null);
  setTimeout(() => autoUpdater.quitAndInstall(false, true), 500);
  return true;
}

function configureUpdater(): void {
  if (!updateEnabled) return;

  autoUpdater.autoDownload = true;
  // Install only after a fresh per-account eligibility check, never merely on process quit.
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.allowPrerelease = false;
  autoUpdater.autoRunAppAfterInstall = true;

  autoUpdater.on("checking-for-update", () => {
    setState({ phase: "checking", error: undefined });
  });

  autoUpdater.on("update-available", (info) => {
    clearErrorRetry();
    setState({
      phase: "available",
      ...updateInfoPatch(info),
      error: undefined,
      percent: 0,
      transferred: 0,
    });
    if (hasVisibleWindow()) {
      availablePromptPromise = offerAvailableUpdate(info).finally(() => {
        availablePromptPromise = null;
      });
    }
  });

  autoUpdater.on("update-not-available", (info) => {
    clearErrorRetry();
    bootstrapAutoInstallRequested = false;
    setState({
      phase: "up-to-date",
      ...updateInfoPatch(info),
      availableVersion: undefined,
      percent: undefined,
      transferred: undefined,
      total: undefined,
      bytesPerSecond: undefined,
      error: undefined,
    });
  });

  autoUpdater.on("download-progress", (progress) => {
    setState({
      phase: "downloading",
      ...progressPatch(progress),
      error: undefined,
    });
  });

  autoUpdater.on("update-downloaded", (info) => {
    setState({
      phase: "downloaded",
      ...updateInfoPatch(info),
      percent: 100,
      error: undefined,
    });
    void maybeInstallHeadless().then((accepted) => {
      if (!accepted && !headlessInstallWanted()) void offerDownloadedUpdate(info);
    });
  });

  autoUpdater.on("error", (error) => {
    // Includes download failures after discovery. The next scheduled check
    // re-resolves the release through GitHub rather than retrying a dead CDN.
    if (updateSource) sourceFailed = true;
    setState({ phase: "error", error: errorText(error) });
    scheduleErrorRetry();
  });
}

function runAutomaticCheck(): void {
  if (!readUpdatePreferences().automaticUpdates) return;
  if (!updateEnabled || updateInFlight()) return;
  if (hasVisibleWindow()) {
    void checkForUpdates();
    return;
  }
  // Background Hosts use the same installer and idle-task guard as Desktop.
  if (isHostProcess) void ensureBootstrapUpdate();
}

function startAutomaticChecks(): void {
  if (!updateEnabled || startupTimer || periodicTimer) return;

  startupTimer = setTimeout(() => {
    startupTimer = null;
    runAutomaticCheck();
  }, STARTUP_CHECK_DELAY_MS);
  startupTimer.unref?.();

  for (const delay of EARLY_RECHECK_DELAYS_MS) {
    const timer = setTimeout(() => {
      const index = earlyRecheckTimers.indexOf(timer);
      if (index >= 0) earlyRecheckTimers.splice(index, 1);
      runAutomaticCheck();
    }, delay);
    timer.unref?.();
    earlyRecheckTimers.push(timer);
  }

  periodicTimer = setInterval(runAutomaticCheck, PERIODIC_CHECK_INTERVAL_MS);
  periodicTimer.unref?.();
}

configureUpdater();

handleHostChannel("loom:update-preferences", (_event: unknown, patch: unknown) => {
  writeUpdatePreferences(patch);
  // Install only after a fresh per-account eligibility check, never merely on process quit.
  autoUpdater.autoInstallOnAppQuit = false;
  broadcastState();
  return softwareUpdateState();
});
handleHostChannel("loom:update-status", () => softwareUpdateState());
handleHostChannel("loom:update-check", () => checkForUpdates());
handleHostChannel("loom:update-install", () => installDownloadedUpdate());
handleHostChannel("loom:desktop-activity", () => {
  startAutomaticChecks();
  maybeCheckAfterFocus();
  return softwareUpdateState();
});

app.on("browser-window-created", (_event, window) => {
  const notifyHost = () => { void callHost("loom:desktop-activity").catch(() => undefined); };
  if (!isHostProcess) notifyHost();
  window.webContents.once("did-finish-load", () => {
    if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
      if (isHostProcess) window.webContents.send(STATUS_CHANNEL, softwareUpdateState());
    }
  });
  if (!isHostProcess) window.on("focus", notifyHost);
  window.on("hide", () => { void maybeInstallHeadless(); });
});

app.whenReady().then(() => {
  // Always arm bootstrap checks in the packaged Host. Interactive Hosts prompt
  // normally; web-only Hosts download in the background and install only when
  // the restart-safety guard says Agent work is idle.
  startAutomaticChecks();
});

app.on("before-quit", () => {
  if (startupTimer) clearTimeout(startupTimer);
  if (periodicTimer) clearInterval(periodicTimer);
  if (errorRetryTimer) clearTimeout(errorRetryTimer);
  if (headlessInstallRetryTimer) clearTimeout(headlessInstallRetryTimer);
  for (const timer of earlyRecheckTimers) clearTimeout(timer);
  earlyRecheckTimers.length = 0;
  startupTimer = null;
  periodicTimer = null;
  errorRetryTimer = null;
  headlessInstallRetryTimer = null;
});
