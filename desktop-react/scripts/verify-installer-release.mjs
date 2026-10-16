import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const { load } = createRequire(import.meta.url)("js-yaml");

export async function verifyInstallerRelease(directory, version) {
  const metadata = load(await fs.readFile(path.join(directory, "latest.yml"), "utf8"));
  assert.equal(metadata?.version, version, "Installer metadata differs from package version");
  assert.ok(Array.isArray(metadata.files) && metadata.files.length === 1, "Expected one x64 installer");
  const file = metadata.files[0];
  assert.equal(file.url, "Loom-Setup-x64.exe", "Unexpected installer asset path");
  const bytes = await fs.readFile(path.join(directory, file.url));
  assert.equal(file.size, bytes.length, "Installer size differs from metadata");
  assert.ok(bytes.length > 1000000, "Installer is unexpectedly small");
  const hash = crypto.createHash("sha512").update(bytes).digest("base64");
  assert.equal(file.sha512, hash, "Installer SHA512 differs from metadata");
  assert.equal(metadata.path, file.url, "Legacy updater path differs from installer");
  assert.equal(metadata.sha512, hash, "Legacy updater SHA512 differs from installer");
  assert.ok((await fs.stat(path.join(directory, `${file.url}.blockmap`))).size > 0, "Installer blockmap is empty");
  return metadata;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const { version } = JSON.parse(await fs.readFile(path.join(project, "package.json"), "utf8"));
  await verifyInstallerRelease(path.join(project, "release"), version);
  console.log(`Installer metadata, size, SHA512 and blockmap verified (v${version}).`);
}
