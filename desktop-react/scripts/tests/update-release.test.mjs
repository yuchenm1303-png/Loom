import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";
import { createRequire } from "node:module";
import { verifyInstallerRelease } from "../verify-installer-release.mjs";
const yaml = createRequire(import.meta.url)("js-yaml");

test("release verification rejects mismatched versions, tampering and missing blockmaps", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "loom-release-check-"));
  try {
    const bytes = Buffer.alloc(1000001, 7);
    const name = "Loom-Setup-x64.exe";
    const hash = crypto.createHash("sha512").update(bytes).digest("base64");
    const metadata = { version: "0.1.94", files: [{ url: name, size: bytes.length, sha512: hash }], path: name, sha512: hash };
    await fs.writeFile(path.join(directory, name), bytes);
    await fs.writeFile(path.join(directory, `${name}.blockmap`), "map");
    await fs.writeFile(path.join(directory, "latest.yml"), yaml.dump(metadata));
    await verifyInstallerRelease(directory, "0.1.94");
    await assert.rejects(verifyInstallerRelease(directory, "0.1.93"), /version/);
    bytes[0] = 8;
    await fs.writeFile(path.join(directory, name), bytes);
    await assert.rejects(verifyInstallerRelease(directory, "0.1.94"), /SHA512/);
    bytes[0] = 7;
    await fs.writeFile(path.join(directory, name), bytes);
    await fs.unlink(path.join(directory, `${name}.blockmap`));
    await assert.rejects(verifyInstallerRelease(directory, "0.1.94"), /ENOENT/);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test("Windows workflows run update gates and do not dispatch retired component publishers", async () => {
  const pkg = JSON.parse(await fs.readFile(new URL("../../package.json", import.meta.url), "utf8"));
  assert.ok(pkg.scripts["package:win"].includes("npm run test:electron"));
  assert.ok(pkg.scripts["package:win"].includes("node scripts/verify-installer-release.mjs"));
  for (const name of ["windows-release.yml", "windows-release-on-version.yml"]) {
    const source = await fs.readFile(new URL(`../../../.github/workflows/${name}`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /workflow run (application|ui|host)-runtime-release/);
    assert.ok(source.includes("npm run package:win"));
    assert.ok(source.includes("node scripts/verify-installer-release.mjs"));
  }
});
