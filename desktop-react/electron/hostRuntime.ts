import { app, BrowserWindow, ipcMain } from "electron";
import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { HostClient, HostServer, type HostHandler } from "./hostTransport.js";

export const HOST_ARG = "--loom-host";
export const isHostProcess = process.argv.includes(HOST_ARG) || process.argv.includes("--loom-background-host")
  || process.argv.some((arg) => /^loom:\/\//i.test(arg));
// Keep the existing account/session location in the Host. Only UI Chromium data
// moves to a separate folder so Electron can give the two processes distinct locks.
app.setName("Loom");
export const hostDataPath = process.env.LOOM_HOST_DATA_DIR
  ? path.resolve(process.env.LOOM_HOST_DATA_DIR) : app.getPath("userData");
if (isHostProcess) app.setPath("userData", hostDataPath);
if (!isHostProcess) app.setPath("userData", path.join(hostDataPath, "desktop-ui"));
const endpointId = crypto.createHash("sha256").update(hostDataPath).digest("hex").slice(0, 24);
const endpoint = process.platform === "win32" ? `\\\\.\\pipe\\loom-host-${endpointId}` : path.join(hostDataPath, "host.sock");
const credentialDirectory = path.join(hostDataPath, "host-ipc");
const sessionFile = path.join(credentialDirectory, "session.token");
const handlers = new Map<string, HostHandler>();
let server: HostServer | null = null;
let connection: Promise<void> | null = null;
let lastLaunch = 0;
const client = new HostClient((channel, value) => {
  if (channel === "loom:host-updating") { app.quit(); return; }
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      if (channel === "loom:host-disconnected") window.webContents.send("loom:notification", {
        jsonrpc: "2.0", method: "host/disconnected", params: {},
      });
      else window.webContents.send(channel, value);
    }
  }
});

function launch(args: string[]): void {
  const child = spawn(process.execPath, [...(app.isPackaged ? [] : [app.getAppPath()]), ...args], {
    detached: true, stdio: "ignore", windowsHide: true, env: { ...process.env },
  });
  child.on("error", (error) => console.error("Loom process launch failed", error.message));
  child.unref();
}

export function launchDesktop(): void {
  const index = process.argv.indexOf("--dev-url");
  const devArgs = process.argv.filter((arg) => arg.startsWith("--dev-url="));
  if (index >= 0 && process.argv[index + 1]) devArgs.push("--dev-url", process.argv[index + 1]);
  launch(devArgs);
}

function launchHost(): void {
  if (Date.now() - lastLaunch < 5000) return;
  lastLaunch = Date.now();
  launch([HOST_ARG]);
}

async function connectHost(): Promise<void> {
  if (connection) return connection;
  connection = (async () => {
    await app.whenReady();
    let lastError: unknown;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const token = (await fs.readFile(sessionFile, "utf8")).trim();
        if (!/^[a-f0-9]{64}$/.test(token)) throw new Error("Invalid Host credential");
        await client.connect(endpoint, token);
        return;
      } catch (error) {
        lastError = error;
        launchHost();
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    }
    throw new Error(`Could not start Loom Host: ${lastError instanceof Error ? lastError.message : String(lastError)}`);
  })().finally(() => { connection = null; });
  return connection;
}

export async function callHost(channel: string, args: unknown[] = []): Promise<unknown> {
  await connectHost();
  return client.call(channel, args);
}

export async function prepareDesktopHost(): Promise<void> {
  // Copy only browser preferences, never credentials or Electron lock files.
  const uiData = app.getPath("userData");
  const marker = path.join(uiData, ".host-ui-migrated");
  try { await fs.access(marker); } catch {
    await fs.mkdir(uiData, { recursive: true });
    for (const name of ["Local Storage", "IndexedDB", "Preferences"]) {
      try {
        await fs.access(path.join(uiData, name));
      } catch {
        try { await fs.cp(path.join(hostDataPath, name), path.join(uiData, name), { recursive: true }); } catch {}
      }
    }
    await fs.writeFile(marker, "1");
  }
  await connectHost();
}

export function handleHostChannel(channel: string, handler: (...args: any[]) => unknown): void {
  if (isHostProcess) handlers.set(channel, (args) => handler(undefined, ...args));
  else ipcMain.handle(channel, (_event, ...args) => callHost(channel, args));
}

export function broadcastHostEvent(channel: string, value: unknown): void {
  server?.broadcast(channel, value);
}

export async function startHostTransport(): Promise<void> {
  if (!isHostProcess || server) return;
  await fs.mkdir(hostDataPath, { recursive: true });
  await fs.mkdir(credentialDirectory, { recursive: true, mode: 0o700 });
  // safeStorage keys belong to a Chromium profile. The UI has a different
  // profile, so IPC uses an ephemeral credential in an OS-protected directory.
  // Actual account and provider credentials remain encrypted in the Host.
  if (process.platform === "win32") {
    const identity = spawnSync("whoami.exe", ["/user", "/fo", "csv", "/nh"], { encoding: "utf8", windowsHide: true });
    const sid = identity.stdout?.match(/S-1-5-\d+(?:-\d+)+/)?.[0];
    if (identity.status !== 0 || !sid) throw new Error("Cannot identify Host credential owner");
    const acl = spawnSync("icacls.exe", [credentialDirectory, "/inheritance:r", "/grant:r",
      `*${sid}:(OI)(CI)F`, "*S-1-5-18:(OI)(CI)F"], { encoding: "utf8", windowsHide: true });
    if (acl.status !== 0) throw new Error("Cannot protect Host credential directory");
  } else await fs.chmod(credentialDirectory, 0o700);
  if (process.platform !== "win32") await fs.rm(endpoint, { force: true });
  const token = crypto.randomBytes(32).toString("hex");
  const transport = new HostServer(token, handlers);
  await transport.listen(endpoint);
  if (process.platform !== "win32") await fs.chmod(endpoint, 0o600);
  const temporary = `${sessionFile}.${process.pid}.tmp`;
  await fs.writeFile(temporary, token, { mode: 0o600 });
  await fs.rename(temporary, sessionFile);
  server = transport;
}

app.on("before-quit", () => {
  client.close();
  server?.close();
  server = null;
});
