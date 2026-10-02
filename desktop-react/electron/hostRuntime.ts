import { app } from "electron";
import fs from "node:fs";
import path from "node:path";

export const EMBEDDED_HOST_RUNTIME_VERSION = "1.0.0";
export const EMBEDDED_HOST_RUNTIME_PROTOCOL = 1;
export const HOST_RUNTIME_SCHEMA = 1;

export type HostRuntimeSource = "managed" | "embedded" | "development" | "override";

export interface HostRuntimeManifest {
  schema: number;
  version: string;
  protocol: number;
  platform: string;
  arch: string;
  minBootstrapVersion?: string;
  sourceSha?: string;
  publishedAt?: string;
}

export interface HostRuntimeDescriptor extends HostRuntimeManifest {
  root: string;
  source: HostRuntimeSource;
}

export interface HostRuntimePointer {
  schema: number;
  version: string;
  activatedAt: string;
}

function safeJson<T>(filePath: string): T | null {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
  } catch {
    return null;
  }
}

function validVersion(value: unknown): value is string {
  return typeof value === "string" && /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(value.trim());
}

function validProtocol(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0 && Number(value) <= 1_000_000;
}

export function hostRuntimeManagerRoot(): string {
  return path.join(app.getPath("userData"), "host-runtime");
}

export function hostRuntimeVersionsRoot(): string {
  return path.join(hostRuntimeManagerRoot(), "versions");
}

export function hostRuntimePointerPath(): string {
  return path.join(hostRuntimeManagerRoot(), "current.json");
}

export function embeddedHostRuntimeRoot(repoRoot?: string): string {
  if (app.isPackaged) return process.resourcesPath;
  return path.resolve(repoRoot || process.cwd());
}

export function readHostRuntimeManifest(root: string): HostRuntimeManifest | null {
  const parsed = safeJson<Partial<HostRuntimeManifest>>(path.join(root, "manifest.json"));
  if (!parsed || parsed.schema !== HOST_RUNTIME_SCHEMA) return null;
  if (!validVersion(parsed.version) || !validProtocol(parsed.protocol)) return null;
  if (String(parsed.platform || "") !== "win32" || String(parsed.arch || "") !== "x64") return null;
  return {
    schema: HOST_RUNTIME_SCHEMA,
    version: parsed.version,
    protocol: parsed.protocol,
    platform: "win32",
    arch: "x64",
    minBootstrapVersion: String(parsed.minBootstrapVersion || "").trim() || undefined,
    sourceSha: String(parsed.sourceSha || "").trim() || undefined,
    publishedAt: String(parsed.publishedAt || "").trim() || undefined,
  };
}

function runtimeRootLooksUsable(root: string): boolean {
  if (!root || !fs.existsSync(root)) return false;
  if (process.platform !== "win32") return true;
  return fs.existsSync(path.join(root, "python.exe"))
    && fs.existsSync(path.join(root, "wxc-exec.exe"))
    && fs.existsSync(path.join(root, "browser-current-tab", "manifest.json"));
}

function descriptorFromRoot(root: string, source: HostRuntimeSource): HostRuntimeDescriptor | null {
  const manifest = readHostRuntimeManifest(root);
  if (!manifest || !runtimeRootLooksUsable(root)) return null;
  return { ...manifest, root, source };
}

function managedHostRuntime(): HostRuntimeDescriptor | null {
  if (!app.isPackaged || process.platform !== "win32") return null;
  const pointer = safeJson<Partial<HostRuntimePointer>>(hostRuntimePointerPath());
  if (!pointer || pointer.schema !== HOST_RUNTIME_SCHEMA || !validVersion(pointer.version)) return null;
  const descriptor = descriptorFromRoot(path.join(hostRuntimeVersionsRoot(), pointer.version), "managed");
  return descriptor?.version === pointer.version ? descriptor : null;
}

export function currentHostRuntime(repoRoot?: string): HostRuntimeDescriptor {
  const override = String(process.env.LOOM_HOST_RUNTIME_ROOT || "").trim();
  if (override) {
    const descriptor = descriptorFromRoot(path.resolve(override), "override");
    if (descriptor) return descriptor;
  }

  const managed = managedHostRuntime();
  if (managed) return managed;

  const embeddedRoot = embeddedHostRuntimeRoot(repoRoot);
  const embedded = readHostRuntimeManifest(embeddedRoot);
  if (embedded && runtimeRootLooksUsable(embeddedRoot)) {
    return { ...embedded, root: embeddedRoot, source: app.isPackaged ? "embedded" : "development" };
  }

  return {
    schema: HOST_RUNTIME_SCHEMA,
    version: EMBEDDED_HOST_RUNTIME_VERSION,
    protocol: EMBEDDED_HOST_RUNTIME_PROTOCOL,
    platform: process.platform,
    arch: process.arch,
    root: embeddedRoot,
    source: app.isPackaged ? "embedded" : "development",
  };
}

export function currentHostRuntimeVersion(repoRoot?: string): string {
  return currentHostRuntime(repoRoot).version;
}

export function currentHostRuntimeProtocol(repoRoot?: string): number {
  return currentHostRuntime(repoRoot).protocol;
}

export function resolveHostPythonExecutable(repoRoot: string): string {
  const configured = String(process.env.LOOM_PYTHON || "").trim();
  if (configured) return configured;

  const managedPython = path.join(currentHostRuntime(repoRoot).root, process.platform === "win32" ? "python.exe" : "python");
  if (fs.existsSync(managedPython)) return managedPython;

  const repoPython = process.platform === "win32"
    ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
    : path.join(repoRoot, ".venv", "bin", "python");
  if (fs.existsSync(repoPython)) return repoPython;
  return process.platform === "win32" ? "python" : "python3";
}

export function resolveHostBrowserExtensionRoot(repoRoot: string): string {
  const packaged = path.join(currentHostRuntime(repoRoot).root, "browser-current-tab");
  if (fs.existsSync(path.join(packaged, "manifest.json"))) return packaged;
  return path.join(repoRoot, "extensions", "browser-current-tab");
}

export function resolveHostSandboxExecutable(repoRoot: string): string | undefined {
  const executable = path.join(currentHostRuntime(repoRoot).root, "wxc-exec.exe");
  return fs.existsSync(executable) ? executable : undefined;
}

export function activateHostRuntime(version: string): HostRuntimePointer | null {
  if (!validVersion(version)) throw new Error(`Invalid Loom Host runtime version: ${version}`);
  const descriptor = descriptorFromRoot(path.join(hostRuntimeVersionsRoot(), version), "managed");
  if (!descriptor || descriptor.version !== version) throw new Error(`Loom Host runtime ${version} is incomplete or invalid.`);

  fs.mkdirSync(hostRuntimeManagerRoot(), { recursive: true });
  const previous = safeJson<HostRuntimePointer>(hostRuntimePointerPath());
  const pointer: HostRuntimePointer = { schema: HOST_RUNTIME_SCHEMA, version, activatedAt: new Date().toISOString() };
  const temp = `${hostRuntimePointerPath()}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify(pointer, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temp, hostRuntimePointerPath());
  return previous && previous.schema === HOST_RUNTIME_SCHEMA && validVersion(previous.version) ? previous : null;
}

export function restoreHostRuntimePointer(previous: HostRuntimePointer | null): void {
  const target = hostRuntimePointerPath();
  if (!previous) {
    try { fs.unlinkSync(target); } catch {}
    return;
  }
  fs.mkdirSync(hostRuntimeManagerRoot(), { recursive: true });
  const temp = `${target}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify(previous, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temp, target);
}
