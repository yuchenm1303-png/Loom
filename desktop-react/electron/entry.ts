import { app, protocol } from "electron";
import path from "node:path";
import { existsSync, mkdirSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { hostDataPath } from "./hostPaths.js";

// Scheme privileges must be registered before ready, including when update
// selection waits for the OS credential store to become available.
protocol.registerSchemesAsPrivileged([
  { scheme: "loom-artifact", privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

// One installed release owns the main process, preload, UI and runtime.
const installed = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const hostRoot = hostDataPath;
process.env.LOOM_HOST_DATA_DIR = hostRoot;
const isHost = process.argv.includes("--loom-host") || process.argv.includes("--loom-background-host")
  || process.argv.some(arg => /^loom:\/\//i.test(arg)) || existsSync(path.join(hostRoot, "loom-headless-update-restart"));
app.setName("Loom");
// Electron app.setPath requires the target folder to exist. On a fresh x86
// install its independent Loom-x86 data directory is not created yet, so the
// first Host launch would otherwise quit before publishing localhost status.
const profile = isHost ? hostRoot : path.join(hostRoot, "desktop-ui");
mkdirSync(profile, { recursive: true });
app.setPath("userData", profile);
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // Let Electron emit ready while the installed application initializes.
  void loadApplication();
}

async function loadApplication() {
  try {
    await import(pathToFileURL(path.join(installed, "dist-electron/applicationEntry.js")).href);
  } catch (error) {
    console.error("Loom application loading failed", error);
    app.quit();
  }
}
