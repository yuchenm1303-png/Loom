import { app } from "electron";
import fs from "node:fs";
import path from "node:path";

export const EMBEDDED_HOST_RUNTIME_VERSION = "1.0.0";
export const EMBEDDED_HOST_RUNTIME_PROTOCOL = 1;
export const HOST_RUNTIME_SCHEMA = 1;

export type HostRuntimeSource = "embedded" | "development" | "override";

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

export function embeddedHostRuntimeRoot(repoRoot?: string): string {
  if (app.isPackaged) return process.resourcesPath;
  return path.resolve(repoRoot || process.cwd());
}

export function readHostRuntimeManifest(root: string): HostRuntimeManifest | null {
  const parsed = safeJson<Partial<HostRuntimeManifest>>(path.join(root, "manifest.json"));
  if (!parsed || parsed.schema !== HOST_RUNTIME_SCHEMA) return null;
  if (!validVersion(parsed.version) || !validProtocol(parsed.protocol)) return null;
  // Never load a runtime built for another operating system or CPU architecture.
  if (parsed.platform !== process.platform || parsed.arch !== process.arch) return null;
  return {
    schema: HOST_RUNTIME_SCHEMA,
    version: parsed.version,
    protocol: parsed.protocol,
    platform: parsed.platform,
    arch: parsed.arch,
    minBootstrapVersion: String(parsed.minBootstrapVersion || "").trim() || undefined,
    sourceSha: String(parsed.sourceSha || "").trim() || undefined,
    publishedAt: String(parsed.publishedAt || "").trim() || undefined,
  };
}

function runtimeRootLooksUsable(root: string): boolean {
  if (!root || !fs.existsSync(root)) return false;
  const python = path.join(root, process.platform === "win32" ? "python.exe" : "python");
  if (!fs.existsSync(python)) return false;
  if (!fs.existsSync(path.join(root, "browser-current-tab", "manifest.json"))) return false;
  // MXC is a required Windows-only sandbox helper. macOS has no MXC binary.
  return process.platform !== "win32" || fs.existsSync(path.join(root, "wxc-exec.exe"));
}

function descriptorFromRoot(root: string, source: HostRuntimeSource): HostRuntimeDescriptor | null {
  const manifest = readHostRuntimeManifest(root);
  if (!manifest || !runtimeRootLooksUsable(root)) return null;
  return { ...manifest, root, source };
}

export function currentHostRuntime(repoRoot?: string): HostRuntimeDescriptor {
  const override = String(process.env.LOOM_HOST_RUNTIME_ROOT || "").trim();
  if (override && !app.isPackaged) {
    const descriptor = descriptorFromRoot(path.resolve(override), "override");
    if (descriptor) return descriptor;
  }

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
  if (configured && !app.isPackaged) return configured;

  const runtimePython = path.join(currentHostRuntime(repoRoot).root, process.platform === "win32" ? "python.exe" : "python");
  if (fs.existsSync(runtimePython)) return runtimePython;
  if (app.isPackaged) {
    throw new Error("Loom installation is incomplete: its bundled local runtime is missing. Reinstall Loom.");
  }

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
