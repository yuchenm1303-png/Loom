import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import crypto from "node:crypto";
import readline from "node:readline";
import {
  DesktopModelManager,
  type AddModelInput,
  type ModelLaunchSpec,
} from "./modelManager.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DESKTOP_ROOT, "..");

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
}

interface ReasoningUpdateResult {
  runtime: unknown;
  models: ReturnType<DesktopModelManager["snapshot"]>;
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
    const python = process.env.LOOM_PYTHON || (process.platform === "win32" ? "python" : "python3");
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

    const child = spawn(python, args, {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONUTF8: "1",
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
const rpc = new LoomRpcProcess(
  (payload) => mainWindow?.webContents.send("loom:notification", payload),
  modelManager,
);

async function changeModel(
  apply: () => ModelLaunchSpec,
  options: { persistSelection?: string } = {},
): Promise<ModelRestartResult> {
  await rpc.assertRestartSafe();
  const previous = modelManager.current ?? modelManager.ensureInitial();
  apply();
  try {
    const initialization = await rpc.restart();
    if (options.persistSelection) modelManager.setActive(options.persistSelection);
    return { initialization, models: modelManager.snapshot() };
  } catch (error) {
    modelManager.restore(previous);
    try {
      await rpc.restart();
    } catch (rollbackError) {
      console.error("Could not restore previous Loom model after failed switch", rollbackError);
    }
    throw error;
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
    const initialization = await rpc.connect();
    return { initialization, models: modelManager.snapshot() };
  }

  try {
    const next = modelManager.ensureInitial();
    const initialization = await rpc.restart();
    modelManager.setActive(next.selection);
    return { initialization, models: modelManager.snapshot() };
  } catch (error) {
    modelManager.restore(previous);
    try {
      await rpc.restart();
    } catch (rollbackError) {
      console.error("Could not restore previous Loom model after failed delete fallback", rollbackError);
    }
    throw error;
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
    showRendererFailure("Loom renderer process exited", detail);
  });

  void loadRenderer(window);

  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
  });
}

ipcMain.handle("loom:connect", () => rpc.connect());
ipcMain.handle("loom:call", (_event, method: string, params?: Record<string, unknown>) => rpc.call(method, params ?? {}));
ipcMain.handle("loom:disconnect", () => rpc.stop());
ipcMain.handle("loom:pick-files", async () => {
  const result = await dialog.showOpenDialog({
    title: "Attach files",
    properties: ["openFile", "multiSelections"],
  });
  return result.canceled ? [] : result.filePaths;
});

ipcMain.handle("loom:stage-temp-file", async (_event, name: string, bytes: Uint8Array) => {
  // Only the main process can write to disk. The name is sanitised because it
  // reaches the filesystem; the App Server sanitises again when it stages the
  // file into the workspace, and neither side trusts the other's cleaning.
  const safe = (name || "pasted.png").replace(/[^A-Za-z0-9._-]+/g, "_").slice(-80) || "pasted.png";
  const folder = path.join(app.getPath("temp"), "loom-attachments");
  await fs.mkdir(folder, { recursive: true });
  const target = path.join(folder, `${crypto.randomUUID().slice(0, 8)}-${safe}`);
  await fs.writeFile(target, Buffer.from(bytes));
  return target;
});

ipcMain.handle("loom:pick-directory", async () => {
  // Adding a project is choosing a folder, which only the main process can ask
  // for. Cancelling resolves to "" so the caller never has to read a flag.
  const result = await dialog.showOpenDialog({
    title: "Add project folder",
    properties: ["openDirectory", "createDirectory"],
  });
  return result.canceled || !result.filePaths.length ? "" : result.filePaths[0];
});
ipcMain.handle("loom:model-list", () => modelManager.snapshot());
ipcMain.handle("loom:model-switch", async (_event, selection: string) => {
  const value = String(selection || "").trim();
  if (!value) throw new Error("Model profile is required");
  return changeModel(() => modelManager.useProfile(value), { persistSelection: value });
});
ipcMain.handle("loom:model-switch-current", async (_event, model: string) => {
  const value = String(model || "").trim();
  if (!value) throw new Error("Model ID is required");
  return changeModel(() => modelManager.useModelName(value));
});
ipcMain.handle("loom:model-add", async (_event, input: AddModelInput) => {
  await rpc.assertRestartSafe();
  const profile = modelManager.add(input);
  return changeModel(() => modelManager.useProfile(profile.selection), { persistSelection: profile.selection });
});
ipcMain.handle("loom:model-delete", async (_event, selection: string) => deleteModel(selection));
ipcMain.handle("loom:reasoning-set", async (_event, kind: string, value: string): Promise<ReasoningUpdateResult> => {
  await rpc.assertRestartSafe();
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
});

app.whenReady().then(createWindow);
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
app.on("before-quit", () => rpc.stop());