import { app } from "electron";
import path from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { selectApplication, rejectApplicationAndRestart } from "./applicationLoader.js";

// Stable installed bootstrap. Business code, preloads and UI form one release.
const installed = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const hostRoot = path.resolve(process.env.LOOM_HOST_DATA_DIR || app.getPath("userData"));
process.env.LOOM_HOST_DATA_DIR = hostRoot;
const isHost = process.argv.includes("--loom-host") || process.argv.includes("--loom-background-host")
  || process.argv.some(arg => /^loom:\/\//i.test(arg)) || existsSync(path.join(hostRoot, "loom-headless-update-restart"));
app.setName("Loom");
app.setPath("userData", isHost ? hostRoot : path.join(hostRoot, "desktop-ui"));
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  try {
    const entry = await selectApplication(installed, hostRoot, isHost);
    await import(pathToFileURL(entry).href);
  } catch (error) {
    console.error("Loom application loading failed", error);
    await rejectApplicationAndRestart(isHost);
  }
}
