import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "..", "updater.ts"), "utf8");

test("web-only Host keeps bootstrap update checks armed", () => {
  assert.match(source, /function runAutomaticCheck\(\): void {[\s\S]*if \(hasVisibleWindow\(\)\)[\s\S]*checkForUpdates\(\)[\s\S]*if \(isHostProcess\) void ensureBootstrapUpdate\(\);/);
  assert.match(source, /startupTimer = setTimeout\(\(\) => {[\s\S]*runAutomaticCheck\(\);/);
  assert.match(source, /periodicTimer = setInterval\(runAutomaticCheck, PERIODIC_CHECK_INTERVAL_MS\);/);
  assert.match(source, /app\.whenReady\(\)\.then\(\(\) => {[\s\S]*startAutomaticChecks\(\);/);
});
