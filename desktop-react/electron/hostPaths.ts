import { app } from "electron";
import path from "node:path";

// Capture the original profile before Desktop selects its separate UI folder.
// Runtime path queries must not initialize IPC or process lifecycle handlers.
// Electron may include the scoped package name with forward slashes in its
// default Windows profile. Normalize both sources before IPC hashing and before
// passing the directory to a child Host; they must use the identical string.
export const hostDataPath = path.resolve(process.env.LOOM_HOST_DATA_DIR || (
  // Keep the x86 local Host separate from the x64 Host when both app versions
  // are installed on a 64-bit PC. The same profile is inherited by child Hosts.
  process.platform === "win32" && process.arch === "ia32"
    ? path.join(app.getPath("appData"), "Loom-x86")
    : app.getPath("userData")
));
