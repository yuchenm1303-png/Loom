// Run after build:electron. Uses isolated data and a private discovery port;
// never reads the user's account or changes packaged login-startup settings.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { waitForSmokeProcess } from "./host-smoke-process.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(new URL("../package.json", import.meta.url));
const executable = require("electron");
const packagedHost = String(process.env.LOOM_SMOKE_HOST_EXECUTABLE || "").trim();
const temp = await mkdtemp(path.join(os.tmpdir(), "loom-host-smoke-"));
const probe = net.createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const env = { ...process.env, LOOM_HOST_DATA_DIR: path.join(temp, "host"), LOOM_HOME: path.join(temp, "runtime"),
  LOOM_HOST_DISCOVERY_PORT: String(port), LOOM_ACCOUNT_API_BASE_URL: "http://127.0.0.1:1/v1" };
delete env.ELECTRON_RUN_AS_NODE;
for (const name of Object.keys(env)) {
  if (/^(MINIMAX_|OPENAI_|DASHSCOPE_|LOOM_(API_KEY|PRIMARY_API_KEY|ACCOUNT_MODEL_CREDENTIAL|PYTHON|MODEL|BASE_URL|PROVIDER))/.test(name)) {
    delete env[name];
  }
}
const children = [];
let hostOutput = "";
let hostError = null;
const host = spawn(packagedHost || executable, [...(packagedHost ? [] : [root]), "--loom-host"], { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
children.push(host);
host.stdout.on("data", (chunk) => { hostOutput += chunk; });
host.stderr.on("data", (chunk) => { hostOutput += chunk; });
host.on("error", (error) => { hostError = error; });
const statusUrl = `http://127.0.0.1:${port}/loom/status`;

async function waitHost() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (hostError) throw hostError;
    if (host.exitCode !== null || host.signalCode !== null) {
      throw new Error(`Host exited (code=${host.exitCode}, signal=${host.signalCode ?? "none"})`);
    }
    try {
      const response = await fetch(statusUrl, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return response.json();
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Host discovery did not become ready within 60s");
}

let clientNumber = 0;
let clientProgress = "";
async function runClient() {
  const label = `Host smoke client ${++clientNumber}`;
  clientProgress = path.join(temp, `client-${clientNumber}.log`);
  const script = path.join(temp, "client.mjs");
  await writeFile(script, `
    import { appendFileSync } from "node:fs";
    import { createRequire } from "node:module";
    const progress = (stage) => appendFileSync(${JSON.stringify(clientProgress)}, new Date().toISOString() + " " + stage + "\\n");
    progress("loading Electron client");
    const { app } = createRequire(${JSON.stringify(path.join(root, "package.json"))})("electron");
    const { callHost } = await import(${JSON.stringify(pathToFileURL(path.join(root, "dist-electron/hostProcess.js")).href)});
    app.whenReady().then(async () => { try {
      progress("connecting to Host IPC / update-status");
      const state = await callHost("loom:update-status");
      if (!state.currentVersion) throw new Error("Missing Host version");
      progress("Host IPC ready / initializing credential-free runtime");
      const initialization = await callHost("loom:connect");
      if (!initialization?.runtime) throw new Error("Credential-free local service did not initialize");
      progress("HOST_CLIENT_OK / quitting client");
      console.log("HOST_CLIENT_OK");
      app.quit();
    } catch (error) { progress(String(error?.stack || error)); console.error(error); app.exit(1); } });
  `);
  const child = spawn(executable, [script], { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  children.push(child);
  await waitForSmokeProcess(child, { label });
  const progress = await readFile(clientProgress, "utf8");
  assert.match(progress, /HOST_CLIENT_OK/);
  console.log(`${label}: credential-free runtime initialized and client exited normally.`);
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
} catch (error) {
  const progress = clientProgress ? await readFile(clientProgress, "utf8").catch(() => "(client did not write progress)") : "(client not started)";
  throw new Error(`${error?.stack || error}\nClient stages:\n${progress}\nHost output:\n${hostOutput.slice(-16_000) || "(no Host output)"}`, { cause: error });
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
