import assert from "node:assert/strict";
import { test, mock, after } from "node:test";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { sha256 } from "../../dist-electron/uiUpdateStore.js";
import { trustRendererDocument } from "../../dist-electron/rendererIpc.js";

const platform = process.platform;
Object.defineProperty(process, "platform", { value: "win32" });
after(() => Object.defineProperty(process, "platform", { value: platform }));
const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-ui-lifecycle-"));
after(() => fs.rmSync(root, { recursive: true, force: true }));
const contract = JSON.parse(fs.readFileSync(new URL("../../dist-electron/ui-contract.json", import.meta.url))).contract;
const bytes = Buffer.from("<html>new interface</html>");
let build = 501, allowed = true, safe = false, automatic = true, downloads = 0;
const ipcMain = new EventEmitter();
const handlers = new Map();
ipcMain.handle = (channel, handler) => handlers.set(channel, handler);
const app = new EventEmitter();
Object.assign(app, { isPackaged: true, getPath: () => root, getVersion: () => "1.0.0", whenReady: () => ({ then() {} }) });
const window = new EventEmitter();
const contents = new EventEmitter();
let url = "file:///bundled/index.html#thread=kept", navigations = 0;
contents.mainFrame = { origin: "file://", url };
const event = () => ({ sender: contents, senderFrame: contents.mainFrame });
contents.session = { webRequest: { onHeadersReceived() {} } };
contents.getURL = () => url;
contents.send = (channel, token) => {
  if (channel === "loom:ui-update-prepare") queueMicrotask(() => ipcMain.emit("loom:ui-update-prepared", event(), token, safe));
};
Object.assign(window, { webContents: contents, isDestroyed: () => false,
  loadURL: async target => { url = target; contents.mainFrame.url = target; navigations++; } });
trustRendererDocument(window, url);
mock.module("electron", { namedExports: { app, ipcMain, BrowserWindow: { fromWebContents: sender => sender === contents ? window : null } } });
mock.module("../../dist-electron/hostProcess.js", { namedExports: { isHostProcess: false } });
mock.module("../../dist-electron/updateEligibility.js", { namedExports: { mayReceiveUpdates: async () => allowed } });
mock.module("../../dist-electron/updatePreferences.js", { namedExports: { readUpdatePreferences: () => ({ automaticUpdates: automatic }) } });
const fetchBefore = globalThis.fetch;
after(() => { globalThis.fetch = fetchBefore; app.emit("before-quit"); });
globalThis.fetch = async target => {
  downloads++;
  if (target.includes("stable.json")) return new Response(JSON.stringify({ schema: 2, build, bootstrap: "1.0.0", contract,
    files: [{ path: "index.html", size: bytes.length, sha256: sha256(bytes),
      url: `https://github.com/yuchenm1303-png/Loom/releases/download/ui-${build}/${sha256(bytes)}` }] }));
  return new Response(bytes);
};
const { checkUiUpdates, recoverUiWindow } = await import("../../dist-electron/uiUpdater.js");

test("automatic refresh waits for inputs, verifies entitlement, preserves route and confirms actual renderer health", async () => {
  allowed = false;
  await checkUiUpdates(); assert.equal(downloads, 0);
  allowed = true; automatic = false;
  await checkUiUpdates(); assert.equal(downloads, 0);
  await checkUiUpdates(true);
  assert.equal(navigations, 0, "draft/busy renderer must not refresh");
  assert.equal((await handlers.get("loom:ui-update-status")(event())).phase, "waiting");
  safe = true;
  await checkUiUpdates(true);
  assert.equal(navigations, 1);
  assert.equal(new URL(url).hash, "#thread=kept");
  const statePath = path.join(root, "ui-updates", contract, "state.json");
  assert.equal(fs.existsSync(statePath), false, "navigation alone is not a healthy React boot");
  ipcMain.emit("loom:renderer-healthy", { sender: contents, senderFrame: { origin: "file://", url } });
  assert.equal(fs.existsSync(statePath), false, "subframes cannot confirm a trial");
  ipcMain.emit("loom:renderer-healthy", event());
  assert.equal(JSON.parse(fs.readFileSync(statePath)).active, build);
  assert.equal((await handlers.get("loom:ui-update-status")(event())).phase, "updated");
  assert.equal(recoverUiWindow(window), true, "even a later renderer crash rolls back");
  assert.ok(!JSON.parse(fs.readFileSync(statePath)).active);
  assert.equal(new URL(url).hash, "#thread=kept");
  assert.ok(url.includes("/dist/index.html"));
  const before = navigations;
  await checkUiUpdates(true);
  assert.equal(navigations, before, "bad build must not cause a crash loop");
});
