// Real Electron probe: downloaded ESM resolves installed dependencies and its
// sandboxed preload loads from outside app.asar. Uses an isolated profile.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawn } from "node:child_process";
import net from "node:net";
import { applicationHash, prepareApplicationRelease } from "../dist-electron/applicationUpdateStore.js";
const codeRoot = fileURLToPath(new URL("../", import.meta.url));
let installed = codeRoot;
const require = createRequire(new URL("../package.json", import.meta.url));
const home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-app-smoke-"));
const root = path.join(home, "application-updates"), bytes = {};
const version = JSON.parse(await fs.readFile(path.join(codeRoot, "package.json"), "utf8")).version;
try {
  if (process.argv.includes("--asar")) {
    const input = path.join(home, "installed-fixture");
    await fs.mkdir(path.join(input, "node_modules"), { recursive: true });
    await fs.writeFile(path.join(input, "package.json"), JSON.stringify({ type: "module", version }));
    const copied = new Set();
    async function dependency(name, from = path.join(codeRoot, "package.json")) {
      if (copied.has(name)) return;
      copied.add(name);
      const resolver = createRequire(from);
      let directory = path.dirname(resolver.resolve(name));
      while (!await fs.stat(path.join(directory, "package.json")).catch(() => null)) directory = path.dirname(directory);
      const descriptor = path.join(directory, "package.json");
      const info = JSON.parse(await fs.readFile(descriptor, "utf8"));
      await fs.cp(directory, path.join(input, "node_modules", name), { recursive: true });
      for (const child of Object.keys(info.dependencies || {})) await dependency(child, descriptor);
    }
    await dependency("electron-updater"); await dependency("ws");
    installed = path.join(home, "installed.asar");
    await require("@electron/asar").createPackage(input, installed);
  }
  for (const name of await fs.readdir(path.join(codeRoot, "dist-electron"))) {
    if (/\.(js|cjs)$/.test(name)) bytes[`dist-electron/${name}`] = await fs.readFile(path.join(codeRoot, "dist-electron", name));
  }
  bytes["package.json"] = Buffer.from('{"type":"module"}');
  bytes["dist/ui-baseline.json"] = Buffer.from('{}');
  bytes["dist/index.html"] = Buffer.from('<html><script>window.addEventListener("DOMContentLoaded", () => { if (!window.loom) throw new Error("Missing preload"); window.dispatchEvent(new Event("loom:renderer-healthy")); });</script></html>');
  // Import real relocated modules that depend on ws and electron-updater, then
  // load the real preload. Automatic network checks stay disabled in the probe.
  bytes["dist-electron/applicationEntry.js"] = Buffer.from(`
    import { app, BrowserWindow, ipcMain } from "electron";
    import path from "node:path";
    import { fileURLToPath } from "node:url";
    console.log("PROBE_APPLICATION_ENTRY");
    process.env.LOOM_DISABLE_AUTO_UPDATES = "1";
    await import("./remoteRelay.js");
    console.log("PROBE_RELAY_IMPORTED");
    await import("./updater.js");
    console.log("PROBE_UPDATER_IMPORTED");
    app.whenReady().then(async () => {
    const directory = path.dirname(fileURLToPath(import.meta.url));
    const window = new BrowserWindow({ show: false, webPreferences: { preload: path.join(directory, "preload.cjs"), sandbox: true, contextIsolation: true, nodeIntegration: false } });
    let loaded = false, healthy = false, finished = false;
    const finish = async () => {
      if (!loaded || !healthy || finished) return;
      finished = true;
      await globalThis.loomApplicationBootstrap.healthy();
      console.log("APPLICATION_UPDATE_SMOKE_OK"); window.destroy(); app.exit(0);
    };
    window.webContents.on("preload-error", (_e, _p, error) => { console.error(error); app.exit(1); });
    ipcMain.once("loom:renderer-healthy", () => { healthy = true; void finish(); });
    await window.loadFile(path.join(directory, "../dist/index.html"));
    loaded = true; await finish();
    setTimeout(() => app.exit(2), 15000).unref();
    }).catch(error => { console.error(error); app.exit(1); });
  `);
  const release = { schema: 1, kind: "application", bootstrap: version, build: 1, sourceSha: "a".repeat(40), sourceTime: 4000000000,
    files: Object.entries(bytes).map(([name, data]) => { const sha256 = applicationHash(data);
      return { path: name, size: data.length, sha256, url: `https://github.com/yuchenm1303-png/Loom/releases/download/app-1/${sha256}` }; }) };
  await prepareApplicationRelease(root, release, async file => bytes[file.path]);
  await fs.writeFile(path.join(root, "state.json"), JSON.stringify({ ready: 1 }));
  await fs.writeFile(path.join(home, "package.json"), JSON.stringify({ name: "loom-app-update-smoke", version, type: "module", main: "probe.mjs" }));
  await fs.writeFile(path.join(home, "probe.mjs"), `
    import { app } from "electron";
    import { pathToFileURL } from "node:url";
    import fs from "node:fs";
    const trace = value => fs.appendFileSync(${JSON.stringify(path.join(home, "trace.txt"))}, value + "\\n");
    trace("PROBE_STARTED");
    Object.defineProperty(app, "isPackaged", { value: true });
    app.getVersion = () => ${JSON.stringify(version)};
    app.getAppPath = () => ${JSON.stringify(installed)};
    app.setPath("userData", ${JSON.stringify(path.join(home, "profile"))});
    try {
      const { selectApplication } = await import(${JSON.stringify(pathToFileURL(path.join(codeRoot, "dist-electron/applicationLoader.js")).href)});
      trace("LOADER_IMPORTED");
      const entry = await selectApplication(${JSON.stringify(installed)}, ${JSON.stringify(home)}, true);
      trace("APPLICATION_SELECTED " + entry);
      await import(pathToFileURL(entry).href);
    } catch (error) { trace(String(error.stack)); console.error(error); app.exit(1); }
  `);
  const env = { ...process.env, LOOM_HOST_DATA_DIR: home, LOOM_ACCOUNT_API_BASE_URL: "http://127.0.0.1:1/v1" };
  const portProbe = net.createServer();
  await new Promise(resolve => portProbe.listen(0, "127.0.0.1", resolve));
  env.LOOM_HOST_DISCOVERY_PORT = String(portProbe.address().port);
  await new Promise(resolve => portProbe.close(resolve));
  await fs.mkdir(path.join(home, "profile"), { recursive: true });
  delete env.ELECTRON_RUN_AS_NODE; delete env.LOOM_DISABLE_AUTO_UPDATES;
  const child = spawn(require("electron"), [home, "--loom-host"], { env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  child.stdout.on("data", chunk => output += chunk); child.stderr.on("data", chunk => output += chunk);
  const timer = setTimeout(() => child.kill(), 25000);
  const code = await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", resolve); }).finally(() => clearTimeout(timer));
  output += await fs.readFile(path.join(home, "trace.txt"), "utf8").catch(() => "No trace");
  assert.equal(code, 0, output);
  assert.match(output, /APPLICATION_UPDATE_SMOKE_OK/);
  assert.equal(JSON.parse(await fs.readFile(path.join(root, "state.json"), "utf8")).active, 1);
  console.log("Downloaded application code, installed dependencies, sandboxed preload and health activation passed.");
} finally { await fs.rm(home, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }); }
