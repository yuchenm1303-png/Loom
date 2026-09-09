import { app, BrowserWindow, ipcMain } from "electron";
import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import readline from "node:readline";

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

class LoomRpcProcess {
  private child: ChildProcessWithoutNullStreams | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
  private initialized = false;
  private initializeResult: unknown = null;
  private connectPromise: Promise<unknown> | null = null;

  constructor(private readonly notify: (payload: JsonRpcResponse) => void) {}

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
    const python = process.env.LOOM_PYTHON || (process.platform === "win32" ? "python" : "python3");
    const script = path.join(REPO_ROOT, "loom_app_server.py");
    const child = spawn(python, [script, "--workspace", REPO_ROOT], {
      cwd: REPO_ROOT,
      env: { ...process.env, PYTHONUTF8: "1" },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child = child;

    const stdout = readline.createInterface({ input: child.stdout });
    stdout.on("line", (line) => this.handleLine(line));
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => console.error(`[loom-app-server] ${String(chunk).trimEnd()}`));
    child.on("error", (error) => this.failAll(error));
    child.on("exit", (code, signal) => {
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
const rpc = new LoomRpcProcess((payload) => mainWindow?.webContents.send("loom:notification", payload));

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
  mainWindow.once("ready-to-show", () => mainWindow?.show());

  const devUrl = process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173";
  if (!app.isPackaged) void mainWindow.loadURL(devUrl);
  else void mainWindow.loadFile(path.join(DESKTOP_ROOT, "dist", "index.html"));

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

ipcMain.handle("loom:connect", () => rpc.connect());
ipcMain.handle("loom:call", (_event, method: string, params?: Record<string, unknown>) => rpc.call(method, params ?? {}));
ipcMain.handle("loom:disconnect", () => rpc.stop());

app.whenReady().then(createWindow);
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
app.on("before-quit", () => rpc.stop());
