import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { pathToFileURL } from "node:url";
import { prepareRendererRelease, rendererHash } from "../../dist-electron/rendererUpdateStore.js";

const directory = await fs.mkdtemp(path.join(os.tmpdir(), "loom-ui-activation-"));
const root = path.join(directory, "ui-updates");
const ipcMain = new EventEmitter(), app = new EventEmitter();
Object.assign(app, { isPackaged: true, getVersion: () => "0.1.82", getPath: () => directory });
mock.module("electron", { namedExports: { app, ipcMain, net: { fetch: async () => new Response("missing", { status: 404 }) } } });
const { loadUpdatedRenderer } = await import("../../dist-electron/rendererUpdater.js");
function window() {
  const win = new EventEmitter();
  win.webContents = new EventEmitter();
  win.loads = [];
  win.isDestroyed = () => false;
  win.loadFile = async file => { win.loads.push(file); };
  win.webContents.getURL = () => pathToFileURL(win.loads.at(-1)).href;
  return win;
}
async function bundle(build) {
  const bytes = Buffer.from(`<html>${build}</html>`), sha256 = rendererHash(bytes);
  const release = { schema: 1, build, bootstrap: "0.1.82", files: [{ path: "index.html", size: bytes.length,
    sha256, url: `https://github.com/yuchenm1303-png/Loom/releases/download/ui-1/${sha256}` }] };
  return prepareRendererRelease(root, release, async () => bytes);
}
const read = async () => JSON.parse(await fs.readFile(path.join(root, "state.json"), "utf8"));
const write = value => fs.writeFile(path.join(root, "state.json"), JSON.stringify(value));
async function until(predicate) {
  for (let i = 0; i < 100; i++) { if (await predicate()) return; await new Promise(resolve => setTimeout(resolve, 10)); }
  throw new Error("Activation did not settle");
}

test("renderer activation commits only on matching window health and rolls back failures", async () => {
  try {
    const first = await bundle(1);
    await write({ ready: 1 });
    const win = window();
    await loadUpdatedRenderer(win, "bundled.html");
    assert.equal((await read()).booting, 1);
    ipcMain.emit("loom:renderer-healthy", { sender: {} });
    assert.equal((await read()).active, undefined);
    ipcMain.emit("loom:renderer-healthy", { sender: win.webContents });
    await until(async () => (await read()).active === 1);
    win.webContents = null;
    win.emit("closed");
    await bundle(2);
    await write({ active: 1, ready: 2 });
    const broken = window();
    await loadUpdatedRenderer(broken, "bundled.html");
    broken.webContents.emit("did-fail-load");
    await until(async () => broken.loads.length === 2);
    assert.equal(broken.loads.at(-1), path.join(first, "index.html"));
    assert.equal((await read()).active, 1);
    assert.ok((await read()).rejected.includes(2));
    broken.emit("closed");
    // A process killed before health acknowledgement cannot boot-loop.
    await write({ active: 1, ready: 2, booting: 2 });
    const recovered = window();
    await loadUpdatedRenderer(recovered, "bundled.html");
    assert.equal(recovered.loads.at(-1), path.join(first, "index.html"));
    assert.ok((await read()).rejected.includes(2));
    recovered.emit("closed");
  } finally { app.emit("before-quit"); await fs.rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 50 }); }
});
