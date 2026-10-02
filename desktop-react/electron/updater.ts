import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import electronUpdater, {
  type AppUpdater,
  type ProgressInfo,
  type UpdateInfo,
} from "electron-updater";
import { broadcastHostEvent, handleHostChannel, isHostProcess } from "./hostRuntime.js";

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
const STATUS_CHANNEL = "loom:update-status-changed";
const RELEASE_BASE_URL = "https://github.com/yuchenm1303-png/Loom/releases/tag";

function getAutoUpdater(): AppUpdater {
  // electron-updater is CommonJS. Destructuring the default import keeps the
  // NodeNext/ESM build compatible with the package's CJS export shape.
  const { autoUpdater } = electronUpdater;
  return autoUpdater;
}

const autoUpdater = getAutoUpdater();
const updateEnabled = isHostProcess && app.isPackaged && process.platform === "win32";
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
const earlyRecheckTimers: NodeJS.Timeout[] = [];

function publicState(): SoftwareUpdateState {
  return { ...state };
}

function broadcastState(): void {
  const payload = publicState();
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

function clearErrorRetry(): void {
  if (!errorRetryTimer) return;
  clearTimeout(errorRetryTimer);
  errorRetryTimer = null;
}

function scheduleErrorRetry(): void {
  if (!updateEnabled || errorRetryTimer || updateInFlight()) return;
  errorRetryTimer = setTimeout(() => {
    errorRetryTimer = null;
    void checkForUpdates();
  }, ERROR_RETRY_DELAY_MS);
  errorRetryTimer.unref?.();
}

async function offerAvailableUpdate(info: UpdateInfo): Promise<void> {
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

  if (result.response === 0 && state.phase === "downloaded") {
    restartForUpdate();
  }
}

async function checkForUpdates(): Promise<SoftwareUpdateState> {
  if (!updateEnabled) return publicState();
  if (checkPromise) return checkPromise;
  if (updateInFlight()) return publicState();

  clearErrorRetry();
  lastCheckStartedAt = Date.now();
  checkPromise = (async () => {
    setState({
      phase: "checking",
      error: undefined,
      checkedAt: new Date().toISOString(),
      percent: undefined,
      transferred: undefined,
      total: undefined,
      bytesPerSecond: undefined,
    });

    try {
      await autoUpdater.checkForUpdates();
    } catch (error) {
      setState({ phase: "error", error: errorText(error) });
      scheduleErrorRetry();
    }

    return publicState();
  })().finally(() => {
    checkPromise = null;
  });

  return checkPromise;
}

function installDownloadedUpdate(): { accepted: boolean; state: SoftwareUpdateState } {
  if (!updateEnabled || state.phase !== "downloaded") {
    return { accepted: false, state: publicState() };
  }

  restartForUpdate();
  return { accepted: true, state: publicState() };
}

function maybeCheckAfterFocus(): void {
  if (!updateEnabled || !lastCheckStartedAt || checkPromise || updateInFlight()) return;
  if (Date.now() - lastCheckStartedAt < FOCUS_RECHECK_MIN_AGE_MS) return;
  void checkForUpdates();
}

function restartForUpdate(): void {
  // Close desktop clients before the installer replaces the shared executable.
  broadcastHostEvent("loom:host-updating", null);
  setTimeout(() => autoUpdater.quitAndInstall(false, true), 500);
}

function configureUpdater(): void {
  if (!updateEnabled) return;

  autoUpdater.autoDownload = true;
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
    availablePromptPromise = offerAvailableUpdate(info).finally(() => {
      availablePromptPromise = null;
    });
  });

  autoUpdater.on("update-not-available", (info) => {
    clearErrorRetry();
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
    void offerDownloadedUpdate(info);
  });

  autoUpdater.on("error", (error) => {
    setState({ phase: "error", error: errorText(error) });
    scheduleErrorRetry();
  });
}

function startAutomaticChecks(): void {
  if (!updateEnabled || startupTimer || periodicTimer) return;

  startupTimer = setTimeout(() => {
    startupTimer = null;
    void checkForUpdates();
  }, STARTUP_CHECK_DELAY_MS);
  startupTimer.unref?.();

  for (const delay of EARLY_RECHECK_DELAYS_MS) {
    const timer = setTimeout(() => {
      const index = earlyRecheckTimers.indexOf(timer);
      if (index >= 0) earlyRecheckTimers.splice(index, 1);
      if (!updateInFlight()) void checkForUpdates();
    }, delay);
    timer.unref?.();
    earlyRecheckTimers.push(timer);
  }

  periodicTimer = setInterval(() => {
    if (!updateInFlight()) void checkForUpdates();
  }, PERIODIC_CHECK_INTERVAL_MS);
  periodicTimer.unref?.();
}

configureUpdater();

handleHostChannel("loom:update-status", () => publicState());
handleHostChannel("loom:update-check", () => checkForUpdates());
handleHostChannel("loom:update-install", () => installDownloadedUpdate());

app.on("browser-window-created", (_event, window) => {
  window.webContents.once("did-finish-load", () => {
    if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
      if (isHostProcess) window.webContents.send(STATUS_CHANNEL, publicState());
    }
  });
  window.on("focus", maybeCheckAfterFocus);
});

app.whenReady().then(() => {
  startAutomaticChecks();
});

app.on("before-quit", () => {
  if (startupTimer) clearTimeout(startupTimer);
  if (periodicTimer) clearInterval(periodicTimer);
  if (errorRetryTimer) clearTimeout(errorRetryTimer);
  for (const timer of earlyRecheckTimers) clearTimeout(timer);
  earlyRecheckTimers.length = 0;
  startupTimer = null;
  periodicTimer = null;
  errorRetryTimer = null;
});
