// Real, hidden Electron window: verified disk assets, CSP/IPC trust, storage continuity and health handshake.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
const require = createRequire(import.meta.url);
const desktop = fileURLToPath(new URL("../../", import.meta.url));
const folder = await fs.mkdtemp(path.join(os.tmpdir(), "loom-ui-native-"));
const output = path.join(folder, "result.json");
const entry = path.join(folder, "main.cjs");
const moduleUrl = name => pathToFileURL(path.join(desktop, "dist-electron", name + ".js")).href;
await fs.writeFile(path.join(folder, "index.html"), "<!doctype html><html><body>Bundled interface</body></html>");
await fs.writeFile(entry, `
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');
app.setPath('userData', ${JSON.stringify(path.join(folder, "profile"))});
app.commandLine.appendSwitch('disable-gpu');
app.whenReady().then(async () => {
  try {
    const { UiUpdateStore, sha256 } = await import(${JSON.stringify(moduleUrl("uiUpdateStore"))});
    const { trustRendererDocument, fromLoomRenderer, sentByLoomRenderer } = await import(${JSON.stringify(moduleUrl("rendererIpc"))});
    const { installRendererContentSecurity } = await import(${JSON.stringify(moduleUrl("contentSecurity"))});
    const contract = 'a'.repeat(64);
    const store = new UiUpdateStore(${JSON.stringify(path.join(folder, "cache"))}, '1.0.0', contract);
    const contents = new Map([
      ['index.html', Buffer.from('<!doctype html><html><body>Updated interface<script src="health.js"></script></body></html>')],
      ['health.js', Buffer.from('setTimeout(() => window.dispatchEvent(new Event("loom:renderer-healthy")), 50);')],
    ]);
    const manifest = { schema: 2, build: 1, bootstrap: '1.0.0', contract, files: [...contents].map(([name, bytes]) => ({
      path: name, size: bytes.length, sha256: sha256(bytes), url: 'https://github.com/yuchenm1303-png/Loom/releases/download/ui-1/' + sha256(bytes),
    })) };
    await store.stage(manifest, async url => [...contents.values()].find(bytes => url.endsWith(sha256(bytes))));
    const window = new BrowserWindow({ show: false, webPreferences: { preload: ${JSON.stringify(path.join(desktop, "dist-electron/preload.cjs"))}, contextIsolation: true, sandbox: true, nodeIntegration: false } });
    ipcMain.handle('loom:call', fromLoomRenderer(() => 'bridge-kept'));
    await window.loadFile(${JSON.stringify(path.join(folder, "index.html"))});
    await window.webContents.executeJavaScript('localStorage.setItem("ui.probe", "kept"); sessionStorage.setItem("ui.route", "kept")');
    const url = pathToFileURL(path.join(store.directory(1), 'dist', 'index.html')).href;
    trustRendererDocument(window, url);
    installRendererContentSecurity(window, { url, development: false });
    const healthy = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('No renderer health signal')), 10000);
      ipcMain.on('loom:renderer-healthy', event => {
        if (event.sender !== window.webContents || !sentByLoomRenderer(event)) return;
        clearTimeout(timer); store.confirm(1); resolve();
      });
    });
    await window.loadURL(url + '#thread=preserved');
    await healthy;
    assert.ok(store.activeIndex());
    assert.equal(await window.webContents.executeJavaScript('localStorage.getItem("ui.probe")'), 'kept');
    assert.equal(await window.webContents.executeJavaScript('sessionStorage.getItem("ui.route")'), 'kept');
    assert.equal(await window.webContents.executeJavaScript('window.loom.call("probe")'), 'bridge-kept');
    assert.equal(new URL(window.webContents.getURL()).hash, '#thread=preserved');
    fs.writeFileSync(${JSON.stringify(output)}, JSON.stringify({ ok: true }));
  } catch (error) { fs.writeFileSync(${JSON.stringify(output)}, JSON.stringify({ ok: false, error: error.stack || String(error) })); }
  finally { for (const window of BrowserWindow.getAllWindows()) window.destroy(); app.exit(0); }
});
app.on('window-all-closed', () => {});
`);
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require("electron"), [entry], { cwd: desktop, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let log = "";
child.stdout.on("data", chunk => { log += chunk; });
child.stderr.on("data", chunk => { log += chunk; });
const timeout = setTimeout(() => child.kill(), 30_000);
try {
  await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", resolve); });
  const result = JSON.parse(await fs.readFile(output, "utf8").catch(() => { throw new Error(log.slice(-3000)); }));
  assert.equal(result.ok, true, result.error);
  console.log("PASS: real Electron cached UI switch preserves storage, route, sandboxed IPC and CSP; React-style health confirmation activates the build.");
} finally { clearTimeout(timeout); await fs.rm(folder, { recursive: true, force: true }); }
