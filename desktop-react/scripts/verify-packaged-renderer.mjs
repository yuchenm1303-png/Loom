import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import asar from "@electron/asar";
import { verifyPackagedUpdateConfig } from "./packaged-update-config.mjs";
import { verifyPackagedRuntime } from "./verify-packaged-runtime.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const archive = path.join(root, "release/win-unpacked/resources/app.asar");
verifyPackagedRuntime(path.dirname(archive));
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const localPackage = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
await verifyPackagedUpdateConfig(path.dirname(archive), localPackage);
const packedPackage = JSON.parse(asar.extractFile(archive, "package.json").toString());
assert.equal(packedPackage.version, localPackage.version, "Packaged version differs from the workspace");

let checked = 0;
function verifyDirectory(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      verifyDirectory(fullPath);
      continue;
    }
    const relative = path.relative(root, fullPath);
    assert.equal(digest(asar.extractFile(archive, relative)), digest(fs.readFileSync(fullPath)),
      `Stale packaged renderer file: ${relative}`);
    checked += 1;
  }
}
verifyDirectory(path.join(root, "dist"));
assert.ok(checked > 0, "Renderer build is empty");
console.log(`Packaged renderer verified: ${checked} files match the current build (v${localPackage.version}).`);
