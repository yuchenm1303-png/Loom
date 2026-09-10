import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
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
  type ModelLaunchSpec,
} from "./modelManager.js";
import { closeHudOverlayWindow, createHudOverlayWindow, sendHudUpdate } from "./hudWindow.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");
const REPO_VENV_PYTHON = process.platform === "win32"
  ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
  : path.join(REPO_ROOT, ".venv", "bin", "python");

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

function computerLogRoot(): string {
  const configured = process.env.LOOM_COMPUTER_LOG_DIR?.trim();
  return configured ? path.resolve(configured) : path.join(REPO_ROOT, ".loom", "logs", "computer-use");
}

function timestampSlug(): string {
  return new Date().toISOString().replace(/[:.]/g, "-").replace("T", "_").replace("Z", "");
}

function parseLastJsonLine(stdout: string): Record<string, unknown> {
  const lines = String(stdout || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const last = lines.at(-1) || "{}";
  const parsed = JSON.parse(last);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
}

async function exportComputerLogs(): Promise<Record<string, unknown>> {
  const logDir = computerLogRoot();
  const defaultPath = path.join(app.getPath("desktop"), `loom-computer-use-logs-${timestampSlug()}.zip`);
  const selection = await dialog.showSaveDialog({
    title: "Export Computer Use logs",
    defaultPath,
    filters: [{ name: "Zip archive", extensions: ["zip"] }],
  });
  if (selection.canceled || !selection.filePath) {
    return { ok: false, cancelled: true, logDir };
  }
  const archivePath = selection.filePath.endsWith(".zip") ? selection.filePath : `${selection.filePath}.zip`;
  const script = String.raw`
import json
import os
import sys
import zipfile
from pathlib import Path

source = Path(sys.argv[1]).expanduser().resolve()
target = Path(sys.argv[2]).expanduser().resolve()
if not source.exists():
    raise SystemExit(f"Computer Use log directory does not exist: {source}")
if not source.is_dir():
    raise SystemExit(f"Computer Use log path is not a directory: {source}")
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    target.unlink()
count = 0
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(source.parent).as_posix()
        archive.write(item, rel)
        count += 1
print(json.dumps({"fileCount": count, "sizeBytes": target.stat().st_size}, ensure_ascii=False))
`;
  const python = resolvePythonExecutable();
  const result = spawnSync(python, ["-c", script, logDir, archivePath], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONPATH: appendPythonPath(process.env.PYTHONPATH),
    },
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const stderr = String(result.stderr || "").trim();
    const stdout = String(result.stdout || "").trim();
    throw new Error(stderr || stdout || `Computer Use log export failed with status ${result.status}`);
  }
  const summary = parseLastJsonLine(String(result.stdout || ""));
  return {
    ok: true,
    archivePath,
    logDir,
    python,
    ...summary,
  };
}

async function revealPath(targetPath: string): Promise<boolean> {
  const target = path.resolve(String(targetPath || ""));
  if (!target) return false;
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
    const parent = path.dirname(target);
    const error = await shell.openPath(parent);
    if (error) throw new Error(error);
    return true;
  }
}

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
}

interface ReasoningUpdateResult {
  runtime: unknown;
  models: ReturnType<DesktopModelManager["snapshot"]>;
}

function initializationFromRuntime(payload: unknown): unknown {
  if (payload && typeof payload === "object" && "runtime" in payload) return payload;
  return { runtime: payload };
}

function runtimeModelParams(spec: ModelLaunchSpec): Record<string, unknown> {
  return {
    provider: spec.provider,
    baseUrl: spec.baseUrl,
    model: spec.model,
    apiKey: spec.apiKey,
    vision: true,
    reasoningKind: spec.reasoning?.kind ?? "",
    reasoningValue: spec.reasoning?.value ?? "",
  };
}

function missingHotSwitchMethod(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes("Method not found: runtime/set_model");
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
    const payload = JSON.stringify({ jsonrpc: "2.0", id, method, params });
    const promise = new Promise<unknown>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.child.stdin.write(`${payload}\n`, "utf8");
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
    if (!this.child) return;
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params })}\n`, "utf8");
  }

  private startProcess(): void {
    const spec = this.models.current ?? this.models.ensureInitial();
    const python = resolvePythonExecutable();
    const script = path.join(REPO_ROOT, "loom_app_server.py");
    const args = [
      script,
      "--workspace",
      REPO_ROOT,
      "--provider",
      spec.provider,
      "--model",
      spec.model,
    ];
    if (spec.baseUrl) args.push("--base-url", spec.baseUrl);
    if (spec.reasoning) {
      args.push("--reasoning-kind", spec.reasoning.kind);
      args.push("--reasoning-value", spec.reasoning.value);
    }

    console.log(`[loom-app-server] launching ${python}`);
    const child = spawn(python, args, {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONUTF8: "1",
        PYTHONPATH: appendPythonPath(process.env.PYTHONPATH),
        LOOM_DESKTOP_PYTHON: python,
        LOOM_API_KEY: spec.apiKey,
      },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child = child;

    const stdout = readline.createInterface({ input: child.stdout });
    stdout.on("line", (line) => this.handleLine(line));
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
  if (payload.method === "hud/update") {
    sendHudUpdate(payload.params ?? {});
  }
}
const rpc = new LoomRpcProcess(handleRuntimeNotification, modelManager);

async function changeModel(
  apply: () => ModelLaunchSpec,
  options: { persistSelection?: string } = {},
): Promise<ModelRestartResult> {
  await rpc.assertRestartSafe();
  const previous = modelManager.current ?? modelManager.ensureInitial();
  const next = apply();
  const hadRunningServer = rpc.ready;
  try {
    const initialization = await rpc.setModel(next);
    if (options.persistSelection) modelManager.setActive(options.persistSelection);
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

async function deleteModel(selection: string): Promise<ModelRestartResult> {
  const value = String(selection || "").trim();
  if (!value) throw new Error("Model profile is required");
  await rpc.assertRestartSafe();
  const previous = modelManager.current ?? modelManager.ensureInitial();
  const deletesCurrent = previous.selection === value;
  modelManager.delete(value);

  if (!deletesCurrent) {
    const initialization = await rpc.currentInitialization();
    return { initialization, models: modelManager.snapshot(), hotSwitch: true };
  }

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

function rendererFailureDocument(title: string, detail: string): string {
  const safeTitle = title.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char);
  const safeDetail = detail.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char);
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="color-scheme" content="dark"><title>Loom startup error</title><style>html,body{height:100%;margin:0;background:#0d0e11;color:#eceef2;font-family:Segoe UI,sans-serif}.wrap{height:100%;display:grid;place-items:center;padding:32px;box-sizing:border-box}.card{width:min(680px,100%);padding:22px;border:1px solid #303440;border-radius:14px;background:#15171d;box-shadow:0 18px 60px rgba(0,0,0,.28)}h1{font-size:18px;margin:0 0 10px}p{color:#a5abb6;font-size:13px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}</style></head><body><div class="wrap"><div class="card"><h1>${safeTitle}</h1><p>${safeDetail}</p></div></div></body></html>`;
}

function showRendererFailure(title: string, detail: string): void {
  const window = mainWindow;
  if (!window || window.isDestroyed()) return;
  const html = rendererFailureDocument(title, detail);
  void window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
}

function devServerUrlFromArgs(): string | null {
  const prefix = "--dev-url=";
  const arg = process.argv.find((value) => value.startsWith(prefix));
  const value = arg?.slice(prefix.length).trim();
  return value || null;
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
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1040,
    minHeight: 680,
    backgroundColor: "#0d0e11",
    title: "Loom",
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
    showRendererFailure("Loom renderer process exited",