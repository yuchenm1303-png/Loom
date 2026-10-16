import { app, protocol } from "electron";
import path from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { selectApplication, rejectApplicationAndRestart } from "./applicationLoader.js";
import { hostDataPath } from "./hostPaths.js";

// Scheme privileges must be registered before ready, including when update
// selection waits for the OS credential store to become available.
protocol.registerSchemesAsPrivileged([
  { scheme: "loom-artifact", privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

// Stable installed bootstrap. Business code, preloads and UI form one release.
const installed = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// The loader imports hostProcess, which already moves Desktop's userData into
// desktop-ui. Use the original profile captured before that import's effects.
const hostRoot = hostDataPath;
process.env.LOOM_HOST_DATA_DIR = hostRoot;
const isHost = process.argv.includes("--loom-host") || process.argv.includes("--loom-background-host")
  || process.argv.some(arg => /^loom:\/\//i.test(arg)) || existsSync(path.join(hostRoot, "loom-headless-update-restart"));
app.setName("Loom");
app.setPath("userData", isHost ? hostRoot : path.join(hostRoot, "desktop-ui"));
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // Electron waits for an ESM entry's top-level await before emitting ready.
  // Selection may need ready for credential decryption, so let the entry
  // finish evaluating while the guarded application load continues.
  void loadApplication();
}

async function loadApplication() {
  try {
    const entry = await selectApplication(installed, hostRoot, isHost);
    await import(pathToFileURL(entry).href);
  } catch (error) {
    console.error("Loom application loading failed", error);
    await rejectApplicationAndRestart(isHost);
  }
}
