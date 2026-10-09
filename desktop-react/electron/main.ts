import { app, BrowserWindow, clipboard, dialog, ipcMain, nativeImage, nativeTheme, protocol, shell } from "electron";
import { ChildProcessWithoutNullStreams, spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import fsSync from "node:fs";
import crypto from "node:crypto";
import readline from "node:readline";
import { syncExtensionInstall } from "./browserExtensionAssets.js";
import {
  DesktopModelManager,
  type AddModelInput,
  type EditModelInput,
  type ModelLaunchSpec,
  type ModelProfile,
} from "./modelManager.js";
import { LoomAccountClient, type LoomAccountSnapshot, type LoomAuthCapabilities, type LoomAuthChallenge, type LoomModelPolicyAccess } from "./accountClient.js";
import { accountErrorPayload, type AccountErrorPayload } from "./accountErrors.js";
import { latestSync } from "./latestSync.js";
import { runtimeModelParams, runtimeModelArguments } from "./runtimeModelConfig.js";
import { startupModel } from "./startupModel.js";
import { closeHudOverlayWindow, createHudOverlayWindow, sendHudUpdate } from "./hudWindow.js";
import {
  sendRelayNotification,
  startWebRelay,
  stopWebRelay,
  type WebRelayOperations,
} from "./remoteRelay.js";
import { webRelayAuthPayload } from "./webRelayAuth.js";
import { hostAccount } from "./hostAccount.js";
import { createSearchRelay } from "./searchRelay.js";
import { broadcastHostEvent, desktopPidFile, handleHostChannel, isHostProcess, prepareDesktopHost, startHostTransport } from "./hostProcess.js";
import { registerHeadlessUpdateGuard } from "./updater.js";
import {
  currentHostRuntimeVersion,
  resolveHostBrowserExtensionRoot,
  resolveHostPythonExecutable,
  resolveHostSandboxExecutable,
} from "./hostRuntime.js";
import { registerHostRuntimeUpdateHooks } from "./hostRuntimeUpdater.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const DEV_WINDOW_ICON = path.join(DESKTOP_ROOT, "build", "icon.png");
const HTML_ESCAPE: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const APP_SERVER_CONNECT_TIMEOUT_MS = 15_000;
const APP_SERVER_CALL_TIMEOUT_MS = 120_000;

type DiagnosticLogKind = "computer" | "browser";

const LOCAL_IMAGE_MIME_TYPES = new Map<string, string>([
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".gif", "image/gif"],
  [".webp", "image/webp"],
  [".bmp", "image/bmp"],
]);
const MAX_INLINE_IMAGE_BYTES = 20 * 1024 * 1024;
const LOCAL_MEDIA_MIME_TYPES = new Map<string, string>([
  [".mp3", "audio/mpeg"],
  [".wav", "audio/wav"],
  [".m4a", "audio/mp4"],
  [".aac", "audio/aac"],
  [".flac", "audio/flac"],
  [".ogg", "audio/ogg"],
  [".opus", "audio/ogg"],
  [".mp4", "video/mp4"],
  [".webm", "video/webm"],
  [".mov", "video/quicktime"],
  [".m4v", "video/mp4"],
]);
const MAX_INLINE_MEDIA_BYTES = 32 * 1024 * 1024;
const ARTIFACT_MIME_TYPES = new Map<string, string>([
  [".html", "text/html; charset=utf-8"],
  [".htm", "text/html; charset=utf-8"],
  [".svg", "image/svg+xml; charset=utf-8"],
  [".pdf", "application/pdf"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".mjs", "text/javascript; charset=utf-8"],
  [".cjs", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".txt", "text/plain; charset=utf-8"],
  [".md", "text/markdown; charset=utf-8"],
  [".markdown", "text/markdown; charset=utf-8"],
  [".csv", "text/csv; charset=utf-8"],
  [".log", "text/plain; charset=utf-8"],
  [".xml", "application/xml; charset=utf-8"],
  [".ts", "text/plain; charset=utf-8"],
  [".tsx", "text/plain; charset=utf-8"],
  [".jsx", "text/plain; charset=utf-8"],
  [".py", "text/plain; charset=utf-8"],
  [".go", "text/plain; charset=utf-8"],
  [".rs", "text/plain; charset=utf-8"],
  [".java", "text/plain; charset=utf-8"],
  [".kt", "text/plain; charset=utf-8"],
  [".scss", "text/css; charset=utf-8"],
  [".less", "text/css; charset=utf-8"],
  [".sql", "text/plain; charset=utf-8"],
  [".sh", "text/plain; charset=utf-8"],
  [".bash", "text/plain; charset=utf-8"],
  [".ps1", "text/plain; charset=utf-8"],
  [".yaml", "text/plain; charset=utf-8"],
  [".yml", "text/plain; charset=utf-8"],
  [".toml", "text/plain; charset=utf-8"],
  [".wasm", "application/wasm"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
  [".ttf", "font/ttf"],
  [".otf", "font/otf"],
  ...LOCAL_IMAGE_MIME_TYPES,
  ...LOCAL_MEDIA_MIME_TYPES,
]);
const LOCAL_BROWSER_ARTIFACT_SUFFIXES = new Set([
  ".html", ".htm", ".svg", ".pdf",
  ".txt", ".md", ".markdown", ".csv", ".log", ".json", ".xml", ".css",
  ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".go", ".rs",
  ".java", ".kt", ".scss", ".less", ".sql", ".sh", ".bash", ".ps1",
  ".yaml", ".yml", ".toml",
  ...LOCAL_IMAGE_MIME_TYPES.keys(),
  ...LOCAL_MEDIA_MIME_TYPES.keys(),
]);

protocol.registerSchemesAsPrivileged([
  {
    scheme: "loom-artifact",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
    },
  },
]);

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id?: number | string | null;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
  method?: string;
  params?: Record<string, unknown>;
}

interface RuntimeStatus {
  activeThreadIds?: unknown[];
  reasoning?: { kind: string; value: string } | null;
}

interface ModelRestartResult {
  initialization: unknown;
  models: ReturnType<DesktopModelManager["snapshot"]>;
  hotSwitch?: boolean;
  thread?: Record<string, unknown>;
}

interface ReasoningUpdateResult {
  runtime: unknown;
  models: ReturnType<DesktopModelManager["snapshot"]>;
}

function resolvePythonExecutable(): string {
  return resolveHostPythonExecutable(REPO_ROOT);
}

function appendPythonPath(existing: string | undefined): string {
  return [REPO_ROOT, existing]
    .filter((value): value is string => Boolean(value && value.trim()))
    .join(path.delimiter);
}

function initializationFromRuntime(payload: unknown): unknown {
  if (payload && typeof payload === "object" && "runtime" in payload) return payload;
  return { runtime: payload };
}

function missingHotSwitchMethod(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes("Method not found: runtime/set_model");
}

function computerLogRoot(): string {
  const configured = process.env.LOOM_COMPUTER_LOG_DIR?.trim();
  return configured ? path.resolve(configured) : path.join(REPO_ROOT, ".loom", "logs", "computer-use");
}

function browserLogRoot(): string {
  const configured = process.env.LOOM_BROWSER_LOG_DIR?.trim() || process.env.LOOM_BROWSER_DIAG_DIR?.trim();
  return configured ? path.resolve(configured) : path.join(REPO_ROOT, ".loom", "logs", "browser-use");
}

function loomRuntimeHome(): string {
  // Mirrors _runtime_home() in browser_extension_bridge.py. Both sides have to
  // resolve the same folder or they pair the extension against different tokens,
  // which is invisible until the browser reports a working extension as broken.
  const configured = String(process.env.LOOM_HOME || "").trim();
  return configured ? path.resolve(configured) : path.join(app.getPath("home"), ".loom");
}

function browserBridgeTokenPath(): string {
  // Deliberately not userData: that directory is named after the build, so the
  // packaged app and an unpackaged Electron run keep separate tokens, and a
  // Python server started from a checkout reads a third one from ~/.loom. The
  // extension can only be paired with one of them, so the other two look like a
  // broken extension. This is the path the Python bridge already defaults to.
  return path.join(loomRuntimeHome(), "browser", "current-tab-bridge.token");
}

function ensureBrowserBridgeToken(): string {
  const target = browserBridgeTokenPath();
  try {
    const existing = fsSync.readFileSync(target, "utf8").trim();
    if (existing.length >= 32) return existing;
  } catch {}
  fsSync.mkdirSync(path.dirname(target), { recursive: true });
  const token = crypto.randomBytes(48).toString("base64url");
  fsSync.writeFileSync(target, token, { encoding: "utf8", mode: 0o600 });
  return token;
}

function unpackedExtensionId(absolutePath: string): string {
  // Chromium derives an unpacked extension's id from its absolute path: the
  // first 16 bytes of SHA-256 over the path, each nibble mapped to a-p. Surfacing
  // it lets Settings name the exact row in edge://extensions, so "the version
  // never changes" stops being something the user has to investigate - a mismatch
  // means the browser is loading a different folder than the one Loom maintains.
  const digest = crypto.createHash("sha256").update(Buffer.from(absolutePath, "utf16le")).digest("hex");
  return [...digest.slice(0, 32)].map((c) => String.fromCharCode(97 + parseInt(c, 16))).join("");
}

function browserExtensionSource(): string {
  return resolveHostBrowserExtensionRoot(REPO_ROOT);
}

function browserExtensionTarget(): string {
  // Keep the install folder stable across dev Electron and packaged Loom. The
  // absolute path is important: Chromium's unpacked-extension picker does not
  // expand `~`, and app.getPath("userData") is named "Electron" in development.
  return path.join(loomRuntimeHome(), "browser", "current-tab-extension");
}

function legacyBrowserExtensionTargets(): string[] {
  const stable = path.resolve(browserExtensionTarget());
  const appData = app.getPath("appData");
  // Chromium keys an unpacked extension by its absolute path, so an install left
  // in an older location keeps loading its own copy forever - it cannot be
  // upgraded in place from somewhere else, only kept in sync. Each build only
  // knows its own userData name, so listing the historical names explicitly is
  // what lets the packaged app repair an install left behind by a dev run and
  // the other way round. Whichever folder the browser actually loaded then has
  // the current code and the same token.
  const candidates = [
    path.join(app.getPath("userData"), "browser", "current-tab-extension"),
    path.join(appData, "Electron", "browser", "current-tab-extension"),
    path.join(appData, "Loom", "browser", "current-tab-extension"),
  ];
  return [...new Set(candidates.map((candidate) => path.resolve(candidate)))].filter((candidate) => (
    candidate !== stable && fsSync.existsSync(path.join(candidate, "manifest.json"))
  ));
}

async function syncBrowserExtensionAssets(): Promise<{ target: string; version: string; installTargets: string[] }> {
  const source = browserExtensionSource();
  const target = browserExtensionTarget();
  if (!fsSync.existsSync(path.join(source, "manifest.json"))) {
    throw new Error(`Packaged browser extension is missing: ${source}`);
  }
  const manifest = JSON.parse(await fs.readFile(path.join(source, "manifest.json"), "utf8")) as { version?: string };
  const version = String(manifest.version || "");
  const installTargets = [target, ...legacyBrowserExtensionTargets()];
  const bridgeConfig = JSON.stringify({ bridgeUrl: "http://127.0.0.1:39222", token: ensureBrowserBridgeToken() });
  const updateSignal = JSON.stringify({ token: crypto.randomUUID(), version });
  for (const installTarget of installTargets) {
    await syncExtensionInstall(source, installTarget, bridgeConfig, updateSignal);
  }
  return { target, version, installTargets };
}

async function setupBrowserExtension(browser: "edge" | "chrome" = "edge", extensionConnected = false): Promise<Record<string, unknown>> {
  const { target, version, installTargets } = await syncBrowserExtensionAssets();
  if (!extensionConnected) clipboard.writeText(target);
  const folderError = extensionConnected ? "" : await shell.openPath(target);
  const managementUrl = browser === "chrome" ? "chrome://extensions" : "edge://extensions";
  const roots = browser === "chrome"
    ? [
        path.join(process.env.PROGRAMFILES || "", "Google", "Chrome", "Application", "chrome.exe"),
        path.join(process.env["PROGRAMFILES(X86)"] || "", "Google", "Chrome", "Application", "chrome.exe"),
        path.join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
      ]
    : [
        path.join(process.env["PROGRAMFILES(X86)"] || "", "Microsoft", "Edge", "Application", "msedge.exe"),
        path.join(process.env.PROGRAMFILES || "", "Microsoft", "Edge", "Application", "msedge.exe"),
        path.join(process.env.LOCALAPPDATA || "", "Microsoft", "Edge", "Application", "msedge.exe"),
      ];
  const executable = roots.find((candidate) => candidate && fsSync.existsSync(candidate));
  let openError = "";
  if (!extensionConnected && executable) {
    const child = spawn(executable, [managementUrl], { detached: true, stdio: "ignore", windowsHide: false });
    child.unref();
  } else if (!extensionConnected) {
    openError = await shell.openExternal(managementUrl).then(() => "", (error) => String(error));
  }
  return {
    ok: true,
    desiredVersion: version,
    manualInstallRequired: !extensionConnected,
    automaticUpdateRequested: extensionConnected,
    migratedLegacyInstalls: Math.max(0, installTargets.length - 1),
    // Every folder that now holds this version paired with this token, each with
    // the id the browser will show for it. Any of them is a working install.
    installedPaths: installTargets.map((installTarget) => ({
      path: installTarget,
      extensionId: unpackedExtensionId(path.resolve(installTarget)),
      primary: path.resolve(installTarget) === path.resolve(target),
    })),
    extensionPath: target,
    pathCopied: !extensionConnected,
    folderOpened: extensionConnected || !folderError,
    folderError,
    managementUrl,
    openError,
  };
}

function diagnosticLogRoot(kind: DiagnosticLogKind): string {
  return kind === "browser" ? browserLogRoot() : computerLogRoot();
}

function diagnosticLabel(kind: DiagnosticLogKind): string {
  return kind === "browser" ? "Browser Use" : "Computer Use";
}

function diagnosticArchiveStem(kind: DiagnosticLogKind): string {
  return kind === "browser" ? "loom-browser-use-logs" : "loom-computer-use-logs";
}

function timestampSlug(): string {
  return new Date().toISOString().replace(/[:.]/g, "-").replace("T", "_").replace("Z", "");
}

function parseLastJsonLine(stdout: string): Record<string, unknown> {
  const lines = String(stdout || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const last = lines.length ? lines[lines.length - 1] : "{}";
  const parsed = JSON.parse(last);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
}

async function exportDiagnosticLogs(kind: DiagnosticLogKind): Promise<Record<string, unknown>> {
  const logDir = diagnosticLogRoot(kind);
  const label = diagnosticLabel(kind);
  const defaultPath = path.join(app.getPath("desktop"), `${diagnosticArchiveStem(kind)}-${timestampSlug()}.zip`);
  const selection = await dialog.showSaveDialog({
    title: `Export ${label} logs`,
    defaultPath,
    filters: [{ name: "Zip archive", extensions: ["zip"] }],
  });
  if (selection.canceled || !selection.filePath) return { ok: false, cancelled: true, logDir, kind };
  const archivePath = selection.filePath.endsWith(".zip") ? selection.filePath : `${selection.filePath}.zip`;
  const script = String.raw`
import json
import sys
import zipfile
from pathlib import Path
source = Path(sys.argv[1]).expanduser().resolve()
target = Path(sys.argv[2]).expanduser().resolve()
label = sys.argv[3]
if not source.exists():
    raise SystemExit(f"{label} log directory does not exist: {source}")
if not source.is_dir():
    raise SystemExit(f"{label} log path is not a directory: {source}")
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    target.unlink()
count = 0
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for item in sorted(source.rglob("*")):
        if item.is_file():
            archive.write(item, item.relative_to(source.parent).as_posix())
            count += 1
print(json.dumps({"fileCount": count, "sizeBytes": target.stat().st_size}, ensure_ascii=False))
`;
  const python = resolvePythonExecutable();
  const result = spawnSync(python, ["-c", script, logDir, archivePath, label], {
    cwd: REPO_ROOT,
    env: { ...process.env, PYTHONUTF8: "1", PYTHONPATH: appendPythonPath(process.env.PYTHONPATH) },
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const stderr = String(result.stderr || "").trim();
    const stdout = String(result.stdout || "").trim();
    throw new Error(stderr || stdout || `${label} log export failed with status ${result.status}`);
  }
  return { ok: true, kind, archivePath, logDir, python, ...parseLastJsonLine(String(result.stdout || "")) };
}

function exportComputerLogs(): Promise<Record<string, unknown>> {
  return exportDiagnosticLogs("computer");
}

function exportBrowserLogs(): Promise<Record<string, unknown>> {
  return exportDiagnosticLogs("browser");
}

async function revealPath(targetPath: string): Promise<boolean> {
  const target = path.resolve(String(targetPath || ""));
  if (!targetPath) return false;
  try {
    const stat = await fs.stat(target);
    if (stat.isFile()) {
      shell.showItemInFolder(target);
      return true;
    }
    const error = await shell.openPath(target);
    if (error) throw new Error(error);
    return true;
  } catch {
    const error = await shell.openPath(path.dirname(target));
    if (error) throw new Error(error);
    return true;
  }
}

function resolveWorkspaceLocalPath(targetPath: string, workspaceRoot: string): string {
  const rootValue = String(workspaceRoot || "").trim();
  const targetValue = String(targetPath || "").trim();
  if (!rootValue || !targetValue) throw new Error("Local path and workspace are required");

  const root = path.resolve(rootValue);
  const target = path.isAbsolute(targetValue)
    ? path.resolve(targetValue)
    : path.resolve(root, targetValue);
  const relative = path.relative(root, target);
  if (!relative || relative === ".") throw new Error("Local path must point to a file");
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("Local path must be inside the active workspace");
  }
  return target;
}

async function readLocalImage(targetPath: string, workspaceRoot: string): Promise<{
  dataUrl: string;
  path: string;
  name: string;
  size: number;
  mimeType: string;
}> {
  const requested = resolveWorkspaceLocalPath(targetPath, workspaceRoot);
  const root = await fs.realpath(path.resolve(String(workspaceRoot || "").trim()));
  const target = await fs.realpath(requested);
  const realRelative = path.relative(root, target);
  if (realRelative === ".." || realRelative.startsWith(`..${path.sep}`) || path.isAbsolute(realRelative)) {
    throw new Error("Local image must be inside the active workspace");
  }

  const extension = path.extname(target).toLowerCase();
  const mimeType = LOCAL_IMAGE_MIME_TYPES.get(extension);
  if (!mimeType) throw new Error("Unsupported local image format");

  const stat = await fs.stat(target);
  if (!stat.isFile()) throw new Error("Local image path is not a file");
  if (stat.size > MAX_INLINE_IMAGE_BYTES) {
    throw new Error("Local image is too large to preview");
  }

  const bytes = await fs.readFile(target);
  return {
    dataUrl: `data:${mimeType};base64,${bytes.toString("base64")}`,
    path: target,
    name: path.basename(target),
    size: stat.size,
    mimeType,
  };
}


function pathIsInsideWorkspace(root: string, target: string): boolean {
  const relative = path.relative(root, target);
  return relative === "" || (
    relative !== ".."
    && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative)
  );
}

async function resolveWorkspaceFile(targetPath: string, workspaceRoot: string): Promise<{
  root: string;
  target: string;
  stat: Awaited<ReturnType<typeof fs.stat>>;
}> {
  const requested = resolveWorkspaceLocalPath(targetPath, workspaceRoot);
  const root = await fs.realpath(path.resolve(String(workspaceRoot || "").trim()));
  const target = await fs.realpath(requested);
  if (!pathIsInsideWorkspace(root, target)) {
    throw new Error("Local file must be inside the active workspace");
  }
  const stat = await fs.stat(target);
  if (!stat.isFile()) throw new Error("Local path is not a file");
  return { root, target, stat };
}

async function readLocalMedia(targetPath: string, workspaceRoot: string): Promise<{
  dataUrl: string;
  path: string;
  name: string;
  size: number;
  mimeType: string;
}> {
  const requested = resolveWorkspaceLocalPath(targetPath, workspaceRoot);
  const root = await fs.realpath(path.resolve(String(workspaceRoot || "").trim()));
  const target = await fs.realpath(requested);
  const realRelative = path.relative(root, target);
  if (realRelative === ".." || realRelative.startsWith(`..${path.sep}`) || path.isAbsolute(realRelative)) {
    throw new Error("Local media must be inside the active workspace");
  }

  const mimeType = LOCAL_MEDIA_MIME_TYPES.get(path.extname(target).toLowerCase());
  if (!mimeType) throw new Error("Unsupported local media format");
  const stat = await fs.stat(target);
  if (!stat.isFile()) throw new Error("Local media path is not a file");
  if (stat.size > MAX_INLINE_MEDIA_BYTES) throw new Error("Local media is too large to preview");

  const bytes = await fs.readFile(target);
  return {
    dataUrl: `data:${mimeType};base64,${bytes.toString("base64")}`,
    path: target,
    name: path.basename(target),
    size: stat.size,
    mimeType,
  };
}


function artifactPreviewToken(root: string): string {
  return crypto.createHash("sha256").update(root).digest("hex").slice(0, 28);
}

function artifactMimeType(target: string): string {
  return ARTIFACT_MIME_TYPES.get(path.extname(target).toLowerCase()) || "application/octet-stream";
}

async function localArtifactPreviewUrl(targetPath: string, workspaceRoot: string): Promise<string> {
  const { root, target } = await resolveWorkspaceFile(targetPath, workspaceRoot);
  const token = artifactPreviewToken(root);
  artifactPreviewRoots.set(token, root);
  const relative = path.relative(root, target)
    .split(path.sep)
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `loom-artifact://${token}/${relative}`;
}

async function serveLocalArtifact(request: { url: string }): Promise<Response> {
  try {
    const url = new URL(request.url);
    const root = artifactPreviewRoots.get(url.hostname);
    if (!root) return new Response("Unknown artifact workspace", { status: 404 });

    const segments = url.pathname
      .split("/")
      .filter(Boolean)
      .map((segment) => decodeURIComponent(segment));
    let requested = path.resolve(root, ...segments);
    if (!pathIsInsideWorkspace(root, requested)) {
      return new Response("Artifact path is outside the workspace", { status: 403 });
    }

    let stat = await fs.stat(requested);
    if (stat.isDirectory()) {
      requested = path.join(requested, "index.html");
      stat = await fs.stat(requested);
    }
    if (!stat.isFile()) return new Response("Artifact path is not a file", { status: 404 });

    const target = await fs.realpath(requested);
    if (!pathIsInsideWorkspace(root, target)) {
      return new Response("Artifact path is outside the workspace", { status: 403 });
    }

    const bytes = await fs.readFile(target);
    return new Response(bytes, {
      status: 200,
      headers: {
        "content-type": artifactMimeType(target),
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return new Response(message || "Artifact could not be loaded", { status: 404 });
  }
}

const MAX_RELAY_ARTIFACT_BYTES = 48 * 1024 * 1024;

/**
 * Reads an artifact for Loom Web. The `loom-artifact://` URL that the renderer
 * uses is minted by `localArtifactPreviewUrl` and only resolves inside a
 * renderer session through `protocol.handle`, so the relay reads the bytes
 * directly here — mirroring the directory-to-index.html fallback and workspace
 * checks that `serveLocalArtifact` performs for that URL.
 */
async function readRelayArtifact(targetPath: string, workspaceRoot: string): Promise<{
  base64: string;
  mimeType: string;
}> {
  const requested = resolveWorkspaceLocalPath(targetPath, workspaceRoot);
  const root = await fs.realpath(path.resolve(String(workspaceRoot || "").trim()));
  const resolved = (await fs.stat(requested)).isDirectory()
    ? path.join(requested, "index.html")
    : requested;
  const { target, stat } = await resolveWorkspaceFile(resolved, root);
  if (stat.size > MAX_RELAY_ARTIFACT_BYTES) {
    throw new Error("Artifact is too large for Loom Web preview (48 MB max).");
  }
  const bytes = await fs.readFile(target);
  return { base64: bytes.toString("base64"), mimeType: artifactMimeType(target) };
}

async function openLocalArtifact(targetPath: string, workspaceRoot: string): Promise<boolean> {
  const { root, target } = await resolveWorkspaceFile(targetPath, workspaceRoot);
  const extension = path.extname(target).toLowerCase();

  if (!LOCAL_BROWSER_ARTIFACT_SUFFIXES.has(extension)) {
    const error = await shell.openPath(target);
    if (error) throw new Error(error);
    return true;
  }

  const preview = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 720,
    minHeight: 520,
    title: path.basename(target),
    parent: mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined,
    modal: false,
    autoHideMenuBar: true,
    show: false,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#0d0e11" : "#ffffff",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
    },
  });

  preview.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "mailto:") {
        void shell.openExternal(parsed.toString());
      }
    } catch {
      // Invalid targets are denied below.
    }
    return { action: "deny" };
  });

  preview.webContents.on("will-navigate", (event, url) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === "file:") {
        const localTarget = fsSync.realpathSync(path.resolve(fileURLToPath(parsed)));
        if (pathIsInsideWorkspace(root, localTarget)) return;
      }
      if (parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "mailto:") {
        event.preventDefault();
        void shell.openExternal(parsed.toString());
        return;
      }
    } catch {
      // Fall through to a blocked navigation.
    }
    event.preventDefault();
  });

  artifactWindows.add(preview);
  preview.on("closed", () => artifactWindows.delete(preview));
  preview.once("ready-to-show", () => preview.show());
  try {
    await preview.loadFile(target);
    return true;
  } catch (error) {
    if (!preview.isDestroyed()) preview.destroy();
    throw error;
  }
}

async function copyImageSource(sourceValue: string): Promise<boolean> {
  const source = String(sourceValue || "").trim();
  if (!source) return false;

  let image;
  if (source.startsWith("data:image/")) {
    image = nativeImage.createFromDataURL(source);
  } else if (source.startsWith("file://")) {
    image = nativeImage.createFromPath(fileURLToPath(source));
  } else if (/^https?:\/\//i.test(source)) {
    const response = await fetch(source);
    if (!response.ok) throw new Error(`Could not download image (HTTP ${response.status})`);
    const bytes = Buffer.from(await response.arrayBuffer());
    image = nativeImage.createFromBuffer(bytes);
  } else if (path.isAbsolute(source)) {
    image = nativeImage.createFromPath(source);
  } else {
    throw new Error("Unsupported image source");
  }

  if (!image || image.isEmpty()) throw new Error("Image could not be decoded");
  clipboard.writeImage(image);
  return true;
}

async function openExternalUrl(value: string): Promise<boolean> {
  const url = new URL(String(value || "").trim());
  if (url.protocol !== "https:" && url.protocol !== "http:" && url.protocol !== "mailto:") {
    throw new Error("Only http, https, and mailto links can be opened externally");
  }
  await shell.openExternal(url.toString());
  return true;
}

class LoomRpcProcess {
  private modelDeferred = false;
  private child: ChildProcessWithoutNullStreams | null = null;
  private nextId = 1;
  private pending = new Map<number, {
    resolve: (value: unknown) => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }>();
  private initialized = false;
  private initializeResult: unknown = null;
  private connectPromise: Promise<unknown> | null = null;

  constructor(
    private readonly notify: (payload: JsonRpcResponse) => void,
    private readonly models: DesktopModelManager,
    private readonly account: LoomAccountClient,
  ) {}

  private async materializeModelSpec(spec: ModelLaunchSpec): Promise<ModelLaunchSpec> {
    if (spec.authMode !== "loom-account") return spec;
    return { ...spec, apiKey: await this.account.modelCredential() };
  }

  async modelParams(spec: ModelLaunchSpec): Promise<Record<string, unknown>> {
    return runtimeModelParams(await this.materializeModelSpec(spec));
  }

  async ensureModelReady(): Promise<void> {
    if (!this.modelDeferred) return;
    await this.setModel(this.models.current ?? this.models.ensureInitial());
    this.modelDeferred = false;
  }

  get ready(): boolean {
    return Boolean(this.child && this.initialized);
  }

  private async initializeOnce(): Promise<unknown> {
    if (!this.child) await this.startProcess();
    const result = await this.call("initialize", {
      protocolVersion: 1,
      clientInfo: { name: "loom-react-desktop", version: "0.1.0" },
    }, APP_SERVER_CONNECT_TIMEOUT_MS);
    this.sendNotification("initialized", {});
    this.initializeResult = result;
    this.initialized = true;
    return result;
  }

  async connect(): Promise<unknown> {
    if (this.child && this.initialized) return this.initializeResult;
    if (this.connectPromise) return this.connectPromise;
    this.connectPromise = (async () => {
      try {
        return await this.initializeOnce();
      } catch (firstError) {
        console.warn("[loom-app-server] initialize failed; restarting once", firstError);
        this.stopProcess(new Error("Loom App Server initialization timed out"));
        try {
          return await this.initializeOnce();
        } catch (secondError) {
          this.stopProcess(new Error("Loom App Server initialization failed after recovery"));
          throw secondError;
        }
      }
    })();
    try {
      return await this.connectPromise;
    } finally {
      this.connectPromise = null;
    }
  }

  async call(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = APP_SERVER_CALL_TIMEOUT_MS,
  ): Promise<unknown> {
    const child = this.child;
    if (!child) throw new Error("Loom App Server is not running");
    const id = this.nextId++;
    const promise = new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => {
        const pending = this.pending.get(id);
        if (!pending) return;
        this.pending.delete(id);
        reject(new Error(`Loom App Server request timed out: ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });
    try {
      child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`, "utf8");
    } catch (cause) {
      const pending = this.pending.get(id);
      if (pending) {
        clearTimeout(pending.timer);
        this.pending.delete(id);
        pending.reject(cause instanceof Error ? cause : new Error(String(cause)));
      }
    }
    return promise;
  }

  async assertRestartSafe(): Promise<void> {
    // Both runtime and bootstrap updates use this boundary. An uninitialized
    // child can still belong to a client awaiting its connect result.
    if (this.connectPromise) {
      throw new Error("Wait for the local runtime to finish connecting before restarting it.");
    }
    if (!this.child || !this.initialized) return;
    const status = await this.call("runtime/status", {}) as RuntimeStatus;
    if (Array.isArray(status.activeThreadIds) && status.activeThreadIds.length > 0) {
      throw new Error("Finish or stop the current turn before changing model settings.");
    }
  }

  async setModel(spec: ModelLaunchSpec): Promise<unknown> {
    if (!this.child || !this.initialized) return this.connect();
    const runtime = await this.call("runtime/set_model", await this.modelParams(spec));
    this.initializeResult = initializationFromRuntime(runtime);
    return this.initializeResult;
  }

  async currentInitialization(): Promise<unknown> {
    if (!this.child || !this.initialized) return this.connect();
    const runtime = await this.call("runtime/status", {});
    return initializationFromRuntime(runtime);
  }

  async restart(): Promise<unknown> {
    this.stop();
    return this.connect();
  }

  private stopProcess(error: Error): void {
    const child = this.child;
    this.child = null;
    this.initialized = false;
    this.initializeResult = null;
    this.failAll(error);
    if (child && !child.killed) child.kill();
  }

  stop(): void {
    this.connectPromise = null;
    this.stopProcess(new Error("Loom App Server stopped"));
  }

  private sendNotification(method: string, params: Record<string, unknown>): void {
    this.child?.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params })}\n`, "utf8");
  }

  private async startProcess(): Promise<void> {
    const searchRelay = await sharedSearchRelay();
    const selectedSpec = this.models.current ?? this.models.ensureInitial();
    const launch = await startupModel(selectedSpec, () => this.account.modelCredential());
    const spec = launch.spec;
    this.modelDeferred = launch.deferred;
    let accountModelCredential = spec.authMode === "loom-account" ? spec.apiKey : "";
    if (!accountModelCredential) {
      try {
        accountModelCredential = await this.account.modelCredential();
      } catch {
        // BYOK/offline desktops remain usable. Account-gated automation stays
        // fail-closed until a Loom account credential becomes available.
      }
    }
    const python = resolvePythonExecutable();
    let accountAutomationCredential = "";
    try { accountAutomationCredential = await this.account.automationCredential(); } catch {}
    const sandboxExecutable = resolveHostSandboxExecutable(REPO_ROOT);
    const script = path.join(REPO_ROOT, "loom_app_server.py");
    const args = [script, "--workspace", REPO_ROOT, "--provider", spec.provider, "--model", spec.model, "--selection", spec.selection, "--local-ipc", ...runtimeModelArguments(spec)];
    if (this.modelDeferred) args.push("--allow-unconfigured-model");
    if (spec.baseUrl) args.push("--base-url", spec.baseUrl);
    if (spec.reasoning) args.push("--reasoning-kind", spec.reasoning.kind, "--reasoning-value", spec.reasoning.value);
    console.log(`[loom-app-server] launching ${python}`);
    const child = spawn(python, args, {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        ...searchRelay.env,
        PYTHONUTF8: "1",
        PYTHONPATH: appendPythonPath(process.env.PYTHONPATH),
        LOOM_DESKTOP_PYTHON: python,
        LOOM_HOST_RUNTIME_VERSION: currentHostRuntimeVersion(REPO_ROOT),
        LOOM_WINDOWS_SANDBOX_EXECUTABLE: sandboxExecutable || process.env.LOOM_WINDOWS_SANDBOX_EXECUTABLE,
        // Computer Use observes the foreground window, which is sometimes Loom
        // itself. Knowing which process owns Loom's own windows lets it say so
        // instead of silently automating its own UI.
        LOOM_DESKTOP_HOST_PID: String(process.pid),
        LOOM_DESKTOP_CLIENT_PIDS_FILE: desktopPidFile,
        LOOM_API_KEY: spec.apiKey,
        LOOM_ACCOUNT_API_BASE_URL: this.account.serviceUrl,
        LOOM_ACCOUNT_MODEL_CREDENTIAL: accountModelCredential,
        LOOM_ACCOUNT_AUTOMATION_CREDENTIAL: accountAutomationCredential,
        LOOM_ACCOUNT_TOOL_ACCESS_ENFORCED: "1",
        LOOM_BROWSER_EXTENSION_TOKEN: ensureBrowserBridgeToken(),
        // The page HUD is the extension's asset, and a browser Loom launches
        // has no extension in it: the runtime injects the same file over CDP.
        // A packaged build keeps it next to the extension rather than inside
        // the Python package, so the path comes from whoever knows the layout.
        LOOM_BROWSER_HUD_ASSET: path.join(browserExtensionSource(), "browser-hud.js"),
      },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child = child;
    readline.createInterface({ input: child.stdout }).on("line", (line) => this.handleLine(line));
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => console.error(`[loom-app-server] ${String(chunk).trimEnd()}`));
    child.on("error", (error) => {
      if (this.child === child) this.failAll(error);
    });
    child.on("exit", (code, signal) => {
      if (this.child !== child) return;
      this.child = null;
      this.initialized = false;
      this.initializeResult = null;
      this.connectPromise = null;
      this.failAll(new Error(`Loom App Server exited (${code ?? signal ?? "unknown"})`));
    });
  }

  private handleLine(line: string): void {
    let payload: JsonRpcResponse;
    try {
      payload = JSON.parse(line) as JsonRpcResponse;
    } catch {
      console.error("Invalid App Server JSON:", line);
      return;
    }
    if (payload.method) {
      this.notify(payload);
      return;
    }
    if (typeof payload.id !== "number") return;
    const pending = this.pending.get(payload.id);
    if (!pending) return;
    this.pending.delete(payload.id);
    clearTimeout(pending.timer);
    if (payload.error) pending.reject(new Error(payload.error.message));
    else pending.resolve(payload.result);
  }

  private failAll(error: Error): void {
    for (const { reject, timer } of this.pending.values()) {
      clearTimeout(timer);
      reject(error);
    }
    this.pending.clear();
  }
}

let mainWindow: BrowserWindow | null = null;
const artifactWindows = new Set<BrowserWindow>();
const artifactPreviewRoots = new Map<string, string>();
const modelManager = new DesktopModelManager(REPO_ROOT);
const accountClient = hostAccount;
type DesktopOAuthProvider = "google" | "github";
const DESKTOP_OAUTH_TTL_MS = 10 * 60 * 1000;
let pendingDesktopOAuth: { provider: DesktopOAuthProvider; nonce: string; expiresAt: number } | null = null;

async function startDesktopOAuth(provider: string): Promise<void> {
  if (isHostProcess || (provider !== "google" && provider !== "github")) {
    throw new Error("Invalid desktop OAuth provider");
  }
  const capabilities = await accountClient.capabilities();
  if (!capabilities[provider]) throw new Error("This sign-in provider is currently unavailable");
  const account = await accountClient.status();
  if (!account.configured || !account.reachable) throw new Error("Account service is not reachable");
  const base = new URL(account.serviceUrl);
  if (base.protocol !== "https:") throw new Error("Secure HTTPS is required for desktop OAuth");
  const nonce = crypto.randomBytes(24).toString("hex");
  const returnTo = "loom://auth/callback?nonce=" + nonce;
  const startUrl = new URL(base.toString().replace(/\/$/, "") + "/auth/oauth/" + provider + "/start");
  startUrl.searchParams.set("return_to", returnTo);
  pendingDesktopOAuth = { provider, nonce, expiresAt: Date.now() + DESKTOP_OAUTH_TTL_MS };
  try {
    await shell.openExternal(startUrl.toString());
  } catch (error) {
    pendingDesktopOAuth = null;
    throw error;
  }
}

export async function handleDesktopOAuthUrl(value: string): Promise<void> {
  if (isHostProcess) return;
  const pending = pendingDesktopOAuth;
  if (!pending || pending.expiresAt < Date.now()) {
    pendingDesktopOAuth = null;
    return;
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return;
  }
  if (url.protocol !== "loom:" || url.hostname !== "auth" || url.pathname !== "/callback") return;
  if (url.searchParams.get("nonce") !== pending.nonce) return;
  if (url.searchParams.get("loom_oauth_provider") &&
      url.searchParams.get("loom_oauth_provider") !== pending.provider) return;
  pendingDesktopOAuth = null;
  showDesktopWindow();
  const oauthError = url.searchParams.get("loom_oauth_error");
  const code = url.searchParams.get("loom_oauth_code");
  if (oauthError || !code) {
    mainWindow?.webContents.send("loom:account-oauth-result", {
      ok: false,
      error: { code: oauthError || "OAUTH_CODE_MISSING", message: "Quick sign-in was cancelled or could not be completed." },
    });
    return;
  }
  const result = await runAccountMutation(() => accountClient.oauthExchange(code));
  mainWindow?.webContents.send("loom:account-oauth-result", result.ok
    ? { ok: true }
    : { ok: false, error: result.error });
}

function handleRuntimeNotification(payload: JsonRpcResponse): void {
  mainWindow?.webContents.send("loom:notification", payload);
  broadcastHostEvent("loom:notification", payload);
  sendRelayNotification(payload);
  if (payload.method === "hud/update") sendHudUpdate(payload.params ?? {});
}
const rpc = new LoomRpcProcess(handleRuntimeNotification, modelManager, accountClient);
registerHeadlessUpdateGuard(async () => {
  try {
    await rpc.assertRestartSafe();
    return true;
  } catch {
    return false;
  }
});
registerHostRuntimeUpdateHooks({
  canActivate: async () => {
    try {
      await rpc.assertRestartSafe();
      return true;
    } catch {
      return false;
    }
  },
  reload: async () => {
    // Browser Use assets are part of the independently versioned Host runtime.
    // Synchronize installed unpacked-extension folders before the App Server
    // resumes so Browser Use and Agent code cross the version boundary together.
    await syncBrowserExtensionAssets();
    const wasReady = rpc.ready;
    rpc.stop();
    if (wasReady) await rpc.connect();
  },
});
let searchRelayPromise: ReturnType<typeof createSearchRelay> | null = null;
export function showDesktopWindow(): void {
  if (isHostProcess) return;
  if (!mainWindow) createWindow();
  if (mainWindow?.isMinimized()) mainWindow.restore();
  mainWindow?.show();
  mainWindow?.focus();
}
function sharedSearchRelay(): ReturnType<typeof createSearchRelay> {
  if (!searchRelayPromise) searchRelayPromise = createSearchRelay((query, count) => accountClient.search(query, count));
  return searchRelayPromise;
}

async function changeModel(
  apply: () => ModelLaunchSpec,
  options: { persistSelection?: string } = {},
): Promise<ModelRestartResult> {
  const previous = modelManager.current ?? modelManager.ensureInitial();
  const next = apply();
  const hadRunningServer = rpc.ready;
  try {
    const initialization = await rpc.setModel(next);
    if (options.persistSelection) modelManager.markActive(options.persistSelection);
    return { initialization, models: modelManager.snapshot(), hotSwitch: hadRunningServer };
  } catch (error) {
    modelManager.restore(previous);
    if (!missingHotSwitchMethod(error)) throw error;
    modelManager.restore(next);
    try {
      const initialization = await rpc.restart();
      if (options.persistSelection) modelManager.setActive(options.persistSelection);
      return { initialization, models: modelManager.snapshot(), hotSwitch: false };
    } catch (fallbackError) {
      modelManager.restore(previous);
      try {
        await rpc.restart();
      } catch (rollbackError) {
        console.error("Could not restore previous Loom model after failed switch", rollbackError);
      }
      throw fallbackError;
    }
  }
}

async function changeThreadModel(
  threadId: string,
  spec: ModelLaunchSpec,
): Promise<ModelRestartResult> {
  const id = String(threadId || "").trim();
  if (!id) throw new Error("Thread is required");
  const result = await rpc.call("thread/set_model", {
    threadId: id,
    ...(await rpc.modelParams(spec)),
  }) as { thread?: Record<string, unknown>; runtime?: unknown };
  const confirmedSelection = String(result.thread?.modelSelection ?? "");
  const confirmedModel = String(result.thread?.model ?? "");
  const confirmedVision = result.thread?.modelVision;
  if (
    confirmedSelection !== spec.selection
    || confirmedModel !== spec.model
    || (typeof confirmedVision === "boolean" && confirmedVision !== (spec.vision !== false))
  ) {
    throw new Error(
      `Model switch was not confirmed by the runtime (requested ${spec.selection}/${spec.model}, `
      + `received ${confirmedSelection || "unknown"}/${confirmedModel || "unknown"}).`,
    );
  }
  return {
    initialization: { runtime: result.runtime ?? {} },
    models: modelManager.snapshotFor(spec),
    hotSwitch: true,
    thread: result.thread,
  };
}

async function deleteModel(selection: string): Promise<ModelRestartResult> {
  const value = String(selection || "").trim();
  if (!value) throw new Error("Model profile is required");
  await rpc.assertRestartSafe();
  const previous = modelManager.current ?? modelManager.ensureInitial();
  const deletesCurrent = previous.selection === value;
  modelManager.delete(value);
  if (!deletesCurrent) return { initialization: await rpc.currentInitialization(), models: modelManager.snapshot(), hotSwitch: true };
  const next = modelManager.ensureInitial();
  try {
    const initialization = await rpc.setModel(next);
    modelManager.setActive(next.selection);
    return { initialization, models: modelManager.snapshot(), hotSwitch: true };
  } catch (error) {
    modelManager.restore(previous);
    if (!missingHotSwitchMethod(error)) throw error;
    modelManager.restore(next);
    try {
      const initialization = await rpc.restart();
      modelManager.setActive(next.selection);
      return { initialization, models: modelManager.snapshot(), hotSwitch: false };
    } catch (fallbackError) {
      modelManager.restore(previous);
      try {
        await rpc.restart();
      } catch (rollbackError) {
        console.error("Could not restore previous Loom model after failed delete fallback", rollbackError);
      }
      throw fallbackError;
    }
  }
}

function htmlEscape(value: string): string {
  return value.replace(/[&<>"']/g, (char) => HTML_ESCAPE[char] ?? char);
}

function rendererFailureDocument(title: string, detail: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="color-scheme" content="dark"><title>Loom startup error</title><style>html,body{height:100%;margin:0;background:#0d0e11;color:#eceef2;font-family:Segoe UI,sans-serif}.wrap{height:100%;display:grid;place-items:center;padding:32px;box-sizing:border-box}.card{width:min(680px,100%);padding:22px;border:1px solid #303440;border-radius:14px;background:#15171d;box-shadow:0 18px 60px rgba(0,0,0,.28)}h1{font-size:18px;margin:0 0 10px}p{color:#a5abb6;font-size:13px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}</style></head><body><div class="wrap"><div class="card"><h1>${htmlEscape(title)}</h1><p>${htmlEscape(detail)}</p></div></div></body></html>`;
}

function showRendererFailure(title: string, detail: string): void {
  const window = mainWindow;
  if (!window || window.isDestroyed()) return;
  void window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(rendererFailureDocument(title, detail))}`);
}

function devServerUrlFromArgs(): string | null {
  const prefix = "--dev-url=";
  const arg = process.argv.find((value) => value.startsWith(prefix));
  return arg?.slice(prefix.length).trim() || null;
}

async function loadRenderer(window: BrowserWindow): Promise<void> {
  const devUrl = devServerUrlFromArgs();
  try {
    if (devUrl) {
      console.log(`[loom-desktop] loading dev renderer ${devUrl}`);
      await window.loadURL(devUrl);
      return;
    }
    const builtIndex = path.join(DESKTOP_ROOT, "dist", "index.html");
    console.log(`[loom-desktop] loading built renderer ${builtIndex}`);
    await window.loadFile(builtIndex);
  } catch (error) {
    const detail = error instanceof Error ? error.stack || error.message : String(error);
    console.error("Loom renderer navigation failed", detail);
    showRendererFailure("Loom renderer could not be loaded", detail);
  }
}

function createWindow(): void {
  // Start from the OS preference. The renderer restores the persisted
  // Appearance choice and can switch native chrome to light/dark explicitly.
  nativeTheme.themeSource = "system";
  const windowIcon = !app.isPackaged && process.platform === "win32" && fsSync.existsSync(DEV_WINDOW_ICON)
    ? DEV_WINDOW_ICON
    : undefined;
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1040,
    minHeight: 680,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#0d0e11" : "#f7f7f8",
    title: "Loom",
    icon: windowIcon,
    autoHideMenuBar: true,
    show: false,
    // A login-started background Host must not reserve a taskbar slot. If the
    // user opens Loom, remoteRelay restores the normal taskbar button first.
    skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  const window = mainWindow;
  let rendererDocumentUrl = "";
  window.once("ready-to-show", () => window.show());
  window.webContents.on("did-finish-load", () => {
    const loaded = window.webContents.getURL();
    if (loaded) rendererDocumentUrl = loaded.split("#", 1)[0];
  });
  window.webContents.on("will-navigate", (event, targetUrl) => {
    if (!rendererDocumentUrl) return;
    const destination = String(targetUrl || "");
    if (destination.split("#", 1)[0] === rendererDocumentUrl) return;

    // The Loom shell is a single-document app. Relative Markdown links used to
    // navigate this BrowserWindow away from the transcript and back into the
    // renderer entrypoint, which looked like the whole conversation vanished.
    event.preventDefault();
    try {
      const parsed = new URL(destination);
      if (parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "mailto:") {
        void shell.openExternal(parsed.toString());
      }
    } catch {
      // Local workspace links are handled by the renderer IPC bridge instead.
    }
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "mailto:") {
        void shell.openExternal(parsed.toString());
      }
    } catch {
      // Deny unknown window-open targets.
    }
    return { action: "deny" };
  });
  window.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame || validatedURL.startsWith("data:text/html")) return;
    const detail = `${errorDescription} (${errorCode})\n${validatedURL}`;
    console.error("Loom renderer did-fail-load", detail);
    showRendererFailure("Loom renderer failed to navigate", detail);
  });
  window.webContents.on("preload-error", (_event, preloadPath, error) => {
    const detail = `${preloadPath}\n${error.stack || error.message}`;
    console.error("Loom preload failed", detail);
    showRendererFailure("Loom preload failed to load", detail);
  });
  window.webContents.on("render-process-gone", (_event, details) => {
    const detail = `reason=${details.reason}, exitCode=${details.exitCode}`;
    console.error("Loom renderer process exited", detail);
    showRendererFailure("Loom renderer process exited", detail);
  });
  void loadRenderer(window);
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
  });
}

function ensureDesktopUi(): void {
  if (mainWindow && !mainWindow.isDestroyed()) return;
  createWindow();
  createHudOverlayWindow();
}



// ---------------------------------------------------------------------------
// Shared desktop operations
//
// Everything the Loom Web relay can invoke lives here as a named function, and
// the renderer's IPC channels below delegate to those same functions, so the two
// transports cannot drift apart. Renderer-only channels — native theme, file
// dialogs, account actions — keep their own handlers and are deliberately absent
// from the table: a web client has no renderer to answer a dialog.
// ---------------------------------------------------------------------------

function runWriteClipboardText(value: string): boolean {
  clipboard.writeText(String(value || ""));
  return true;
}

function modelPolicyGroup(selection: string): string | null {
  const value = String(selection || "").trim();
  if (value === "builtin:minimax" || value.startsWith("builtin:minimax:")) return "minimax";
  if (value === "builtin:deepseek" || value.startsWith("builtin:deepseek:")) return "deepseek";
  if (value === "builtin:ant-ling" || value.startsWith("builtin:ant-ling:")) return "ant-ling";
  if (value.startsWith("builtin:opencode-go:")) return "opencode-go";
  if (value === "builtin:cqu" || value.startsWith("managed:")) return "managed-relay";
  return null;
}

function policyAllowsSelection(access: LoomModelPolicyAccess, selection: string): boolean {
  const value = String(selection || "").trim();
  if (!value || value.startsWith("saved:")) return true;
  if (!(value.startsWith("builtin:") || value.startsWith("managed:"))) return true;
  if (!access.enabled && Array.isArray(access.decisions) && access.decisions.length) return false;
  const decision = access.decisions?.find((item) => item.model_id === value);
  if (decision) return Boolean(decision.enabled);
  if (access.schema_version === 2) return false;
  if (!Array.isArray(access.decisions)) return access.enabled && access.models.includes(value);
  const groupId = modelPolicyGroup(value);
  const group = groupId ? access.model_groups?.find((item) => item.id === groupId) : undefined;
  if (group && group.enabled === false) return false;
  // Dynamic provider catalogs can contain models not known to the central
  // catalogue yet. Group policy is still applied here; exact per-model policy
  // is checked again by the App Server before the next turn starts.
  return true;
}

function policyBlockedMessage(access: LoomModelPolicyAccess, selection: string): string {
  const decision = access.decisions?.find((item) => item.model_id === selection);
  const source = String(decision?.source || "policy");
  if (source === "catalog" || (!decision && access.schema_version === 2)) return "Model is no longer available from this provider";
  if (source === "global_group") return "Disabled by Loom Admin · model group";
  if (source === "global") return "Disabled by Loom Admin · global policy";
  if (source === "account") return "Disabled by Loom Admin · account access";
  if (source === "user") return "Disabled by Loom Admin · account rule";
  if (source === "group") return "Disabled by Loom Admin · access group";
  return "Disabled by Loom Admin model policy";
}

function applyModelPolicy(
  snapshot: ReturnType<DesktopModelManager["snapshot"]>,
  access: LoomModelPolicyAccess | null,
): ReturnType<DesktopModelManager["snapshot"]> {
  if (!access) {
    const gate = <T extends ModelProfile>(profile: T): T => profile.kind === "builtin"
      ? { ...profile, available: false, statusMessage: "Model permissions unavailable. Retry shortly." }
      : profile;
    return { ...snapshot, profiles: snapshot.profiles.map(gate), primary: gate(snapshot.primary),
      current: snapshot.current ? gate(snapshot.current) : null };
  }
  const profiles = snapshot.profiles.map((profile) => {
    if (profile.kind !== "builtin" || policyAllowsSelection(access, profile.selection)) return profile;
    return {
      ...profile,
      available: false,
      statusMessage: policyBlockedMessage(access, profile.selection),
    };
  });
  const primary = profiles.find((profile) => profile.selection === snapshot.primary.selection) ?? snapshot.primary;
  const current = snapshot.current && !policyAllowsSelection(access, snapshot.current.selection)
    ? { ...snapshot.current, available: false, statusMessage: policyBlockedMessage(access, snapshot.current.selection) }
    : snapshot.current;
  return { ...snapshot, profiles, primary, current };
}

function applySignedOutModelGate(
  snapshot: ReturnType<DesktopModelManager["snapshot"]>,
  authenticated: boolean,
): ReturnType<DesktopModelManager["snapshot"]> {
  if (authenticated) return snapshot;
  const message = "Sign in to Loom to use models";
  const profiles = snapshot.profiles.map((profile) => ({ ...profile, available: false, statusMessage: message }));
  const primary = profiles.find((profile) => profile.selection === snapshot.primary.selection)
    ?? { ...snapshot.primary, available: false, statusMessage: message };
  const current = snapshot.current ? { ...snapshot.current, available: false, statusMessage: message } : null;
  return { ...snapshot, profiles, primary, current };
}

async function currentModelPolicy(): Promise<LoomModelPolicyAccess | null> {
  try {
    return await accountClient.modelPolicyAccess();
  } catch (error) {
    // The App Server performs fail-closed enforcement before every signed-in
    // built-in turn. Keeping the last local catalogue visible during an outage
    // makes it possible to switch to saved/BYOK connections instead of trapping
    // the user in an empty model picker.
    console.warn("[model-policy] catalogue check failed", error);
    return null;
  }
}

async function assertSignedInForModels(): Promise<void> {
  if (!await accountClient.verifyAuthenticatedSession()) {
    throw new Error("Sign in to Loom before using models.");
  }
}

async function assertModelSelectionAllowed(selection: string): Promise<void> {
  await assertSignedInForModels();
  const value = String(selection || "").trim();
  if (!value || value.startsWith("saved:")) return;
  const access = await accountClient.modelPolicyCheck(value);
  if (!access) throw new Error("Model permissions unavailable. Retry shortly.");
  if (!policyAllowsSelection(access, value)) {
    throw new Error(policyBlockedMessage(access, value));
  }
}

async function runListModels(forceRefresh = false): Promise<ReturnType<DesktopModelManager["snapshot"]>> {
  const [snapshot, authenticated] = await Promise.all([
    modelManager.listSnapshot(Boolean(forceRefresh)),
    accountClient.hasAuthenticatedSession(),
  ]);
  const access = await currentModelPolicy();
  const authoritative = access?.catalog
    ? await modelManager.applyServerCatalog(snapshot, access.catalog.models)
    : snapshot;
  return applySignedOutModelGate(applyModelPolicy(authoritative, access), authenticated && await accountClient.hasAuthenticatedSession());
}

async function runRpcCall(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
  if (method === "turn/start") {
    await assertSignedInForModels();
    await rpc.ensureModelReady();
  }
  return rpc.call(method, params);
}

async function runStageTempFile(name: string, bytes: Uint8Array): Promise<string> {
  const safe = (name || "pasted.png").replace(/[^A-Za-z0-9._-]+/g, "_").slice(-80) || "pasted.png";
  const folder = path.join(app.getPath("temp"), "loom-attachments");
  await fs.mkdir(folder, { recursive: true });
  const target = path.join(folder, `${crypto.randomUUID().slice(0, 8)}-${safe}`);
  await fs.writeFile(target, Buffer.from(bytes));
  return target;
}

async function runSwitchModelProfile(threadOrSelection: string, maybeSelection?: string): Promise<ModelRestartResult> {
  if (maybeSelection === undefined) {
    const selection = String(threadOrSelection || "").trim();
    if (!selection) throw new Error("Model profile is required");
    await assertModelSelectionAllowed(selection);
    return changeModel(() => modelManager.useProfile(selection), { persistSelection: selection });
  }
  const selection = String(maybeSelection || "").trim();
  if (!selection) throw new Error("Model profile is required");
  const spec = modelManager.resolve(selection);
  await assertModelSelectionAllowed(spec.selection);
  return changeThreadModel(threadOrSelection, spec);
}

async function runSwitchCurrentModel(
  threadOrModel: string,
  selectionOrUndefined?: string,
  modelOrUndefined?: string,
): Promise<ModelRestartResult> {
  if (modelOrUndefined === undefined) {
    const model = String(threadOrModel || "").trim();
    if (!model) throw new Error("Model ID is required");
    const currentSelection = (modelManager.current ?? modelManager.ensureInitial()).selection;
    const spec = modelManager.resolveModelNameFor(currentSelection, model);
    await assertModelSelectionAllowed(spec.selection);
    return changeModel(() => modelManager.useModelName(model));
  }
  const selection = String(selectionOrUndefined || "").trim();
  const model = String(modelOrUndefined || "").trim();
  if (!selection) throw new Error("Model profile is required");
  if (!model) throw new Error("Model ID is required");
  const spec = modelManager.resolveModelNameFor(selection, model);
  await assertModelSelectionAllowed(spec.selection);
  return changeThreadModel(threadOrModel, spec);
}

async function runTestModel(selection: string): Promise<ReturnType<DesktopModelManager["test"]>> {
  await assertSignedInForModels();
  return modelManager.test(String(selection || ""));
}

async function runAddModel(
  threadOrInput: string | AddModelInput,
  maybeInput?: AddModelInput,
): Promise<ModelRestartResult> {
  const threadScoped = typeof threadOrInput === "string";
  const input = (threadScoped ? maybeInput : threadOrInput) as AddModelInput | undefined;
  if (!input) throw new Error("Model connection input is required");
  await assertSignedInForModels();
  const profile = await modelManager.add(input);
  if (threadScoped) {
    return changeThreadModel(String(threadOrInput), modelManager.resolve(profile.selection));
  }
  await rpc.assertRestartSafe();
  return changeModel(() => modelManager.useProfile(profile.selection), { persistSelection: profile.selection });
}

async function runUpdateModel(input: EditModelInput): Promise<ReturnType<DesktopModelManager["snapshot"]>> {
  await rpc.assertRestartSafe();
  const selection = String(input?.selection || "").trim();
  if (!selection) throw new Error("Model profile is required");
  if ((modelManager.current ?? modelManager.ensureInitial()).selection === selection) {
    throw new Error("Switch to another model before editing the active connection.");
  }
  modelManager.update(input);
  return modelManager.snapshot();
}

async function runSetReasoning(
  ...args: string[]
): Promise<ReasoningUpdateResult & { thread?: Record<string, unknown> }> {
  if (args.length === 2) {
    const [kind, value] = args;
    const current = modelManager.current ?? modelManager.ensureInitial();
    const previous = current.reasoning ?? null;
    const next = modelManager.setReasoning(String(kind || "").trim(), String(value || "").trim());
    try {
      const runtime = await rpc.call("runtime/set_reasoning", { kind: next.kind, value: next.value });
      return { runtime, models: modelManager.snapshot() };
    } catch (error) {
      if (previous) {
        try {
          modelManager.setReasoning(previous.kind, previous.value);
        } catch (rollbackError) {
          console.error("Could not restore previous reasoning setting", rollbackError);
        }
      }
      throw error;
    }
  }

  const [threadId, selection, model, kind, value] = args;
  const id = String(threadId || "").trim();
  const selected = String(selection || "").trim();
  if (!id) throw new Error("Thread is required");
  if (!selected) throw new Error("Model profile is required");
  const result = await rpc.call("thread/set_reasoning", {
    threadId: id,
    kind: String(kind || "").trim(),
    value: String(value || "").trim(),
  }) as { thread?: Record<string, unknown>; runtime?: unknown };
  const spec = modelManager.resolveModelNameFor(selected, String(model || "").trim());
  const runtimeReasoning = (
    result.runtime
    && typeof result.runtime === "object"
    && "reasoning" in result.runtime
  ) ? (result.runtime as { reasoning?: { kind?: string; value?: string } | null }).reasoning : null;
  const current = runtimeReasoning && spec.reasoning
    ? { ...spec, reasoning: { ...spec.reasoning, kind: runtimeReasoning.kind || spec.reasoning.kind, value: runtimeReasoning.value || spec.reasoning.value } }
    : spec;
  return { runtime: result.runtime ?? {}, models: modelManager.snapshotFor(current), thread: result.thread };
}

/**
 * Arg coercion is intentionally identical to the preload relay's table: the web
 * client sends `unknown[]`, so every value is coerced defensively here.
 */
const desktopOperations: WebRelayOperations = {
  connect: async () => rpc.connect(),
  call: async (args) => runRpcCall(String(args[0] || ""), (args[1] || {}) as Record<string, unknown>),
  listModels: async (args) => runListModels(Boolean(args[0])),
  setModelProviderKey: async (args) => modelManager.setProviderKey(String(args[0] || ""), String(args[1] || "")),
  switchModelProfile: async (args) => args.length > 1
    ? runSwitchModelProfile(String(args[0] || ""), String(args[1] || ""))
    : runSwitchModelProfile(String(args[0] || "")),
  switchCurrentModel: async (args) => args.length > 2
    ? runSwitchCurrentModel(String(args[0] || ""), String(args[1] || ""), String(args[2] || ""))
    : runSwitchCurrentModel(String(args[0] || "")),
  addModel: async (args) => args.length > 1
    ? runAddModel(String(args[0] || ""), (args[1] || {}) as AddModelInput)
    : runAddModel((args[0] || {}) as AddModelInput),
  updateModel: async (args) => runUpdateModel((args[0] || {}) as EditModelInput),
  testModel: async (args) => runTestModel(String(args[0] || "")),
  deleteModel: async (args) => deleteModel(String(args[0] || "")),
  setReasoning: async (args) => runSetReasoning(...args.map((value) => String(value || ""))),
  exportComputerLogs: async () => exportComputerLogs(),
  exportBrowserLogs: async () => exportBrowserLogs(),
  setupBrowserExtension: async (args) => setupBrowserExtension(
    (args[0] || "edge") as "edge" | "chrome",
    Boolean(args[1]),
  ),
  revealPath: async (args) => revealPath(String(args[0] || "")),
  copyImageSource: async (args) => copyImageSource(String(args[0] || "")),
  readClipboardText: async () => clipboard.readText(),
  writeClipboardText: async (args) => runWriteClipboardText(String(args[0] || "")),
  openExternal: async (args) => openExternalUrl(String(args[0] || "")),
  readLocalImage: async (args) => readLocalImage(String(args[0] || ""), String(args[1] || "")),
  readLocalMedia: async (args) => readLocalMedia(String(args[0] || ""), String(args[1] || "")),
  stageTempFile: async (args) => {
    const bytes = Buffer.from(String(args[1] || ""), "base64");
    if (bytes.byteLength > MAX_RELAY_ARTIFACT_BYTES) {
      throw new Error("Attachment is too large for Loom Web (48 MB max).");
    }
    return runStageTempFile(String(args[0] || "attachment"), new Uint8Array(bytes));
  },
  readLocalArtifact: async (args) => readRelayArtifact(String(args[0] || ""), String(args[1] || "")),
};

handleHostChannel("loom:connect", () => rpc.connect());
handleHostChannel("loom:call", (_event, method: string, params?: Record<string, unknown>) => runRpcCall(method, params ?? {}));
// Disconnecting a UI must never stop shared web tasks or the Agent Runtime.
ipcMain.handle("loom:disconnect", () => true);
ipcMain.handle("loom:set-native-theme", (_event, source: "system" | "light" | "dark") => {
  const next = source === "light" || source === "dark" ? source : "system";
  nativeTheme.themeSource = next;
  const resolved = nativeTheme.shouldUseDarkColors ? "dark" : "light";
  const window = mainWindow;
  if (window && !window.isDestroyed()) {
    window.setBackgroundColor(resolved === "dark" ? "#0d0e11" : "#f7f7f8");
  }
  return resolved;
});
handleHostChannel("loom:export-computer-logs", () => exportComputerLogs());
handleHostChannel("loom:export-browser-logs", () => exportBrowserLogs());
handleHostChannel("loom:setup-browser-extension", (_event, browser: "edge" | "chrome" = "edge", extensionConnected = false) => setupBrowserExtension(browser, extensionConnected));
ipcMain.handle("loom:reveal-path", (_event, targetPath: string) => revealPath(targetPath));
ipcMain.handle("loom:copy-image-source", (_event, source: string) => copyImageSource(source));
ipcMain.handle("loom:clipboard-read-text", () => clipboard.readText());
ipcMain.handle("loom:clipboard-write-text", (_event, value: string) => runWriteClipboardText(value));
ipcMain.handle("loom:open-external", (_event, url: string) => openExternalUrl(url));
ipcMain.handle("loom:open-local-artifact", (_event, targetPath: string, workspaceRoot: string) => (
  openLocalArtifact(targetPath, workspaceRoot)
));
ipcMain.handle("loom:local-artifact-preview-url", (_event, targetPath: string, workspaceRoot: string) => (
  localArtifactPreviewUrl(targetPath, workspaceRoot)
));
ipcMain.handle("loom:read-local-image", (_event, targetPath: string, workspaceRoot: string) => (
  readLocalImage(targetPath, workspaceRoot)
));
ipcMain.handle("loom:read-local-media", (_event, targetPath: string, workspaceRoot: string) => (
  readLocalMedia(targetPath, workspaceRoot)
));
ipcMain.handle("loom:pick-files", async () => {
  const result = await dialog.showOpenDialog({ title: "Attach files", properties: ["openFile", "multiSelections"] });
  return result.canceled ? [] : result.filePaths;
});
ipcMain.handle("loom:stage-temp-file", (_event, name: string, bytes: Uint8Array) => runStageTempFile(name, bytes));
ipcMain.handle("loom:pick-directory", async () => {
  const result = await dialog.showOpenDialog({ title: "Add project folder", properties: ["openDirectory", "createDirectory"] });
  return result.canceled || !result.filePaths.length ? "" : result.filePaths[0];
});
// Account handlers return a discriminated result rather than rejecting: an IPC
// rejection only carries an Error's message, which would drop the service's
// machine readable `code` and force the renderer to show raw English text.
type AccountIpcResult =
  | { ok: true; snapshot: LoomAccountSnapshot }
  | { ok: false; error: AccountErrorPayload };

type AccountCapabilitiesIpcResult =
  | { ok: true; capabilities: LoomAuthCapabilities }
  | { ok: false; error: AccountErrorPayload };

type AccountChallengeIpcResult =
  | { ok: true; challenge: LoomAuthChallenge }
  | { ok: false; error: AccountErrorPayload };

async function runAccountAction(
  action: () => Promise<LoomAccountSnapshot>,
): Promise<AccountIpcResult> {
  try {
    return { ok: true, snapshot: await action() };
  } catch (error) {
    return { ok: false, error: accountErrorPayload(error) };
  }
}

const syncAccountToolAccessCredential = latestSync(async () => {
  let credential = "";
  let modelCredential = "";
  try {
    if (await accountClient.hasAuthenticatedSession()) {
      credential = await accountClient.automationCredential();
      modelCredential = await accountClient.modelCredential();
    }
  } catch {
    // A transient refresh failure is not sign-out. Keep the existing credential;
    // the runtime still validates its authorization/expiry at the next query.
    return;
  }
  if (!rpc.ready) return;
  try {
    await rpc.call("account/tool-access-credential", { credential, modelCredential }, 5_000);
  } catch {
    // Runtime restarts also receive the latest credential through the env.
  }
});

async function runAccountMutation(
  action: () => Promise<LoomAccountSnapshot>,
): Promise<AccountIpcResult> {
  const result = await runAccountAction(action);
  if (result.ok) await syncAccountToolAccessCredential();
  return result;
}

async function runAccountCapabilities(
  action: () => Promise<LoomAuthCapabilities>,
): Promise<AccountCapabilitiesIpcResult> {
  try {
    return { ok: true, capabilities: await action() };
  } catch (error) {
    return { ok: false, error: accountErrorPayload(error) };
  }
}

async function runAccountChallenge(
  action: () => Promise<LoomAuthChallenge>,
): Promise<AccountChallengeIpcResult> {
  try {
    return { ok: true, challenge: await action() };
  } catch (error) {
    return { ok: false, error: accountErrorPayload(error) };
  }
}

handleHostChannel("loom:account-status", () => runAccountAction(() => accountClient.status()));
handleHostChannel("loom:account-capabilities", () => runAccountCapabilities(() => accountClient.capabilities()));
ipcMain.handle("loom:account-oauth-start", async (_event, provider: string) => {
  try {
    await startDesktopOAuth(String(provider || ""));
    return { ok: true };
  } catch (error) {
    return { ok: false, error: accountErrorPayload(error) };
  }
});
handleHostChannel("loom:account-login", (_event, email: string, password: string) =>
  runAccountMutation(() => accountClient.login(String(email || ""), String(password || "")))
);
handleHostChannel("loom:account-register", (_event, email: string, password: string) =>
  runAccountMutation(() => accountClient.register(String(email || ""), String(password || "")))
);
handleHostChannel("loom:account-register-start", (_event, email: string, password: string) =>
  runAccountChallenge(() => accountClient.registerStart(String(email || ""), String(password || "")))
);
handleHostChannel("loom:account-verify-email", (_event, challengeId: string, code: string) =>
  runAccountMutation(() => accountClient.verifyEmail(String(challengeId || ""), String(code || "")))
);
handleHostChannel("loom:account-resend-email", (_event, challengeId: string) =>
  runAccountChallenge(() => accountClient.resendEmail(String(challengeId || "")))
);
handleHostChannel("loom:account-forgot-password", (_event, email: string) =>
  runAccountChallenge(() => accountClient.forgotPassword(String(email || "")))
);
handleHostChannel("loom:account-reset-password", (_event, challengeId: string, code: string, password: string) =>
  runAccountMutation(() => accountClient.resetPassword(String(challengeId || ""), String(code || ""), String(password || "")))
);
handleHostChannel("loom:account-oauth-exchange", (_event, code: string) =>
  runAccountMutation(() => accountClient.oauthExchange(String(code || "")))
);
handleHostChannel("loom:account-update-profile", (_event, displayName: string, avatarDataUrl: string) =>
  runAccountAction(() => accountClient.updateProfile(String(displayName || ""), String(avatarDataUrl || "")))
);
handleHostChannel("loom:account-logout", () => runAccountMutation(() => accountClient.logout()));
handleHostChannel("loom:model-list", (_event, forceRefresh?: boolean) => runListModels(Boolean(forceRefresh)));
handleHostChannel("loom:model-provider-key", (_event, provider: string, apiKey: string) =>
  modelManager.setProviderKey(String(provider || ""), String(apiKey || ""))
);
handleHostChannel("loom:model-switch", (_event, threadOrSelection: string, maybeSelection?: string) => (
  runSwitchModelProfile(threadOrSelection, maybeSelection)
));
handleHostChannel("loom:model-switch-current", (
  _event,
  threadOrModel: string,
  selectionOrUndefined?: string,
  modelOrUndefined?: string,
) => runSwitchCurrentModel(threadOrModel, selectionOrUndefined, modelOrUndefined));
handleHostChannel("loom:model-add", (_event, threadOrInput: string | AddModelInput, maybeInput?: AddModelInput) => (
  runAddModel(threadOrInput, maybeInput)
));
handleHostChannel("loom:model-update", (_event, input: EditModelInput) => runUpdateModel(input));
handleHostChannel("loom:model-test", async (_event, selection: string) => runTestModel(selection));
handleHostChannel("loom:model-delete", async (_event, selection: string) => deleteModel(selection));
handleHostChannel("loom:reasoning-set", (_event, ...args: string[]) => runSetReasoning(...args));

app.setName("Loom");

app.whenReady().then(async () => {
  void protocol.handle("loom-artifact", serveLocalArtifact);
  // Keep the packaged executable, taskbar grouping, Start menu shortcut, and
  // Windows notifications on the same application identity. electron-builder
  // stamps the executable with the icon configured for this appId.
  if (process.platform === "win32") app.setAppUserModelId("com.loom.agent");
  // electron-builder registers loom:// for the installed application.
  // Reassert ownership on packaged launches without registering a dev instance.
  if (!isHostProcess && app.isPackaged) app.setAsDefaultProtocolClient("loom");
  if (!isHostProcess) {
    await prepareDesktopHost();
    ensureDesktopUi();
    return;
  }
  // Startup and runtime activation use exactly the same selected asset source.
  // Do not create an unsolicited first install; update existing paired installs.
  if (fsSync.existsSync(path.join(browserExtensionTarget(), "manifest.json")) || legacyBrowserExtensionTargets().length) {
    await syncBrowserExtensionAssets();
  }
  await startHostTransport();
  const authorizationRefresh = setInterval(() => { void syncAccountToolAccessCredential(); }, 30_000);
  authorizationRefresh.unref();
  app.once("before-quit", () => clearInterval(authorizationRefresh));
  createHudOverlayWindow();
  // The relay owns its own reconnect loop; it stays idle until an account
  // session exists, and a failed auth lookup just schedules a retry.
  startWebRelay({ auth: webRelayAuthPayload, operations: desktopOperations });
}).catch((error) => {
  console.error("Loom startup failed", error);
  dialog.showErrorBox("Loom startup failed", error instanceof Error ? error.message : String(error));
  app.quit();
});
app.on("activate", () => {
  if (!isHostProcess && !mainWindow) createWindow();
});
app.on("window-all-closed", () => {
  if (!isHostProcess && process.platform !== "darwin") app.quit();
});
app.on("before-quit", () => {
  if (isHostProcess) {
    rpc.stop();
    closeHudOverlayWindow();
    stopWebRelay();
  }
});
