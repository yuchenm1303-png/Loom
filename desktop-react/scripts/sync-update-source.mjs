// S3-compatible public update mirror. AWS credentials are read by the AWS CLI
// from the runner environment and never written to release files or Git.
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { validateUpdateSource } from "../dist-electron/updateSource.js";
const require = createRequire(import.meta.url), yaml = require("js-yaml");
const source = validateUpdateSource(process.env.LOOM_UPDATE_SOURCE_URL || "");
const bucket = process.env.LOOM_UPDATE_BUCKET || "";
const prefix = (process.env.LOOM_UPDATE_PREFIX || "").replace(/^\/+|\/+$/g, "");
if (!source || !/^[a-zA-Z0-9.-]+$/.test(bucket) || !/^[\w./-]*$/.test(prefix) || prefix.split("/").includes("..")) throw new Error("Configure source URL, bucket and safe optional prefix");
const endpoint = process.env.LOOM_UPDATE_S3_ENDPOINT || "";
if (endpoint && new URL(endpoint).protocol !== "https:") throw new Error("S3 endpoint must use HTTPS");
const repository = "yuchenm1303-png/Loom";
const root = await fs.mkdtemp(path.join(os.tmpdir(), "loom-update-mirror-"));
const run = (tool, args) => execFileSync(tool, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
const groups = new Map(), expectations = new Map();
function asset(url, hash, size, algorithm = "sha256") {
  const match = /^https:\/\/github\.com\/yuchenm1303-png\/Loom\/releases\/download\/([\w.-]+)\/([\w.-]+)$/.exec(url);
  if (!match) throw new Error("Untrusted source asset URL");
  const [, tag, name] = match;
  if (!groups.has(tag)) groups.set(tag, new Set());
  groups.get(tag).add(name);
  if (hash) expectations.set(`${tag}/${name}`, { hash, size, algorithm });
}
const latest = JSON.parse(run("gh", ["release", "view", "--repo", repository, "--json", "tagName,assets"]));
if (!/^v\d+\.\d+\.\d+$/.test(latest.tagName)) throw new Error("Latest release is not an installer release");
const metadataDir = path.join(root, "metadata");
await fs.mkdir(metadataDir);
run("gh", ["release", "download", latest.tagName, "--repo", repository, "--pattern", "latest.yml", "--dir", metadataDir]);
const metadata = yaml.load(await fs.readFile(path.join(metadataDir, "latest.yml"), "utf8"));
if (`v${metadata.version}` !== latest.tagName || !Array.isArray(metadata.files) || !metadata.files.length) throw new Error("Installer metadata/version mismatch");
for (const file of metadata.files) {
  if (!/^[\w.-]+\.exe$/.test(file.url) || !file.sha512 || !Number.isSafeInteger(file.size)) throw new Error("Unsafe installer metadata");
  const base = `https://github.com/${repository}/releases/download/${latest.tagName}/`;
  asset(base + file.url, file.sha512, file.size, "sha512");
  asset(base + file.url + ".blockmap");
}
const upload = (file, key, mutable = false) => {
  const args = ["s3", "cp", file, `s3://${bucket}/${prefix ? prefix + "/" : ""}${key}`,
    "--only-show-errors", "--cache-control", mutable ? "no-cache, max-age=0, must-revalidate" : "public, max-age=31536000, immutable"];
  if (endpoint) args.push("--endpoint-url", endpoint);
  run("aws", args);
};
try {
  // Include inherited content-addressed objects from historical release tags.
  for (const [tag, names] of groups) {
    const directory = path.join(root, tag);
    await fs.mkdir(directory);
    const patterns = [...names];
    for (let index = 0; index < patterns.length; index += 30) {
      run("gh", ["release", "download", tag, "--repo", repository, "--dir", directory,
        ...patterns.slice(index, index + 30).flatMap(name => ["--pattern", name])]);
    }
    for (const name of names) {
      const file = path.join(directory, name), expected = expectations.get(`${tag}/${name}`);
      if (expected) {
        const bytes = await fs.readFile(file);
        const actual = crypto.createHash(expected.algorithm).update(bytes).digest(expected.algorithm === "sha512" ? "base64" : "hex");
        if (bytes.length !== expected.size || actual !== expected.hash) throw new Error(`Mirror asset integrity failure: ${tag}/${name}`);
      }
      upload(file, `releases/${tag}/${name}`);
      // Publish the new blockmap for the generic provider's versioned EXE URL.
    }
  }
  // Do not advertise a source whose bucket is private, whose DNS/TLS is not
  // ready, or whose CDN strips byte ranges (required for incremental updates).
  for (const file of metadata.files) {
    const response = await fetch(`${source}/releases/${latest.tagName}/${file.url}`, {
      headers: { Range: "bytes=0-0" }, signal: AbortSignal.timeout(30000),
    });
    try {
      if (response.status !== 206 || response.headers.get("content-range") !== `bytes 0-0/${file.size}`) {
        throw new Error("Public download source must support HTTPS byte-range requests before channels can be published");
      }
    } finally { await response.body?.cancel(); }
  }
  // All referenced bytes now exist. Preserve versioned URLs in latest.yml so
  // intermediary caches cannot combine one release's EXE with another's hash.
  for (const file of metadata.files) file.url = `../releases/${latest.tagName}/${file.url}`;
  if (metadata.path) metadata.path = `../releases/${latest.tagName}/${path.basename(metadata.path)}`;
  await fs.writeFile(path.join(metadataDir, "latest.yml"), yaml.dump(metadata));
  upload(path.join(metadataDir, "latest.yml"), "installer/latest.yml", true);
  console.log(`Update source synchronized: ${groups.size} release groups; installer metadata published last.`);
} finally { await fs.rm(root, { recursive: true, force: true }); }
