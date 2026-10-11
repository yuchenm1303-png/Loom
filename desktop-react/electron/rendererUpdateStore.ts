import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

export interface RendererFile { path: string; size: number; sha256: string; url: string }
export interface RendererRelease { schema: 1; build: number; bootstrap: string; files: RendererFile[]; sourceSha?: string; sourceTime?: number }
export function validateRendererRelease(raw: unknown, bootstrap: string): RendererRelease {
  const value = raw as RendererRelease;
  if (!value || value.schema !== 1 || value.bootstrap !== bootstrap || !Number.isSafeInteger(value.build)
    || value.build <= 0 || !Array.isArray(value.files) || value.files.length > 10000) throw new Error("Incompatible UI update manifest");
  if (value.sourceTime !== undefined && (!Number.isSafeInteger(value.sourceTime) || value.sourceTime < 0)) throw new Error("Invalid UI source time");
  let total = 0;
  const seen = new Set<string>();
  for (const file of value.files) {
    if (!file || !/^[a-zA-Z0-9_@./-]+$/.test(file.path) || file.path.startsWith("/")
      || file.path.split("/").some(part => !part || part === "." || part === "..")
      || seen.has(file.path.toLowerCase()) || !Number.isSafeInteger(file.size) || file.size < 0
      || !/^[a-f0-9]{64}$/.test(file.sha256)
      || !new RegExp(`^https://github\\.com/yuchenm1303-png/Loom/releases/download/ui-\\d+/${file.sha256}$`).test(file.url)
      || file.path.split("/").some(part => /^(con|prn|aux|nul|com\d|lpt\d)(\.|$)/i.test(part))) throw new Error("Invalid UI update file");
    seen.add(file.path.toLowerCase()); total += file.size;
  }
  if (!seen.has("index.html") || total > 128 * 1024 * 1024) throw new Error("Invalid UI update size or entrypoint");
  return value;
}
export function rendererHash(data: Buffer) { return crypto.createHash("sha256").update(data).digest("hex"); }

export async function readRendererRelease(root: string, bootstrap: string): Promise<RendererRelease | null> {
  try { return validateRendererRelease(JSON.parse(await fs.readFile(path.join(root, "release.json"), "utf8")), bootstrap); }
  catch { return null; }
}
export async function verifyRendererRelease(root: string, release: RendererRelease) {
  for (const file of release.files) {
    const bytes = await fs.readFile(path.join(root, file.path));
    if (bytes.length !== file.size || rendererHash(bytes) !== file.sha256) throw new Error("UI update checksum mismatch");
  }
}
export async function prepareRendererRelease(root: string, release: RendererRelease,
  fetchFile: (file: RendererFile) => Promise<Buffer>) {
  const staging = path.join(root, `staging-${release.build}`), target = path.join(root, String(release.build));
  await fs.rm(staging, { recursive: true, force: true });
  await fs.mkdir(staging, { recursive: true });
  try {
    let cursor = 0;
    const jobs = Array.from({ length: 4 }, async () => {
      while (cursor < release.files.length) {
        const file = release.files[cursor++], cache = path.join(root, "objects", file.sha256);
        let bytes: Buffer;
        try { bytes = await fs.readFile(cache); if (rendererHash(bytes) !== file.sha256) throw new Error("Corrupt cache"); }
        catch { bytes = await fetchFile(file); }
        if (bytes.length !== file.size || rendererHash(bytes) !== file.sha256) throw new Error("UI update checksum mismatch");
        await fs.mkdir(path.dirname(cache), { recursive: true });
        await fs.writeFile(cache, bytes);
        const output = path.join(staging, file.path);
        await fs.mkdir(path.dirname(output), { recursive: true });
        await fs.writeFile(output, bytes);
      }
    });
    const results = await Promise.allSettled(jobs);
    const failed = results.find(item => item.status === "rejected");
    if (failed?.status === "rejected") throw failed.reason;
    await fs.writeFile(path.join(staging, "release.json"), JSON.stringify(release));
    await fs.rm(target, { recursive: true, force: true });
    await fs.rename(staging, target);
    return target;
  } finally { await fs.rm(staging, { recursive: true, force: true }); }
}
