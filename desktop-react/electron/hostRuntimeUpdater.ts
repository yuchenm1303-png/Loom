import { app, net } from "electron";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { isHostProcess } from "./hostProcess.js";
import {
  activateHostRuntime,
  compareHostRuntimeBuilds,
  currentHostRuntime,
  hostRuntimeManagerRoot,
  hostRuntimeVersionsRoot,
  readHostRuntimeManifest,
  restoreHostRuntimePointer,
} from "./hostRuntime.js";

export type HostRuntimeUpdatePhase =
  | "disabled"
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "ready"
  | "activating"
  | "up-to-date"
  | "incompatible"
  | "error";

export interface HostRuntimeUpdateState {
  enabled: boolean;
  phase: HostRuntimeUpdatePhase;
  currentVersion: string;
  currentProtocol: number;
  availableVersion?: string;
  availableProtocol?: number;
  requiredProtocol?: number;
  requiredBootstrapVersion?: string;
  percent?: number;
  checkedAt?: string;
  error?: string;
}

interface HostRuntimeChannel {
  schema: number;
  channel: "stable" | string;
  version: string;
  protocol: number;
  minBootstrapVersion: string;
  url: string;
  sha256: string;
  size: number;
  sourceSha?: string;
  publishedAt?: string;
}

export interface HostRuntimeUpdateHooks {
  canActivate: () => boolean | Promise<boolean>;
  reload: () => void | Promise<void>;
}

const CHANNEL_URL = "https://raw.githubusercontent.com/yuchenm1303-png/Loom/main/host-runtime/stable.json";
const STARTUP_CHECK_DELAY_MS = 7_000;
const PERIODIC_CHECK_INTERVAL_MS = 30 * 60_000;
const READY_RETRY_MS = 30_000;
const MAX_CHANNEL_BYTES = 64 * 1024;
const MAX_RUNTIME_BYTES = 512 * 1024 * 1024;

const enabled = isHostProcess && app.isPackaged && process.platform === "win32"
  && process.env.LOOM_DISABLE_AUTO_UPDATES !== "1";
const execFileAsync = promisify(execFile);
let hooks: HostRuntimeUpdateHooks | null = null;
let checkPromise: Promise<HostRuntimeUpdateState> | null = null;
let readyVersion = "";
let retryTimer: NodeJS.Timeout | null = null;
let periodicTimer: NodeJS.Timeout | null = null;
let startupTimer: NodeJS.Timeout | null = null;
let state: HostRuntimeUpdateState = initialState();

function initialState(): HostRuntimeUpdateState {
  const runtime = currentHostRuntime();
  return {
    enabled,
    phase: enabled ? "idle" : "disabled",
    currentVersion: runtime.version,
    currentProtocol: runtime.protocol,
  };
}

function snapshot(): HostRuntimeUpdateState {
  const runtime = currentHostRuntime();
  state = { ...state, enabled, currentVersion: runtime.version, currentProtocol: runtime.protocol };
  return { ...state };
}

function setState(patch: Partial<HostRuntimeUpdateState>): HostRuntimeUpdateState {
  state = { ...state, ...patch };
  return snapshot();
}

export function hostRuntimeUpdateState(): HostRuntimeUpdateState {
  return snapshot();
}

export function registerHostRuntimeUpdateHooks(next: HostRuntimeUpdateHooks): void {
  hooks = next;
  if (state.phase === "ready") void maybeActivateReadyRuntime();
}

function numericVersion(value: string): number[] {
  return String(value || "").split(/[+-]/, 1)[0].split(".").map((part) => Number(part) || 0).slice(0, 3);
}

function compareVersions(left: string, right: string): number {
  const a = numericVersion(left);
  const b = numericVersion(right);
  for (let index = 0; index < 3; index += 1) {
    const delta = (a[index] || 0) - (b[index] || 0);
    if (delta) return delta > 0 ? 1 : -1;
  }
  return 0;
}

function validChannel(value: unknown): HostRuntimeChannel {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid Loom Host update channel.");
  const raw = value as Partial<HostRuntimeChannel>;
  const version = String(raw.version || "").trim();
  const url = String(raw.url || "").trim();
  const sha256 = String(raw.sha256 || "").trim().toLowerCase();
  const minBootstrapVersion = String(raw.minBootstrapVersion || "").trim();
  const protocol = Number(raw.protocol);
  const size = Number(raw.size);
  if (raw.schema !== 1 || !/^\d+\.\d+\.\d+$/.test(version)) throw new Error("Invalid Loom Host runtime version in channel.");
  if (!Number.isInteger(protocol) || protocol < 0 || protocol > 1_000_000) throw new Error("Invalid Loom Host protocol in channel.");
  if (!/^\d+\.\d+\.\d+$/.test(minBootstrapVersion)) throw new Error("Invalid Loom Host bootstrap requirement.");
  if (!/^https:\/\/github\.com\/yuchenm1303-png\/Loom\/releases\/download\/host-v[^/]+\/[^?#]+\.zip$/.test(url)) {
    throw new Error("Loom Host update URL is not trusted.");
  }
  if (!/^[a-f0-9]{64}$/.test(sha256)) throw new Error("Invalid Loom Host update checksum.");
  if (!Number.isSafeInteger(size) || size <= 0 || size > MAX_RUNTIME_BYTES) throw new Error("Invalid Loom Host update size.");
  return {
    schema: 1,
    channel: String(raw.channel || "stable"),
    version,
    protocol,
    minBootstrapVersion,
    url,
    sha256,
    size,
    sourceSha: String(raw.sourceSha || "").trim() || undefined,
    publishedAt: String(raw.publishedAt || "").trim() || undefined,
  };
}

async function fetchBytes(url: string, maxBytes: number): Promise<Buffer> {
  const response = await net.fetch(url, { cache: "no-store", redirect: "follow" });
  if (!response.ok) throw new Error(`Loom Host update request failed (${response.status}).`);
  const declared = Number(response.headers.get("content-length") || 0);
  if (declared > maxBytes) throw new Error("Loom Host update response is unexpectedly large.");
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.byteLength > maxBytes) throw new Error("Loom Host update response is unexpectedly large.");
  return bytes;
}

async function fetchChannel(): Promise<HostRuntimeChannel | null> {
  const custom = String(process.env.LOOM_HOST_UPDATE_CHANNEL_URL || "").trim();
  const url = custom || CHANNEL_URL;
  const bytes = await fetchBytes(url, MAX_CHANNEL_BYTES);
  const raw = JSON.parse(bytes.toString("utf8")) as unknown;
  if (raw && typeof raw === "object" && !Array.isArray(raw) && !String((raw as Record<string, unknown>).url || "").trim()) {
    return null;
  }
  return validChannel(raw);
}

function sha256(bytes: Buffer): string {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function runtimeDownloadPath(version: string): string {
  return path.join(hostRuntimeManagerRoot(), "downloads", `Loom-Host-Runtime-${version}-win-x64.zip`);
}

function runtimeStagingPath(version: string): string {
  return path.join(hostRuntimeManagerRoot(), "staging", `${version}-${process.pid}-${Date.now()}`);
}

async function expandArchive(archive: string, destination: string): Promise<void> {
  await fs.promises.mkdir(destination, { recursive: true });
  const script = "& { param($src,$dst) Expand-Archive -LiteralPath $src -DestinationPath $dst -Force }";
  await execFileAsync("powershell.exe", [
    "-NoLogo",
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    script,
    archive,
    destination,
  ], { encoding: "utf8", windowsHide: true, timeout: 120_000 });
}

async function selfTestRuntime(root: string): Promise<void> {
  const python = path.join(root, "python.exe");
  await execFileAsync(python, ["self-test"], {
    cwd: root,
    encoding: "utf8",
    windowsHide: true,
    timeout: 120_000,
  });
}

async function prepareRuntime(channel: HostRuntimeChannel): Promise<void> {
  setState({
    phase: "downloading",
    availableVersion: channel.version,
    availableProtocol: channel.protocol,
    percent: 0,
    error: undefined,
  });
  await fs.promises.mkdir(path.dirname(runtimeDownloadPath(channel.version)), { recursive: true });
  const bytes = await fetchBytes(channel.url, MAX_RUNTIME_BYTES);
  if (bytes.byteLength !== channel.size) throw new Error("Loom Host runtime size does not match the published manifest.");
  if (sha256(bytes) !== channel.sha256) throw new Error("Loom Host runtime checksum verification failed.");
  await fs.promises.writeFile(runtimeDownloadPath(channel.version), bytes, { mode: 0o600 });
  setState({ percent: 65 });

  const staging = runtimeStagingPath(channel.version);
  await fs.promises.rm(staging, { recursive: true, force: true });
  await expandArchive(runtimeDownloadPath(channel.version), staging);
  const manifest = readHostRuntimeManifest(staging);
  if (!manifest || manifest.version !== channel.version || manifest.protocol !== channel.protocol) {
    await fs.promises.rm(staging, { recursive: true, force: true });
    throw new Error("Loom Host runtime manifest does not match the update channel.");
  }
  if (!fs.existsSync(path.join(staging, "wxc-exec.exe")) || !fs.existsSync(path.join(staging, "browser-current-tab", "manifest.json"))) {
    await fs.promises.rm(staging, { recursive: true, force: true });
    throw new Error("Loom Host runtime bundle is incomplete.");
  }
  await selfTestRuntime(staging);
  setState({ percent: 90 });

  const target = path.join(hostRuntimeVersionsRoot(), channel.version);
  await fs.promises.mkdir(hostRuntimeVersionsRoot(), { recursive: true });
  await fs.promises.rm(target, { recursive: true, force: true });
  await fs.promises.rename(staging, target);
  try { await fs.promises.unlink(runtimeDownloadPath(channel.version)); } catch {}
  readyVersion = channel.version;
  setState({ phase: "ready", percent: 100, error: undefined });
}

function scheduleReadyRetry(): void {
  if (retryTimer || state.phase !== "ready") return;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    void maybeActivateReadyRuntime();
  }, READY_RETRY_MS);
  retryTimer.unref?.();
}

async function pruneOldRuntimes(keep: Set<string>): Promise<void> {
  try {
    const root = hostRuntimeVersionsRoot();
    for (const name of await fs.promises.readdir(root)) {
      if (keep.has(name)) continue;
      const manifest = readHostRuntimeManifest(path.join(root, name));
      if (!manifest) continue;
      await fs.promises.rm(path.join(root, name), { recursive: true, force: true });
    }
  } catch {}
}

async function maybeActivateReadyRuntime(): Promise<boolean> {
  if (!readyVersion || state.phase !== "ready") return false;
  if (!hooks) {
    scheduleReadyRetry();
    return false;
  }
  let safe = false;
  try { safe = await hooks.canActivate(); } catch { safe = false; }
  if (!safe) {
    scheduleReadyRetry();
    return false;
  }

  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  const before = currentHostRuntime();
  setState({ phase: "activating", error: undefined });
  const previousPointer = activateHostRuntime(readyVersion);
  try {
    await hooks.reload();
    const current = currentHostRuntime();
    readyVersion = "";
    await pruneOldRuntimes(new Set([current.version, before.version]));
    setState({
      phase: "up-to-date",
      currentVersion: current.version,
      currentProtocol: current.protocol,
      availableVersion: undefined,
      availableProtocol: undefined,
      percent: undefined,
      error: undefined,
    });
    return true;
  } catch (error) {
    restoreHostRuntimePointer(previousPointer);
    try { await hooks.reload(); } catch {}
    const message = error instanceof Error ? error.message : String(error);
    readyVersion = "";
    setState({ phase: "error", error: `Runtime activation rolled back: ${message}`, percent: undefined });
    return false;
  }
}

export async function ensureHostRuntimeUpdate(requiredProtocol = 0): Promise<HostRuntimeUpdateState> {
  if (!enabled) return hostRuntimeUpdateState();
  if (checkPromise) return checkPromise;
  checkPromise = (async () => {
    const current = currentHostRuntime();
    setState({
      phase: "checking",
      requiredProtocol: Math.max(0, Number(requiredProtocol) || 0),
      checkedAt: new Date().toISOString(),
      error: undefined,
    });
    try {
      const channel = await fetchChannel();
      if (!channel) return setState({ phase: "up-to-date", error: undefined });
      if (compareVersions(app.getVersion(), channel.minBootstrapVersion) < 0) {
        return setState({
          phase: "incompatible",
          availableVersion: channel.version,
          availableProtocol: channel.protocol,
          requiredBootstrapVersion: channel.minBootstrapVersion,
          error: undefined,
        });
      }
      const buildOrder = compareHostRuntimeBuilds(channel, current);
      const needsVersion = buildOrder !== null ? buildOrder > 0 : compareVersions(channel.version, current.version) > 0;
      const needsProtocol = current.protocol < requiredProtocol;
      if (!needsVersion && !needsProtocol) {
        return setState({
          phase: "up-to-date",
          availableVersion: undefined,
          availableProtocol: undefined,
          requiredBootstrapVersion: undefined,
          percent: undefined,
          error: undefined,
        });
      }
      if (channel.protocol < requiredProtocol) {
        return setState({
          phase: "incompatible",
          availableVersion: channel.version,
          availableProtocol: channel.protocol,
          error: `The stable Loom Host runtime does not yet satisfy protocol ${requiredProtocol}.`,
        });
      }
      setState({ phase: "available", availableVersion: channel.version, availableProtocol: channel.protocol, error: undefined });
      await prepareRuntime(channel);
      await maybeActivateReadyRuntime();
      return hostRuntimeUpdateState();
    } catch (error) {
      return setState({ phase: "error", error: error instanceof Error ? error.message : String(error), percent: undefined });
    }
  })().finally(() => { checkPromise = null; });
  return checkPromise;
}

function startAutomaticRuntimeUpdates(): void {
  if (!enabled || startupTimer || periodicTimer) return;
  startupTimer = setTimeout(() => {
    startupTimer = null;
    void ensureHostRuntimeUpdate();
  }, STARTUP_CHECK_DELAY_MS);
  startupTimer.unref?.();
  periodicTimer = setInterval(() => { void ensureHostRuntimeUpdate(); }, PERIODIC_CHECK_INTERVAL_MS);
  periodicTimer.unref?.();
}

app.whenReady().then(startAutomaticRuntimeUpdates);
app.on("before-quit", () => {
  if (startupTimer) clearTimeout(startupTimer);
  if (periodicTimer) clearInterval(periodicTimer);
  if (retryTimer) clearTimeout(retryTimer);
  startupTimer = null;
  periodicTimer = null;
  retryTimer = null;
});
