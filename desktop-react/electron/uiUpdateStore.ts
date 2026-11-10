import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

export interface UiFile { path: string; size: number; sha256: string; url: string }
export interface UiManifest { schema: 2; build: number; bootstrap: string; contract: string; files: UiFile[] }
export const UI_CHANNEL = "https://raw.githubusercontent.com/yuchenm1303-png/Loom/main/ui-runtime/stable.json";
export const MAX_UI_BYTES = 64 * 1024 * 1024;
export const sha256 = (data: Buffer | string): string => crypto.createHash("sha256").update(data).digest("hex");

/** The channel is trusted like the installer feed; asset hosts and paths are never channel-controlled. */
export function validateUiManifest(value: unknown, bootstrap: string, contract: string): UiManifest {
  const m = value as UiManifest;
  if (!m || m.schema !== 2 || !Number.isSafeInteger(m.build) || m.build <= 0
    || m.bootstrap !== bootstrap || !/^[a-f0-9]{64}$/.test(m.contract) || m.contract !== contract
    || !Array.isArray(m.files) || !m.files.length || m.files.length > 1024) throw new Error("Incompatible UI update");
  const seen = new Set<string>();
  let total = 0;
  for (const file of m.files) {
    // Windows aliases, ADS, traversal, reserved names and case collisions must all be refused.
    if (!file || typeof file.path !== "string" || file.path.length > 220
      || !file.path.split("/").every(segment => /^[a-zA-Z0-9_@.-]+$/.test(segment) && segment !== "." && segment !== ".."
        && !/[. ]$/.test(segment) && !/^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(segment))
      || seen.has(file.path.toLowerCase()) || !Number.isSafeInteger(file.size) || file.size < 0
      || !/^[a-f0-9]{64}$/.test(file.sha256)
      || typeof file.url !== "string"
      || !/^https:\/\/github\.com\/yuchenm1303-png\/Loom\/releases\/download\/[\w.-]+\/[a-f0-9]{64}$/.test(file.url)
      || !file.url.endsWith(`/${file.sha256}`)) throw new Error("Invalid UI asset");
    seen.add(file.path.toLowerCase());
    total += file.size;
    if (total > MAX_UI_BYTES) throw new Error("UI update exceeds size limit");
  }
  if (!m.files.some(file => file.path === "index.html")) throw new Error("UI entry missing");
  return m;
}

export async function boundedResponse(response: Response, limit: number): Promise<Buffer> {
  if (!response.ok || !response.body) throw new Error(`UI download failed (${response.status})`);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.length;
      if (size > limit) throw new Error("UI download exceeds size limit");
      chunks.push(item.value);
    }
    return Buffer.concat(chunks);
  } finally { await reader.cancel().catch(() => undefined); }
}

export class UiUpdateStore {
  constructor(readonly root: string, readonly bootstrap: string, readonly contract: string) {}
  private pointer(): { active?: number; rejected?: number[]; highest?: number } {
    try { return JSON.parse(fs.readFileSync(path.join(this.root, "state.json"), "utf8")); } catch { return {}; }
  }
  private save(state: ReturnType<UiUpdateStore["pointer"]>): void {
    fs.mkdirSync(this.root, { recursive: true });
    const temp = path.join(this.root, "state.tmp");
    fs.writeFileSync(temp, JSON.stringify(state));
    fs.renameSync(temp, path.join(this.root, "state.json"));
  }
  directory(build: number): string {
    if (!Number.isSafeInteger(build) || build <= 0) throw new Error("Invalid UI build");
    return path.join(this.root, String(build));
  }
  accepts(build: number): boolean {
    const state = this.pointer();
    return !(state.rejected || []).includes(build) && build > (state.highest || 0);
  }
  prune(): void {
    if (!fs.existsSync(this.root)) return;
    const entries = fs.readdirSync(this.root, { withFileTypes: true });
    const builds = entries.filter(entry => entry.isDirectory() && /^[1-9]\d*$/.test(entry.name))
      .map(entry => Number(entry.name)).filter(Number.isSafeInteger).sort((a, b) => b - a);
    const keep = new Set([this.pointer().active, ...builds.slice(0, 2)]);
    for (const entry of entries) {
      if (!entry.isDirectory() || !( /^stage-[\w-]+$/.test(entry.name)
        || (/^[1-9]\d*$/.test(entry.name) && !keep.has(Number(entry.name))))) continue;
      const target = path.resolve(this.root, entry.name);
      if (!target.startsWith(path.resolve(this.root) + path.sep)) throw new Error("Invalid UI cache path");
      fs.rmSync(target, { recursive: true, force: true });
    }
  }
  verify(build: number): UiManifest {
    const dir = this.directory(build);
    const m = validateUiManifest(JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf8")), this.bootstrap, this.contract);
    if (m.build !== build) throw new Error("UI build mismatch");
    for (const file of m.files) {
      const target = path.join(dir, "dist", file.path);
      // Never follow a locally substituted junction or symbolic link.
      let current = path.join(dir, "dist");
      for (const segment of ["", ...file.path.split("/")]) {
        current = path.join(current, segment);
        if (fs.lstatSync(current).isSymbolicLink()) throw new Error("UI asset is a link");
      }
      const stat = fs.statSync(target);
      if (!stat.isFile() || stat.size !== file.size || sha256(fs.readFileSync(target)) !== file.sha256) throw new Error("UI asset integrity failed");
    }
    return m;
  }
  activeIndex(): string | null {
    const build = this.pointer().active;
    if (!build) return null;
    try { this.verify(build); return path.join(this.directory(build), "dist", "index.html"); }
    catch {
      try { this.reject(build); } catch (error) { console.warn("Could not persist UI rollback", error); }
      return null;
    }
  }
  confirm(build: number): void {
    this.verify(build);
    const state = this.pointer();
    if ((state.rejected || []).includes(build)) throw new Error("UI build was rejected");
    if (build < (state.highest || 0)) return;
    this.save({ ...state, active: build, highest: build });
  }
  reject(build: number): void {
    const state = this.pointer();
    this.save({ ...state, active: state.active === build ? undefined : state.active,
      rejected: [...new Set([...(state.rejected || []), build])].slice(-100), highest: Math.max(state.highest || 0, build) });
  }
  async stage(manifest: UiManifest, download: (url: string, size: number) => Promise<Buffer>): Promise<void> {
    const m = validateUiManifest(manifest, this.bootstrap, this.contract);
    if (fs.existsSync(this.directory(m.build))) { this.verify(m.build); return; }
    fs.mkdirSync(this.root, { recursive: true });
    const stage = fs.mkdtempSync(path.join(this.root, "stage-"));
    try {
      let index = 0;
      let failure: unknown;
      const workers = await Promise.allSettled(Array.from({ length: Math.min(4, m.files.length) }, async () => {
        while (!failure && index < m.files.length) {
          const file = m.files[index++];
          try {
            const bytes = await download(file.url, file.size);
            if (bytes.length !== file.size || sha256(bytes) !== file.sha256) throw new Error("UI asset integrity failed");
            const target = path.join(stage, "dist", file.path);
            fs.mkdirSync(path.dirname(target), { recursive: true });
            fs.writeFileSync(target, bytes);
          } catch (error) { failure = error; throw error; }
        }
      }));
      // Drain every writer before removing a failed staging directory.
      const rejected = workers.find(result => result.status === "rejected");
      if (rejected?.status === "rejected") throw rejected.reason;
      fs.writeFileSync(path.join(stage, "manifest.json"), JSON.stringify(m));
      const destination = this.directory(m.build);
      if (fs.existsSync(destination)) { this.verify(m.build); return; }
      fs.renameSync(stage, destination);
    } finally { fs.rmSync(stage, { recursive: true, force: true }); }
  }
}
