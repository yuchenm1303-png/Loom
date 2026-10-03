import { app } from "electron";
import path from "node:path";

// Capture the original profile before Desktop selects its separate UI folder.
// Runtime path queries must not initialize IPC or process lifecycle handlers.
export const hostDataPath = process.env.LOOM_HOST_DATA_DIR
  ? path.resolve(process.env.LOOM_HOST_DATA_DIR) : app.getPath("userData");
