import fs from "node:fs";
import path from "node:path";

/**
 * The image name the Host runs under.
 *
 * The bundled runtime is a frozen `python.exe`, so the Host showed up in every process list as "python.exe".
 * In a real long goal the agent saw two idle-looking python processes while its own experiments were hogging
 * the CPU and ran `taskkill /F /IM python.exe` — twice — which ended the Host, the goal's scheduler with it.
 * A command that kills "python" must not be able to take the Host along, so the Host starts from a
 * hard link of that executable under its own name. The frozen launcher finds `_internal` next to
 * itself, so the link works wherever the original does. `python.exe` stays: the runtime's tools and the
 * updater still address it by that name.
 */
export const HOST_IMAGE = "loom-host.exe";

export function hostLaunchExecutable(python: string, platform: NodeJS.Platform = process.platform): string {
  if (platform !== "win32" || path.basename(python).toLowerCase() !== "python.exe") return python;
  const directory = path.dirname(python);
  // A development virtualenv's python is not a frozen launcher: copying it would break its pyvenv.cfg lookup.
  if (!fs.existsSync(path.join(directory, "_internal"))) return python;
  const image = path.join(directory, HOST_IMAGE);
  try {
    const source = fs.statSync(python);
    let current = false;
    try {
      const existing = fs.statSync(image);
      current = existing.size === source.size && existing.mtimeMs >= source.mtimeMs - 1000;
    } catch { /* absent */ }
    if (!current) {
      fs.rmSync(image, { force: true });
      try { fs.linkSync(python, image); } catch { fs.copyFileSync(python, image); }
    }
    return image;
  } catch {
    // Read-only install location, antivirus holding the file: the Host still starts, under the old name.
    return python;
  }
}
