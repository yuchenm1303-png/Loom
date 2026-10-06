import fs from "node:fs/promises";
import path from "node:path";

async function assetsMatch(source: string, target: string): Promise<boolean> {
  for (const entry of await fs.readdir(source, { withFileTypes: true })) {
    const src = path.join(source, entry.name);
    const dst = path.join(target, entry.name);
    if (entry.isDirectory()) {
      if (!await assetsMatch(src, dst)) return false;
    } else if (entry.isFile()) {
      try { if (!(await fs.readFile(src)).equals(await fs.readFile(dst))) return false; }
      catch { return false; }
    }
  }
  return true;
}

export async function syncExtensionInstall(source: string, target: string, bridgeConfig: string, updateSignal: string): Promise<boolean> {
  let unchanged = await assetsMatch(source, target);
  try { unchanged = unchanged && await fs.readFile(path.join(target, "bridge-config.json"), "utf8") === bridgeConfig; }
  catch { unchanged = false; }
  if (unchanged) return false;
  await fs.mkdir(target, { recursive: true });
  await fs.cp(source, target, { recursive: true, force: true });
  await fs.writeFile(path.join(target, "bridge-config.json"), bridgeConfig, { encoding: "utf8", mode: 0o600 });
  // The watcher must not reload until all code and pairing files are installed.
  await fs.writeFile(path.join(target, "extension-update.json"), updateSignal, { encoding: "utf8", mode: 0o600 });
  return true;
}
