import { test, mock } from "node:test";
import assert from "node:assert/strict";

let installerChecks = 0;
mock.module("electron", { namedExports: { app: { getVersion: () => "0.1.94" } } });
mock.module("../../dist-electron/hostRuntime.js", { namedExports: {
  currentHostRuntime: () => ({ version: "1.2.3", protocol: 2 }),
} });
mock.module("../../dist-electron/updater.js", { namedExports: {
  ensureBootstrapUpdate: async () => { installerChecks++; },
} });
const updater = await import("../../dist-electron/hostRuntimeUpdater.js");

test("legacy Web runtime update requests check the complete installer without switching a runtime", async () => {
  assert.deepEqual(updater.hostRuntimeUpdateState(), {enabled:false, phase:"disabled", currentVersion:"1.2.3", currentProtocol:2});
  assert.equal((await updater.ensureHostRuntimeUpdate(2)).phase, "disabled");
  const incompatible = await updater.ensureHostRuntimeUpdate(3);
  assert.equal(incompatible.phase, "incompatible");
  assert.equal(incompatible.currentVersion, "1.2.3");
  assert.equal(incompatible.requiredProtocol, 3);
  assert.equal(installerChecks, 2);
});
