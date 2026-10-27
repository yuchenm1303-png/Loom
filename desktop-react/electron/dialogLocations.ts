import { app } from "electron";
import fs from "node:fs";
import path from "node:path";

// Since Electron 43 an open dialog without defaultPath starts in Downloads and
// Windows no longer brings back the folder the user was last in, so Loom
// remembers that folder itself, per kind of dialog and across restarts.
export type DialogPurpose = "attach-files" | "project-folder";

let remembered: Partial<Record<DialogPurpose, string>> | null = null;

function locationsFile(): string {
  return path.join(app.getPath("userData"), "dialog-locations.json");
}

function locations(): Partial<Record<DialogPurpose, string>> {
  if (remembered) return remembered;
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(locationsFile(), "utf8"));
    remembered = parsed && typeof parsed === "object" ? parsed as Partial<Record<DialogPurpose, string>> : {};
  } catch {
    remembered = {};
  }
  return remembered;
}

export function lastDialogFolder(purpose: DialogPurpose): string | undefined {
  const folder = locations()[purpose];
  return typeof folder === "string" && folder && fs.existsSync(folder) ? folder : undefined;
}

/** Remember the folder that holds what was chosen, so its siblings are in view next time. */
export function rememberDialogChoice(purpose: DialogPurpose, chosen: string | undefined): void {
  if (!chosen) return;
  const all = locations();
  const folder = path.dirname(chosen);
  if (all[purpose] === folder) return;
  all[purpose] = folder;
  try {
    fs.writeFileSync(locationsFile(), JSON.stringify(all), { encoding: "utf8", mode: 0o600 });
  } catch {
    // Kept for this session; the next start falls back to Downloads.
  }
}
