import { app, BrowserWindow, dialog, ipcMain } from "electron";
import electronUpdater, {
  type AppUpdater,
  type ProgressInfo,
  type UpdateInfo,
} from "electron-updater";

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

const STARTUP_CHECK_DELAY_MS = 15_000;
const PERIODIC_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1_000;
const STATUS_CHANNEL = "loom:update-status-changed";

function getAutoUpdater(): AppUpdater {
  // electron-updater is CommonJS. Destructuring the default import keeps the
  // NodeNext/ESM build compatible with the package's CJS export shape.
  const { autoUpdater } = electronUpdater;
  return autoUpdater;
}

const autoUpdater = getAutoUpdater();
const updateEnabled = app.isPackaged && process.platform === "win32";
let state: SoftwareUpdateState = {
  enabled: updateEnabled,
  phase: updateEnabled ? "idle" : "disabled",
  currentVersion: app.getVersion(),
};
let checkPromise: Promise<SoftwareUpdateState> | null = null;
let promptedVersion: string | null = null;
let startupTimer: NodeJS.Timeout | null = null;
let periodicTimer: NodeJS.Timeout | null = null;

function publicState(): SoftwareUpdateState {
  return { ...state };
}

function broadcastState(): void {
  const payload = publicState();
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

async function offerDownloadedUpdate(info: UpdateInfo): Promise<void> {
  if (!updateEnabled || promptedVersion === info.version) return;
  promptedVersion = info.version;

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

  const parent = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows().find((window) => !window.isDestroyed());
  const result = parent
    ? await dialog.showMessageBox(parent, options)
    : await dialog.showMessageBox(options);

  if (result.response === 0 && state.phase === "downloaded") {
    autoUpdater.quitAndInstall(false, true);
  }
}

async function checkForUpdates(): Promise<SoftwareUpdateState> {
  if (!updateEnabled) return publicState();
  if (checkPromise) return checkPromise;

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

  autoUpdater.quitAndInstall(false, true);
  return { accepted: true, state: publicState() };
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
    setState({
      phase: "available",
      ...updateInfoPatch(info),
      error: undefined,
      percent: 0,
      transferred: 0,
    });
  });

  autoUpdater.on("update-not-available", (info) => {
    promptedVersion = null;
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
  });
}

function startAutomaticChecks(): void {
  if (!updateEnabled || startupTimer || periodicTimer) return;

  startupTimer = setTimeout(() => {
    startupTimer = null;
    void checkForUpdates();
  }, STARTUP_CHECK_DELAY_MS);
  startupTimer.unref?.();

  periodicTimer = setInterval(() => {
    void checkForUpdates();
  }, PERIODIC_CHECK_INTERVAL_MS);
  periodicTimer.unref?.();
}

configureUpdater();

ipcMain.handle("loom:update-status", () => publicState());
ipcMain.handle("loom:update-check", () => checkForUpdates());
ipcMain.handle("loom:update-install", () => installDownloadedUpdate());

app.on("browser-window-created", (_event, window) => {
  window.webContents.once("did-finish-load", () => {
    if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
      window.webContents.send(STATUS_CHANNEL, publicState());
    }
  });
});

app.whenReady().then(() => {
  startAutomaticChecks();
});

app.on("before-quit", () => {
  if (startupTimer) clearTimeout(startupTimer);
  if (periodicTimer) clearInterval(periodicTimer);
  startupTimer = null;
  periodicTimer = null;
});
