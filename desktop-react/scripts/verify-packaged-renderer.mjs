import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import asar from "@electron/asar";
import { verifyPackagedUpdateConfig } from "./packaged-update-config.mjs";
import { verifyPackagedRuntime } from "./verify-packaged-runtime.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const archive = process.argv[2] ? path.resolve(process.argv[2]) : path.join(root, "release/win-unpacked/resources/app.asar");
verifyPackagedRuntime(path.dirname(archive));
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const localPackage = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
await verifyPackagedUpdateConfig(path.dirname(archive), localPackage);
const packedPackage = JSON.parse(asar.extractFile(archive, "package.json").toString());
assert.equal(packedPackage.version, localPackage.version, "Packaged version differs from the workspace");

// Legacy cached releases must not be reachable from a complete installation.
const entry = asar.extractFile(archive, "dist-electron/entry.js").toString();
const main = asar.extractFile(archive, "dist-electron/main.js").toString();
assert.ok(!entry.includes("selectApplication"), "Packaged entry still selects cached application releases");
assert.ok(!main.includes("loadUpdatedRenderer"), "Packaged main still selects cached UI releases");
for (const file of ["applicationLoader.js", "applicationModuleResolver.js", "applicationUpdater.js", "rendererUpdater.js"]) {
  assert.ok(!asar.listPackage(archive).some(name => name.endsWith(file)), "Obsolete component updater shipped: " + file);
}

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
      `Stale packaged file: ${relative}`);
    checked += 1;
  }
}
verifyDirectory(path.join(root, "dist"));
// A new version label is insufficient: every main-process module must match
// the candidate too, including the account and update fixes.
verifyDirectory(path.join(root, "dist-electron"));
assert.ok(checked > 0, "Renderer build is empty");
const rendererScripts = asar.listPackage(archive)
  .filter(file => /[\\/]dist[\\/]assets[\\/].*\.js$/.test(file))
  .map(file => asar.extractFile(archive, file.replace(/^[\\/]/, "")).toString());
assert.ok(rendererScripts.some(script => script.includes("draftPermissionMode")),
  "Packaged renderer lacks draft permission support; rebuild from current source before releasing");
console.log(`Packaged application verified: ${checked} files match the current build (v${localPackage.version}).`);
