import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";

let allowed = false, installerChecks = 0, applicationChecks = 0, installs = 0;
const updater = new EventEmitter();
updater.checkForUpdates = async () => {
  installerChecks++;
  updater.emit("update-not-available", { version: "0.1.90" });
};
mock.module("electron", { namedExports: {
  app: { isPackaged: true, getVersion: () => "0.1.90", on() {}, whenReady: () => ({ then() {} }) },
  BrowserWindow: { getAllWindows: () => [] }, dialog: {}, ipcMain: {}, shell: {},
} });
mock.module("electron-updater", { defaultExport: { autoUpdater: updater } });
mock.module("../../dist-electron/optimizedUpdater.js", { namedExports: {
  OptimizedNsisUpdater: class { constructor() { return updater; } },
} });
mock.module("../../dist-electron/updatePreferences.js", { namedExports: {
  readUpdatePreferences: () => ({ automaticUpdates: true, silentUpdates: true, autoRestartAfterUpdate: true }),
  writeUpdatePreferences() {},
} });
mock.module("../../dist-electron/updateEligibility.js", { namedExports: { mayReceiveUpdates: async () => allowed } });
mock.module("../../dist-electron/hostProcess.js", { namedExports: {
  isHostProcess: true, broadcastHostEvent() {}, callHost: async () => {}, handleHostChannel() {}, hostHasDesktopClients: () => false,
} });
mock.module("../../dist-electron/applicationUpdater.js", { namedExports: {
  applicationUpdateState: () => ({ phase: "idle" }),
  checkApplicationUpdates: async () => { applicationChecks++; },
  installApplicationUpdate: async () => { installs++; return true; }, onApplicationUpdateStatus() {},
} });
mock.module("../../dist-electron/hostRuntimeUpdater.js", { namedExports: {
  ensureHostRuntimeUpdate: async () => {}, hostRuntimeUpdateState: () => ({}), onHostRuntimeUpdateStatus() {},
} });
const { checkForUpdates, installDownloadedUpdate } = await import("../../dist-electron/updater.js");

test("failed eligibility keeps manual retry available while blocking downloads and installs", async () => {
  const denied = await checkForUpdates();
  assert.equal(denied.phase, "disabled");
  assert.equal(denied.accountUpdatesAllowed, false);
  assert.equal(denied.enabled, true, "installed updater must allow the user to retry");
  assert.equal(installerChecks, 0);
  assert.equal(applicationChecks, 0);
  assert.equal((await installDownloadedUpdate()).accepted, false);
  assert.equal(installs, 0);
  allowed = true;
  const recovered = await checkForUpdates();
  assert.equal(recovered.accountUpdatesAllowed, true);
  assert.equal(recovered.phase, "up-to-date");
  assert.equal(installerChecks, 1);
  assert.equal(applicationChecks, 1);
});
