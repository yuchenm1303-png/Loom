import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";

const cwd = process.cwd();
const packageJson = JSON.parse(await fs.readFile(path.join(cwd, "package.json"), "utf8"));
const sourceSha = process.env.GITHUB_SHA || execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
const build = Number(process.env.GITHUB_RUN_NUMBER || 1) * 1000 + Number(process.env.GITHUB_RUN_ATTEMPT || 1);
const tag = `app-${build}`;
const base = `https://github.com/yuchenm1303-png/Loom/releases/download/${tag}/`;
const repository = "yuchenm1303-png/Loom";
const previous = await fs.readFile(path.join(cwd, "../application-runtime/stable.json"), "utf8").then(JSON.parse).catch(() => ({ files: [] }));
const known = new Map(previous.files.map(file => [file.sha256, file]));
const output = path.join(cwd, "application-publish");
await fs.mkdir(output, { recursive: true });
const files = [], uploaded = new Set();
async function walk(directory, relative = "") {
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const name = relative ? `${relative}/${entry.name}` : entry.name;
    if (entry.isDirectory()) { await walk(path.join(directory, entry.name), name); continue; }
    if (!entry.isFile() || name.endsWith(".map")) continue;
    if (/^dist-electron\/(entry|applicationLoader|applicationModuleResolver)\.js$/.test(name)) continue;
    const bytes = await fs.readFile(path.join(directory, entry.name));
    const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
    const inherited = known.get(sha256);
    const url = inherited?.url || `${base}${sha256}`;
    files.push({ path: name, size: bytes.length, sha256, url });
    if (!inherited && !uploaded.has(sha256)) {
      await fs.writeFile(path.join(output, sha256), bytes);
      uploaded.add(sha256);
    }
  }
}
await walk(path.join(cwd, "dist"), "dist");
await walk(path.join(cwd, "dist-electron"), "dist-electron");
const modulePackage = Buffer.from(JSON.stringify({ type: "module" }));
const packageHash = crypto.createHash("sha256").update(modulePackage).digest("hex");
files.push({ path: "package.json", size: modulePackage.length, sha256: packageHash, url: known.get(packageHash)?.url || `${base}${packageHash}` });
if (!known.has(packageHash)) { await fs.writeFile(path.join(output, packageHash), modulePackage); uploaded.add(packageHash); }
const sourceTime = Number(execFileSync("git", ["show", "-s", "--format=%ct", sourceSha], { encoding: "utf8" }).trim());
const release = { schema: 1, kind: "application", build, bootstrap: packageJson.version, sourceSha, sourceTime, files };
const { validateApplicationRelease } = await import("../dist-electron/applicationUpdateStore.js");
validateApplicationRelease(release, packageJson.version);
const manifest = path.join(output, "manifest.json");
await fs.writeFile(manifest, JSON.stringify(release, null, 2));
if (process.argv.includes("--dry-run")) {
  console.log(JSON.stringify({ files: files.length, changedObjects: uploaded.size, size: files.reduce((total, file) => total + file.size, 0) }));
  process.exit(0);
}
const gh = args => execFileSync("gh", args, { stdio: "inherit" });
gh(["release", "create", tag, "--repo", repository, "--target", sourceSha, "--title", `Loom application update ${build}`, "--notes", `Application code and UI for Desktop ${packageJson.version}. Source ${sourceSha}.`, "--latest=false"]);
const assets = [manifest, ...[...uploaded].map(hash => path.join(output, hash))];
for (let i = 0; i < assets.length; i += 30) gh(["release", "upload", tag, ...assets.slice(i, i + 30), "--repo", repository]);
await fs.mkdir(path.join(cwd, "../application-runtime"), { recursive: true });
await fs.writeFile(path.join(cwd, "../application-runtime/stable.json"), JSON.stringify(release, null, 2) + "\n");
