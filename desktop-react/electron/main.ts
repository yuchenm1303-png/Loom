import { app, BrowserWindow, clipboard, dialog, ipcMain, nativeTheme, shell } from "electron";
import { ChildProcessWithoutNullStreams, spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import fsSync from "node:fs";
import crypto from "node:crypto";
import readline from "node:readline";
import {
  DesktopModelManager,
  type AddModelInput,
  type EditModelInput,
  type ModelLaunchSpec,
} from "./modelManager.js";
import { LoomAccountClient } from "./accountClient.js";
import { closeHudOverlayWindow, createHudOverlayWindow, sendHudUpdate } from "./hudWindow.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const DEV_WINDOW_ICON = path.join(DESKTOP_ROOT, "build", "icon-dev.png");
const REPO_VENV_PYTHON = process.platform === "win32"
  ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
  : path.join(REPO_ROOT, ".venv", "bin", "python");
const HTML_ESCAPE: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

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
  const configured = process.env.LOOM_PYTHON?.trim();
  if (configured) return configured;
  if (fsSync.existsSync(REPO_VENV_PYTHON)) return REPO_VENV_PYTHON;
  return process.platform === "win32" ? "python" : "python3";
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

function runtimeModelParams(spec: ModelLaunchSpec): Record<string, unknown> {
  return {
    selection: spec.selection,
    provider: spec.provider,
    baseUrl: spec.baseUrl,
    model: spec.model,
    apiKey: spec.apiKey,
    vision: spec.vision !== false,
    contextLimits: spec.contextLimits,
    reasoningKind: spec.reasoning?.kind ?? "",
    reasoningValue: spec.reasoning?.value ?? "",
  };
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
  return app.isPackaged
    ? path.join(process.resourcesPath, "browser-current-tab")
    : path.join(REPO_ROOT, "extensions", "browser-current-tab");
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

async function setupBrowserExtension(browser: "edge" | "chrome" = "edge", extensionConnected = false): Promise<Record<string, unknown>> {
  const source = browserExtensionSource();
  const target = browserExtensionTarget();
  if (!fsSync.existsSync(path.join(source, "manifest.json"))) {
    throw new Error(`Packaged browser extension is missing: ${source}`);
  }
  const manifest = JSON.parse(await fs.readFile(path.join(source, "manifest.json"), "utf8")) as { version?: string };
  const installTargets = [target, ...legacyBrowserExtensionTargets()];
  const bridgeConfig = JSON.stringify({ bridgeUrl: "http://127.0.0.1:39222", token: ensureBrowserBridgeToken() });
  const updateSignal = JSON.stringify({ token: crypto.randomUUID(), version: String(manifest.version || "") });
  for (const installTarget of installTargets) {
    await fs.mkdir(installTarget, { recursive: true });
    await fs.cp(source, installTarget, { recursive: true, force: true });
    await fs.writeFile(path.join(installTarget, "bridge-config.json"), bridgeConfig, { encoding: "utf8", mode: 0o600 });
    // Write the signal last. A legacy extension that already has the watcher
    // will now reload only after all code and pairing files are in place.
    await fs.writeFile(path.join(installTarget, "extension-update.json"), updateSignal, { encoding: "utf8", mode: 0o600 });
  }
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
    desiredVersion: String(manifest.version || ""),
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
  if (!rootValue || !targetValue) throw new Error("Local image path and workspace are required");

  const root = path.resolve(rootValue);
  const target = path.isAbsolute(targetValue)
    ? path.resolve(targetValue)
    : path.resolve(root, targetValue);
  const relative = path.relative(root, target);
  if (!relative || relative === ".") throw new Error("Local image path must point to a file");
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("Local image must be inside the active workspace");
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

class LoomRpcProcess {
  private child: ChildProcessWithoutNullStreams | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
  private initialized = false;
  private initializeResult: unknown = null;
  private connectPromise: Promise<unknown> | null = null;

  constructor(
    private readonly notify: (payload: JsonRpcResponse) => void,
    private readonly models: DesktopModelManager,
  ) {}

  get ready(): boolean {
    return Boolean(this.child && this.initialized);
  }

  async connect(): Promise<unknown> {
    if (this.child && this.initialized) return this.initializeResult;
    if (this.connectPromise) return this.connectPromise;
    if (!this.child) this.startProcess();
    this.connectPromise = (async () => {
      const result = await this.call("initialize", {
        protocolVersion: 1,
        clientInfo: { name: "loom-react-desktop", version: "0.1.0" },
      });
      this.sendNotification("initialized", {});
      this.initializeResult = result;
      this.initialized = true;
      return result;
    })();
    try {
      return await this.connectPromise;
    } finally {
      this.connectPromise = null;
    }
  }

  async call(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    if (!this.child) throw new Error("Loom App Server is not running");
    const id = this.nextId++;
    const promise = new Promise<unknown>((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`, "utf8");
    return promise;
  }

  async assertRestartSafe(): Promise<void> {
    if (!this.child || !this.initialized) return;
    const status = await this.call("runtime/status", {}) as RuntimeStatus;
    if (Array.isArray(status.activeThreadIds) && status.activeThreadIds.length > 0) {
      throw new Error("Finish or stop the current turn before changing model settings.");
    }
  }

  async setModel(spec: ModelLaunchSpec): Promise<unknown> {
    if (!this.child || !this.initialized) return this.connect();
    const runtime = await this.call("runtime/set_model", runtimeModelParams(spec));
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

  stop(): void {
    const child = this.child;
    this.child = null;
    this.initialized = false;
    this.initializeResult = null;
    this.connectPromise = null;
    for (const { reject } of this.pending.values()) reject(new Error("Loom App Server stopped"));
    this.pending.clear();
    if (child && !child.killed) child.kill();
  }

  private sendNotification(method: string, params: Record<string, unknown>): void {
    this.child?.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params })}\n`, "utf8");
  }

  private startProcess(): void {
    const spec = this.models.current ?? this.models.ensureInitial();
    const python = resolvePythonExecutable();
    const script = path.join(REPO_ROOT, "loom_app_server.py");
    const args = [script, "--workspace", REPO_ROOT, "--provider", spec.provider, "--model", spec.model, "--selection", spec.selection, "--local-ipc"];
    if (spec.baseUrl) args.push("--base-url", spec.baseUrl);
    if (spec.reasoning) args.push("--reasoning-kind", spec.reasoning.kind, "--reasoning-value", spec.reasoning.value);
    console.log(`[loom-app-server] launching ${python}`);
    const child = spawn(python, args, {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONUTF8: "1",
        PYTHONPATH: appendPythonPath(process.env.PYTHONPATH),
        LOOM_DESKTOP_PYTHON: python,
        // Computer Use observes the foreground window, which is sometimes Loom
        // itself. Knowing which process owns Loom's own windows lets it say so
        // instead of silently automating its own UI.
        LOOM_DESKTOP_HOST_PID: String(process.pid),
        LOOM_API_KEY: spec.apiKey,
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
    if (payload.error) pending.reject(new Error(payload.error.message));
    else pending.resolve(payload.result);
  }

  private failAll(error: Error): void {
    for (const { reject } of this.pending.values()) reject(error);
    this.pending.clear();
  }
}

let mainWindow: BrowserWindow | null = null;
const modelManager = new DesktopModelManager(REPO_ROOT);
function handleRuntimeNotification(payload: JsonRpcResponse): void {
  mainWindow?.webContents.send("loom:notification", payload);
  if (payload.method === "hud/update") sendHudUpdate(payload.params ?? {});
}
const rpc = new LoomRpcProcess(handleRuntimeNotification, modelManager);
const accountClient = new LoomAccountClient();

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
    ...runtimeModelParams(spec),
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
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  const window = mainWindow;
  window.once("ready-to-show", () => window.show());
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
    if (process.platform !== "darwin") app.quit();
  });
}

ipcMain.handle("loom:connect", () => rpc.connect());
ipcMain.handle("loom:call", (_event, method: string, params?: Record<string, unknown>) => rpc.call(method, params ?? {}));
ipcMain.handle("loom:disconnect", () => rpc.stop());
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
ipcMain.handle("loom:export-computer-logs", () => exportComputerLogs());
ipcMain.handle("loom:export-browser-logs", () => exportBrowserLogs());
ipcMain.handle("loom:setup-browser-extension", (_event, browser: "edge" | "chrome" = "edge", extensionConnected = false) => setupBrowserExtension(browser, extensionConnected));
ipcMain.handle("loom:reveal-path", (_event, targetPath: string) => revealPath(targetPath));
ipcMain.handle("loom:read-local-image", (_event, targetPath: string, workspaceRoot: string) => (
  readLocalImage(targetPath, workspaceRoot)
));
ipcMain.handle("loom:pick-files", async () => {
  const result = await dialog.showOpenDialog({ title: "Attach files", properties: ["openFile", "multiSelections"] });
  return result.canceled ? [] : result.filePaths;
});
ipcMain.handle("loom:stage-temp-file", async (_event, name: string, bytes: Uint8Array) => {
  const safe = (name || "pasted.png").replace(/[^A-Za-z0-9._-]+/g, "_").slice(-80) || "pasted.png";
  const folder = path.join(app.getPath("temp"), "loom-attachments");
  await fs.mkdir(folder, { recursive: true });
  const target = path.join(folder, `${crypto.randomUUID().slice(0, 8)}-${safe}`);
  await fs.writeFile(target, Buffer.from(bytes));
  return target;
});
ipcMain.handle("loom:pick-directory", async () => {
  const result = await dialog.showOpenDialog({ title: "Add project folder", properties: ["openDirectory", "createDirectory"] });
  return result.canceled || !result.filePaths.length ? "" : result.filePaths[0];
});
ipcMain.handle("loom:account-status", () => accountClient.status());
ipcMain.handle("loom:account-login", (_event, email: string, password: string) =>
  accountClient.login(String(email || ""), String(password || ""))
);
ipcMain.handle("loom:account-register", (_event, email: string, password: string) =>
  accountClient.register(String(email || ""), String(password || ""))
);
ipcMain.handle("loom:account-logout", () => accountClient.logout());
ipcMain.handle("loom:model-list", () => modelManager.snapshot());
ipcMain.handle("loom:model-provider-key", (_event, provider: string, apiKey: string) =>
  modelManager.setProviderKey(String(provider || ""), String(apiKey || ""))
);
ipcMain.handle("loom:model-switch", async (_event, threadOrSelection: string, maybeSelection?: string) => {
  if (maybeSelection === undefined) {
    const selection = String(threadOrSelection || "").trim();
    if (!selection) throw new Error("Model profile is required");
    return changeModel(() => modelManager.useProfile(selection), { persistSelection: selection });
  }
  const selection = String(maybeSelection || "").trim();
  if (!selection) throw new Error("Model profile is required");
  return changeThreadModel(threadOrSelection, modelManager.resolve(selection));
});
ipcMain.handle("loom:model-switch-current", async (
  _event,
  threadOrModel: string,
  selectionOrUndefined?: string,
  modelOrUndefined?: string,
) => {
  if (modelOrUndefined === undefined) {
    const model = String(threadOrModel || "").trim();
    if (!model) throw new Error("Model ID is required");
    return changeModel(() => modelManager.useModelName(model));
  }
  const selection = String(selectionOrUndefined || "").trim();
  const model = String(modelOrUndefined || "").trim();
  if (!selection) throw new Error("Model profile is required");
  if (!model) throw new Error("Model ID is required");
  return changeThreadModel(threadOrModel, modelManager.resolveModelNameFor(selection, model));
});
ipcMain.handle("loom:model-add", async (_event, threadOrInput: string | AddModelInput, maybeInput?: AddModelInput) => {
  const threadScoped = typeof threadOrInput === "string";
  const input = (threadScoped ? maybeInput : threadOrInput) as AddModelInput | undefined;
  if (!input) throw new Error("Model connection input is required");
  const profile = modelManager.add(input);
  if (threadScoped) {
    return changeThreadModel(String(threadOrInput), modelManager.resolve(profile.selection));
  }
  await rpc.assertRestartSafe();
  return changeModel(() => modelManager.useProfile(profile.selection), { persistSelection: profile.selection });
});
ipcMain.handle("loom:model-update", async (_event, input: EditModelInput) => {
  await rpc.assertRestartSafe();
  const selection = String(input?.selection || "").trim();
  if (!selection) throw new Error("Model profile is required");
  if ((modelManager.current ?? modelManager.ensureInitial()).selection === selection) {
    throw new Error("Switch to another model before editing the active connection.");
  }
  modelManager.update(input);
  return modelManager.snapshot();
});
ipcMain.handle("loom:model-test", async (_event, selection: string) => modelManager.test(selection));
ipcMain.handle("loom:model-delete", async (_event, selection: string) => deleteModel(selection));
ipcMain.handle("loom:reasoning-set", async (_event, ...args: string[]): Promise<ReasoningUpdateResult & { thread?: Record<string, unknown> }> => {
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
});

app.setName("Loom");

app.whenReady().then(() => {
  // Keep the packaged executable, taskbar grouping, Start menu shortcut, and
  // Windows notifications on the same application identity. electron-builder
  // stamps the executable with the icon configured for this appId.
  if (process.platform === "win32") app.setAppUserModelId("com.loom.agent");
  createWindow();
  createHudOverlayWindow();
});
app.on("activate", () => {
  if (!mainWindow) createWindow();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
app.on("before-quit", () => {
  rpc.stop();
  closeHudOverlayWindow();
});
