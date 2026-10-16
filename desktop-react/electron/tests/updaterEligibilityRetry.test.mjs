import { test, mock, after } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

// The NSIS updater is Windows-only; exercise that branch on Linux CI too.
// This test process is isolated from the rest of the test files.
const originalPlatform = process.platform;
Object.defineProperty(process, "platform", { value: "win32" });
after(() => Object.defineProperty(process, "platform", { value: originalPlatform }));

const home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-installer-flow-"));
after(() => fs.rm(home, { recursive: true, force: true }));

let allowed = false, installerChecks = 0, applicationChecks = 0, installs = 0;
let checkGate = Promise.resolve(), installerAvailable = false, canRestartApplication;
let automaticUpdates = false, restartNotifications = 0;
const order = [];
const feeds = [];
let feed;
const updater = new EventEmitter();
updater.setFeedURL = value => { feed = value; feeds.push(value.provider); };
updater.checkForUpdates = async () => {
  installerChecks++;
  order.push("installer");
  await checkGate;
  if (feed?.provider === "generic") {
    updater.emit("error", new Error("CDN unavailable"));
    throw new Error("CDN unavailable");
  }
  updater.emit(installerAvailable ? "update-available" : "update-not-available", { version: "0.1.91" });
};
updater.quitAndInstall = () => { installs++; };
mock.module("electron", { namedExports: {
  app: { isPackaged: true, getVersion: () => "0.1.90", getPath: () => home, on() {}, whenReady: () => ({ then() {} }) },
  BrowserWindow: { getAllWindows: () => [] }, dialog: {}, ipcMain: {}, shell: {},
} });
mock.module("electron-updater", { defaultExport: { autoUpdater: updater } });
mock.module("../../dist-electron/optimizedUpdater.js", { namedExports: {
  OptimizedNsisUpdater: class { constructor() { return updater; } },
} });
mock.module("../../dist-electron/updateSource.js", { namedExports: {
  configuredUpdateSource: () => "https://updates.example.test/loom",
} });
mock.module("../../dist-electron/updatePreferences.js", { namedExports: {
  readUpdatePreferences: () => ({ automaticUpdates, silentUpdates: true, autoRestartAfterUpdate: true }),
  writeUpdatePreferences() {},
} });
mock.module("../../dist-electron/updateEligibility.js", { namedExports: { mayReceiveUpdates: async () => allowed } });
mock.module("../../dist-electron/hostProcess.js", { namedExports: {
  isHostProcess: true, broadcastHostEvent(channel) { if (channel === "loom:host-updating") restartNotifications++; }, callHost: async () => {}, handleHostChannel() {}, hostHasDesktopClients: () => false,
} });
mock.module("../../dist-electron/applicationUpdater.js", { namedExports: {
  applicationUpdateState: () => ({ phase: "idle" }),
  checkApplicationUpdates: async () => { order.push("application"); applicationChecks++; },
  installApplicationUpdate: async () => { installs++; return true; }, onApplicationUpdateStatus() {},
  registerApplicationUpdatePriorityGuard(guard) { canRestartApplication = guard; },
} });
mock.module("../../dist-electron/hostRuntimeUpdater.js", { namedExports: {
  ensureHostRuntimeUpdate: async () => {}, hostRuntimeUpdateState: () => ({}), onHostRuntimeUpdateStatus() {},
} });
const { checkForUpdates, installDownloadedUpdate, registerHeadlessUpdateGuard } = await import("../../dist-electron/updater.js");

test("failed eligibility schedules recovery while blocking downloads and installs", async (t) => {
  const originalTimeout = globalThis.setTimeout;
  const timers = t.mock.method(globalThis, "setTimeout", (...args) => originalTimeout(...args));
  automaticUpdates = true;
  const denied = await checkForUpdates();
  assert.equal(denied.phase, "disabled");
  assert.equal(denied.accountUpdatesAllowed, false);
  assert.equal(denied.enabled, true, "installed updater must allow the user to retry");
  assert.equal(installerChecks, 0);
  assert.equal(applicationChecks, 0);
  assert.ok(timers.mock.calls.some(call => call.arguments[1] === 60_000), "verification failure must schedule the short retry");
  automaticUpdates = false;
  assert.equal((await installDownloadedUpdate()).accepted, false);
  assert.equal(installs, 0);
  allowed = true;
  const recovered = await checkForUpdates();
  assert.equal(recovered.accountUpdatesAllowed, true);
  assert.equal(recovered.phase, "up-to-date");
  assert.equal(installerChecks, 2);
  assert.equal(applicationChecks, 1);
  assert.deepEqual(feeds, ["generic", "github"], "a failed mirror immediately retries GitHub discovery");
});

test("overlapping checks share a single installer-first operation and block component restarts", async () => {
  let finish;
  checkGate = new Promise(resolve => { finish = resolve; });
  const before = installerChecks;
  order.length = 0;
  const checks = [checkForUpdates(), checkForUpdates(), checkForUpdates()];
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(installerChecks, before + 1);
  assert.equal(canRestartApplication(), false);
  finish();
  await Promise.all(checks);
  assert.deepEqual(order, ["installer", "application"]);
  assert.equal(canRestartApplication(), true);
});

test("a required installer suppresses component upgrades; manual install protects tasks and schedules one restart", async () => {
  installerAvailable = true;
  const before = applicationChecks;
  await checkForUpdates();
  assert.equal(applicationChecks, before);
  assert.equal(canRestartApplication(), false);
  automaticUpdates = true;
  updater.emit("update-downloaded", { version: "0.1.91" });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(restartNotifications, 0, "automatic installation must wait until a task guard is registered");
  automaticUpdates = false;
  registerHeadlessUpdateGuard(() => false);
  assert.equal((await installDownloadedUpdate()).accepted, false);
  assert.equal(installs, 0);
  allowed = false;
  assert.equal((await installDownloadedUpdate()).state.phase, "disabled");
  allowed = true;
  assert.equal((await checkForUpdates()).phase, "downloaded", "verification retry preserves the downloaded installer");
  let taskChecks = 0;
  registerHeadlessUpdateGuard(() => ++taskChecks === 1);
  assert.equal((await installDownloadedUpdate()).accepted, false, "tasks started during account verification must block restart");
  assert.equal(restartNotifications, 0);
  registerHeadlessUpdateGuard(() => true);
  const results = await Promise.all([installDownloadedUpdate(), installDownloadedUpdate()]);
  assert.equal(results.filter(result => result.accepted).length, 1);
  await new Promise(resolve => setTimeout(resolve, 550));
  assert.equal(installs, 1);
});
