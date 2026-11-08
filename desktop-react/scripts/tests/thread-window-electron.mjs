// Real Electron IPC exercise with invisible native windows and an isolated profile.
import assert from "node:assert/strict";
import { mkdtemp, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL("../../", import.meta.url));
const folder = await mkdtemp(path.join(tmpdir(), "loom-handoff-windows-"));
const output = path.join(folder, "result.json");
await writeFile(path.join(folder, "index.html"), '<!doctype html><html><body><h1>Handoff window</h1></body></html>');
const entry = path.join(folder, "main.cjs");
await writeFile(entry, `
const { app, BrowserWindow, ipcMain } = require('electron');
const { pathToFileURL } = require('node:url');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const progress = value => fs.appendFileSync(${JSON.stringify(path.join(folder, "progress.log"))}, value + '\\n');
progress('boot');
console.log('handoff-fixture-boot');
app.setPath('userData', ${JSON.stringify(path.join(folder, "profile"))});
app.commandLine.appendSwitch('disable-gpu');
app.whenReady().then(async () => {
  console.log('handoff-fixture-ready');
  progress('ready');
  const { openConversationWindow } = await import(${JSON.stringify(pathToFileURL(path.join(root, "dist-electron/conversationWindows.js")).href)});
  const { fromLoomRenderer } = await import(${JSON.stringify(pathToFileURL(path.join(root, "dist-electron/rendererIpc.js")).href)});
  // The test verifies real native windows/renderers/IPC without taking the user's focus.
  BrowserWindow.prototype.show = function() {};
  BrowserWindow.prototype.focus = function() {};
  ipcMain.handle('loom:call', fromLoomRenderer((event, method) => ({ method, window: event.sender.id })));
  const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'];
  const document = { url: pathToFileURL(${JSON.stringify(path.join(folder, "index.html"))}).href, development: false };
  const options = { document, preload: ${JSON.stringify(path.join(root, "dist-electron/preload.cjs"))}, backgroundColor: '#111111', readThread: async id => { assert.ok(ids.includes(id)); } };
  try {
    console.log('handoff-fixture-opening');
    progress('opening');
    await Promise.all([openConversationWindow(ids[0], options), openConversationWindow(ids[0], options)]);
    assert.equal(BrowserWindow.getAllWindows().length, 1);
    progress('first opened');
    await openConversationWindow(ids[1], options);
    const windows = BrowserWindow.getAllWindows();
    assert.equal(windows.length, 2);
    progress('second opened');
    for (const window of windows) {
      const value = await window.webContents.executeJavaScript("window.loom.call('probe')");
      progress('ipc ' + window.webContents.id);
      assert.equal(value.window, window.webContents.id);
      assert.equal(new URL(window.webContents.getURL()).hash.slice(1).startsWith('thread='), true);
      assert.ok(await window.webContents.executeJavaScript("window.loom.windowControl('state')"));
      progress('chrome ' + window.webContents.id);
    }
    windows[0].destroy();
    progress('first closed');
    assert.ok(await windows[1].webContents.executeJavaScript("window.loom.windowControl('state')"));
    progress('remaining chrome');
    const untrusted = new BrowserWindow({ show: false, webPreferences: { preload: options.preload, contextIsolation: true, sandbox: true, nodeIntegration: false } });
    await untrusted.loadURL(document.url);
    progress('untrusted loaded');
    const denied = await untrusted.webContents.executeJavaScript("window.loom.call('probe').then(() => false, () => true)");
    assert.equal(denied, true);
    fs.writeFileSync(${JSON.stringify(output)}, JSON.stringify({ ok: true, windows: 2, duplicateReused: true, sourceIpcPreserved: true, untrustedDenied: true }));
  } catch (error) { fs.writeFileSync(${JSON.stringify(output)}, JSON.stringify({ ok: false, error: error.stack || String(error) })); }
  finally { for (const window of BrowserWindow.getAllWindows()) window.destroy(); app.exit(0); }
}).catch(error => { fs.writeFileSync(${JSON.stringify(output)}, JSON.stringify({ ok: false, error: error.stack || String(error) })); app.exit(1); });
app.on('window-all-closed', () => {});
`);
const electron = require("electron");
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
env.ELECTRON_ENABLE_LOGGING = "1";
const child = spawn(electron, [entry, "--no-sandbox"], { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let log = "";
child.stdout.on("data", chunk => { log += chunk; });
child.stderr.on("data", chunk => { log += chunk; });
const timeout = setTimeout(() => child.kill(), 45000);
const exit = await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", (code, signal) => resolve({ code, signal })); }).finally(() => clearTimeout(timeout));
let result;
try { result = JSON.parse(await readFile(output, "utf8")); } catch { throw new Error(`Electron window exercise did not finish (${JSON.stringify(exit)}): ${await readFile(path.join(folder, "progress.log"), "utf8").catch(() => "no progress")}\n${log.slice(-4000)}`); }
assert.equal(result.ok, true, result.error);
console.log("Real Electron handoff: two native receivers, duplicate reuse, per-window IPC/chrome, and untrusted preview rejection passed.");
