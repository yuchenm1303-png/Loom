import { app } from "electron";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { register } from "node:module";
import { pruneApplicationReleases, readApplicationPointer, readApplicationRelease, updateApplicationPointer, verifyApplicationRelease } from "./applicationUpdateStore.js";

export interface ApplicationBootstrapControl { build: number; root: string; downloading?: boolean; healthy(): Promise<void>; failed(): Promise<void> }
declare global { var loomApplicationBootstrap: ApplicationBootstrapControl | undefined; }
function alive(pid?: number) {
  if (!pid) return false;
  try { process.kill(pid, 0); return true; } catch { return false; }
}
export async function selectApplication(installed: string, hostRoot: string, isHost: boolean): Promise<string> {
  const root = path.join(hostRoot, "application-updates");
  if (!app.isPackaged || process.env.LOOM_DISABLE_AUTO_UPDATES === "1") return path.join(installed, "dist-electron/applicationEntry.js");
  let pointer = await readApplicationPointer(root);
  const staleCandidate = pointer.booting && !alive(pointer.booting.pid) ? pointer.booting.build : undefined;
  if (isHost && pointer.booting && !alive(pointer.booting.pid)) {
    const failed = pointer.booting.build;
    pointer = await updateApplicationPointer(root, current => ({ ...current,
      rejected: [...(current.rejected || []), failed].slice(-20),
      ready: current.ready === failed ? undefined : current.ready, booting: undefined }));
  }
  // Existing desktops and their Host must load the same code. A newly opened
  // window follows the running Host until the Host is deliberately restarted.
  const session = !isHost && alive(pointer.session?.pid) ? pointer.session : undefined;
  const candidate = session ? session.build : pointer.ready || pointer.active || 0;
  let directory = installed, build = 0;
  const baseline = await fs.readFile(path.join(installed, "dist/ui-baseline.json"), "utf8").then(JSON.parse).catch(() => null);
  const acceptable = (release: { sourceTime: number; sourceSha: string }) => !baseline || release.sourceTime > baseline.sourceTime
    || (release.sourceTime === baseline.sourceTime && release.sourceSha !== baseline.sourceSha);
  for (const id of [candidate, ...(!session && pointer.active !== candidate ? [pointer.active || 0] : [])]) {
    if (!id || id === staleCandidate || pointer.rejected?.includes(id)) continue;
    const target = path.join(root, String(id));
    const release = await readApplicationRelease(target, app.getVersion());
    if (release && acceptable(release)) {
      try { await verifyApplicationRelease(target, release); directory = target; build = id; break; } catch {}
    }
  }
  if (directory !== installed) {
    register(new URL("./applicationModuleResolver.js", import.meta.url), {
      parentURL: import.meta.url, data: { parentURL: pathToFileURL(path.join(installed, "dist-electron/entry.js")).href },
    });
  }
  if (isHost) {
    await updateApplicationPointer(root, current => ({ ...current,
      session: { build, pid: process.pid }, booting: build && build !== current.active ? { build, pid: process.pid } : undefined,
      ...(candidate && candidate !== build ? {
        ready: current.ready === candidate ? undefined : current.ready,
        rejected: [...(current.rejected || []), candidate].slice(-20),
      } : {}) }));
  }
  const watchdog = isHost && build ? setTimeout(() => { void rejectApplicationAndRestart(true); }, 60000) : undefined;
  watchdog?.unref();
  globalThis.loomApplicationBootstrap = {
    build, root,
    failed: () => rejectApplicationAndRestart(isHost),
    healthy: async () => {
      if (watchdog) clearTimeout(watchdog);
      if (!isHost) return;
      const committed = await updateApplicationPointer(root, current => ({ ...current,
        active: build || undefined, previous: build !== current.active ? current.active : current.previous,
        ready: current.ready === build ? undefined : current.ready, booting: undefined }));
      if (!globalThis.loomApplicationBootstrap?.downloading) {
        await pruneApplicationReleases(root, committed).catch(error => console.warn("Application cache cleanup failed", String(error)));
      }
    },
  };
  app.once("before-quit", () => { if (watchdog) clearTimeout(watchdog); });
  return path.join(directory, "dist-electron/applicationEntry.js");
}
export async function rejectApplicationAndRestart(isHost: boolean) {
  const control = globalThis.loomApplicationBootstrap;
  if (!isHost || !control?.build) { app.quit(); return; }
  await updateApplicationPointer(control.root, current => ({ ...current,
    rejected: [...(current.rejected || []), control.build].slice(-20),
    ready: current.ready === control.build ? undefined : current.ready,
    active: current.active === control.build ? current.previous : current.active,
    booting: undefined, session: undefined }));
  app.relaunch(); app.quit();
}
