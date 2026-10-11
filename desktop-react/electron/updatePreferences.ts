import fs from "node:fs";
import path from "node:path";
import { hostDataPath } from "./hostPaths.js";
export interface UpdatePreferences { automaticUpdates: boolean; silentUpdates: boolean }
const file = path.join(hostDataPath, "update-preferences.json");
export function readUpdatePreferences(): UpdatePreferences {
  try {
    const value = JSON.parse(fs.readFileSync(file, "utf8"));
    return { automaticUpdates: value.automaticUpdates !== false, silentUpdates: value.silentUpdates !== false };
  } catch { return { automaticUpdates: true, silentUpdates: true }; }
}
export function writeUpdatePreferences(patch: unknown): UpdatePreferences {
  if (!patch || typeof patch !== "object" || Object.entries(patch).some(([key, value]) =>
    !["automaticUpdates", "silentUpdates"].includes(key) || typeof value !== "boolean")) throw new Error("Invalid update preferences");
  const next = { ...readUpdatePreferences(), ...patch };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = file + ".tmp";
  fs.writeFileSync(temporary, JSON.stringify(next));
  fs.renameSync(temporary, file);
  return next;
}
