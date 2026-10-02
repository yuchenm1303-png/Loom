// Run after build:electron. Uses isolated data and a private discovery port;
// never reads the user's account or changes packaged login-startup settings.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(new URL("../package.json", import.meta.url));
const executable = require("electron");
const temp = await mkdtemp(path.join(os.tmpdir(), "loom-host-smoke-"));
const probe = net.createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const env = { ...process.env, LOOM_HOST_DATA_DIR: path.join(temp, "host"), LOOM_HOME: path.join(temp, "runtime"),
  LOOM_HOST_DISCOVERY_PORT: String(port), LOOM_ACCOUNT_API_BASE_URL: "http://127.0.0.1:1/v1" };
delete env.ELECTRON_RUN_AS_NODE;
const children = [];
let hostOutput = "";
const host = spawn(executable, [root, "--loom-host"], { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
children.push(host);
host.stdout.on("data", (chunk) => { hostOutput += chunk; });
host.stderr.on("data", (chunk) => { hostOutput += chunk; });
host.on("error", (error) => { hostOutput += error.message; });
const statusUrl = `http://127.0.0.1:${port}/loom/status`;

async function waitHost() {
  for (let attempt = 0; attempt < 80; attempt++) {
    if (host.exitCode !== null) throw new Error(`Host exited: ${hostOutput}`);
    try {
      const response = await fetch(statusUrl);
      if (response.ok) return response.json();
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Host did not start: ${hostOutput}`);
}

async function runClient() {
  const script = path.join(temp, "client.mjs");
  await writeFile(script, `
    import { createRequire } from "node:module";
    const { app } = createRequire(${JSON.stringify(path.join(root, "package.json"))})("electron");
    const { callHost } = await import(${JSON.stringify(pathToFileURL(path.join(root, "dist-electron/hostRuntime.js")).href)});
    app.whenReady().then(async () => { try {
      const state = await callHost("loom:update-status");
      if (!state.currentVersion) throw new Error("Missing Host version");
      console.log("HOST_CLIENT_OK");
      app.quit();
    } catch (error) { console.error(error); app.exit(1); } });
  `);
  const child = spawn(executable, [script], { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  children.push(child);
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  const timeout = setTimeout(() => child.kill(), 25000);
  try {
    const code = await new Promise((resolve, reject) => { child.on("error", reject); child.on("exit", resolve); });
    assert.equal(code, 0, output);
    assert.match(output, /HOST_CLIENT_OK/);
  } finally { clearTimeout(timeout); }
}

try {
  const before = await waitHost();
  assert.equal(before.hostPid, host.pid);
  assert.equal(before.desktopRequired, false);
  await runClient();
  const after = await (await fetch(statusUrl)).json();
  assert.equal(after.hostPid, before.hostPid, "Client exit must not stop or replace Host");
  await runClient();
  assert.equal((await (await fetch(statusUrl)).json()).hostPid, before.hostPid);
  console.log("Host starts independently, survives client exit, and accepts a new client.");
} finally {
  for (const child of children) {
    if (child.exitCode === null && child.signalCode === null && child.pid) {
      const exited = new Promise((resolve) => {
        const timeout = setTimeout(resolve, 5000);
        child.once("exit", () => { clearTimeout(timeout); resolve(); });
      });
      // Electron's GPU/utility children must also be removed in this isolated
      // Windows test; terminating only the parent can leave them behind.
      if (process.platform === "win32") spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
      else child.kill();
      await exited;
    }
  }
  await rm(temp, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
}
