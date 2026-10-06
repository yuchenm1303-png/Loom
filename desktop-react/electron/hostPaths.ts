import { app } from "electron";
import path from "node:path";

// Capture the original profile before Desktop selects its separate UI folder.
// Runtime path queries must not initialize IPC or process lifecycle handlers.
// Electron may include the scoped package name with forward slashes in its
// default Windows profile. Normalize both sources before IPC hashing and before
// passing the directory to a child Host; they must use the identical string.
export const hostDataPath = path.resolve(process.env.LOOM_HOST_DATA_DIR || app.getPath("userData"));
