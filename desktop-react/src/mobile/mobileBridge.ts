// window.loom for the phone. The shared state layer talks to this object exactly as
// it talks to Electron or the Web relay; only the transport differs. The socket, the
// tokens and the keystore live in the native shell, so the page never holds a
// credential and a turn keeps its connection while the page is in the background.

import type { AccountDevices } from "../types/loom";
import { MAX_ATTACHMENT_BYTES } from "./attachmentQueue";

export type ConnectionEvent = { at: number; event: string; detail: string };

export type ShellDevice = { id?: string; name?: string; platform?: string; version?: string; online?: boolean };

export type ShellState = {
  paired: boolean;
  /** The account service refused this phone: someone removed it on a computer. */
  removed?: boolean;
  deviceId: string;
  deviceName: string;
  socket: "open" | "connecting" | "closed";
  online: boolean;
  devices: ShellDevice[];
};

export type PairingProgress =
  | { state: "scanning" }
  | { state: "claimed"; confirmCode: string; deviceName: string }
  | { state: "approved" }
  | { state: "cancelled" }
  | { state: "failed"; message: string };

type ShellEvent =
  | { type: "invoke_result"; id: number; result?: unknown; error?: { code?: string; message?: string } }
  | { type: "notification"; payload: unknown }
  | { type: "state"; state: ShellState }
  | { type: "pairing"; progress: PairingProgress }
  | { type: "biometric"; id: number; ok: boolean }
  | { type: "devices"; id: number; devices?: AccountDevices; error?: string };

export type LoomShell = {
  state(): string;
  invoke(id: number, operation: string, argsJson: string): void;
  startPairing(): void;
  cancelPairing(): void;
  selectDevice(deviceId: string): void;
  unpair(): void;
  confirmBiometric(id: number, reason: string): void;
  openUrl(url: string): void;
  openAttachment(id: number, name: string, mimeType: string, base64: string): void;
  setBusyThreads(idsJson: string): void;
  fetchDevices(id: number): void;
  connectionLog(): string;
};

declare global {
  interface Window {
    LoomShell?: LoomShell;
    __loomShellEvent?: (json: string) => void;
  }
}

const CALL_TIMEOUT_MS = 125_000;
const CONNECT_TIMEOUT_MS = 20_000;

type Pending = { resolve(value: unknown): void; reject(cause: Error): void; timer: number };

const pending = new Map<number, Pending>();
const biometrics = new Map<number, (ok: boolean) => void>();
const deviceReads = new Map<number, { resolve(devices: AccountDevices): void; reject(cause: Error): void }>();
const notificationListeners = new Set<(message: never) => void>();
const stateListeners = new Set<(state: ShellState) => void>();
const pairingListeners = new Set<(progress: PairingProgress) => void>();
let nextId = 1;
let wasOpen = false;

function shell(): LoomShell {
  if (!window.LoomShell) throw new Error("The Loom app shell is unavailable.");
  return window.LoomShell;
}

export function readShellState(): ShellState {
  return JSON.parse(shell().state()) as ShellState;
}

export function onShellState(listener: (state: ShellState) => void): () => void {
  stateListeners.add(listener);
  return () => stateListeners.delete(listener);
}

export function onPairing(listener: (progress: PairingProgress) => void): () => void {
  pairingListeners.add(listener);
  return () => pairingListeners.delete(listener);
}

export function confirmBiometric(reason: string): Promise<boolean> {
  return new Promise((resolve) => {
    const id = nextId++;
    biometrics.set(id, resolve);
    shell().confirmBiometric(id, reason);
  });
}

function settle(id: number, result: unknown, error?: { code?: string; message?: string }): void {
  const call = pending.get(id);
  if (!call) return;
  pending.delete(id);
  window.clearTimeout(call.timer);
  if (error) {
    const message = String(error.message || "电脑未能完成此操作，请重试");
    const failure = new Error(message.includes("This action is only available on the computer")
      ? "电脑端尚未支持此手机操作，请更新电脑端 Loom 后重试" : message) as Error & { code?: string };
    failure.code = error.code;
    call.reject(failure);
  } else {
    call.resolve(result);
  }
}

function failAllPending(message: string): void {
  for (const id of [...pending.keys()]) settle(id, undefined, { code: "HOST_OFFLINE", message });
}

function receive(json: string): void {
  const event = JSON.parse(json) as ShellEvent;
  if (event.type === "invoke_result") settle(event.id, event.result, event.error);
  else if (event.type === "notification") for (const listener of notificationListeners) listener(event.payload as never);
  else if (event.type === "pairing") for (const listener of pairingListeners) listener(event.progress);
  else if (event.type === "biometric") {
    biometrics.get(event.id)?.(event.ok);
    biometrics.delete(event.id);
  } else if (event.type === "devices") {
    const read = deviceReads.get(event.id);
    deviceReads.delete(event.id);
    if (event.devices) read?.resolve(event.devices);
    else read?.reject(new Error(event.error || "读取设备失败"));
  } else if (event.type === "state") {
    const open = event.state.socket === "open" && event.state.online;
    if (wasOpen && !open) failAllPending("The connection to the computer dropped.");
    // Events were missed while it was gone: the shared layer re-reads its snapshot.
    if (open && !wasOpen) window.dispatchEvent(new CustomEvent("loom:web-host-reconnected"));
    wasOpen = open;
    for (const listener of stateListeners) listener(event.state);
  }
}

function invoke<T>(operation: string, args: unknown[] = []): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const id = nextId++;
    const timer = window.setTimeout(() => settle(id, undefined, { code: "HOST_TIMEOUT", message: "The computer did not respond." }), CALL_TIMEOUT_MS);
    pending.set(id, { resolve: resolve as (value: unknown) => void, reject, timer });
    try {
      shell().invoke(id, operation, JSON.stringify(args));
    } catch (cause) {
      settle(id, undefined, { message: cause instanceof Error ? cause.message : String(cause) });
    }
  });
}

function connected(): Promise<void> {
  const current = readShellState();
  if (current.socket === "open" && current.online) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      dispose();
      const error = new Error("The computer is offline.") as Error & { code?: string };
      error.code = "HOST_OFFLINE";
      reject(error);
    }, CONNECT_TIMEOUT_MS);
    const dispose = onShellState((state) => {
      if (state.socket !== "open" || !state.online) return;
      window.clearTimeout(timer);
      dispose();
      resolve();
    });
  });
}

export async function pickMobileFiles(): Promise<File[]> {
  return new Promise<File[]>((resolve, reject) => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.hidden = true;
    const finish = (files: File[]) => { input.remove(); resolve(files); };
    input.addEventListener("change", () => finish(Array.from(input.files ?? [])), { once: true });
    input.addEventListener("cancel", () => finish([]), { once: true });
    document.body.append(input);
    try { input.click(); } catch (cause) { input.remove(); reject(cause); }
  });
}

export async function uploadMobileFile(file: File, uploading: () => void = () => {}): Promise<string> {
  if (file.size > MAX_ATTACHMENT_BYTES) throw new Error("单个文件不能超过 10 MB");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  uploading();
  return invoke<string>("stageTempFile", [file.name || "attachment", btoa(binary)]);
}

async function pickFiles(): Promise<string[]> {
  const paths: string[] = [];
  for (const file of await pickMobileFiles()) paths.push(await uploadMobileFile(file));
  return paths;
}

async function openMobileArtifact(path: string, root: string): Promise<boolean> {
  const payload = await invoke<{ base64: string; mimeType: string }>("readLocalArtifact", [path, root]);
  return new Promise<boolean>((resolve, reject) => {
    const id = nextId++;
    const timer = window.setTimeout(() => settle(id, undefined, { message: "打开附件超时，请重试" }), CALL_TIMEOUT_MS);
    pending.set(id, { resolve: resolve as (value: unknown) => void, reject, timer });
    try { shell().openAttachment(id, path.split(/[\\/]/).pop() || "attachment", payload.mimeType, payload.base64); }
    catch (cause) { settle(id, undefined, { message: cause instanceof Error ? cause.message : String(cause) }); }
  });
}

const computerOnly = () => Promise.reject(new Error("This is only available on the computer."));

export function installMobileBridge(): void {
  window.__loomShellEvent = receive;
  // Shares the Web reconnect path: no workspace reset when the connection returns.
  document.documentElement.dataset.loomPlatform = "web";
  document.documentElement.dataset.loomSurface = "mobile";
  wasOpen = (() => { const state = readShellState(); return state.socket === "open" && state.online; })();

  const bridge = {
    connect: async () => { await connected(); return invoke("connect"); },
    call: (method: string, params: Record<string, unknown> = {}) => invoke("call", [method, params]),
    disconnect: async () => {},
    setNativeTheme: async () => "dark",
    onNotification: (listener: (message: never) => void) => {
      notificationListeners.add(listener);
      return () => notificationListeners.delete(listener);
    },
    listModels: (forceRefresh?: boolean) => invoke("listModels", [Boolean(forceRefresh)]),
    switchModelProfile: (...args: unknown[]) => invoke("switchModelProfile", args),
    switchCurrentModel: (...args: unknown[]) => invoke("switchCurrentModel", args),
    setReasoning: (...args: unknown[]) => invoke("setReasoning", args),
    queryModelUsage: (selection: string, force?: boolean) => invoke("queryModelUsage", [selection, Boolean(force)]),
    readLocalImage: (path: string, root: string) => invoke("readLocalImage", [path, root]),
    readLocalMedia: (path: string, root: string) => invoke("readLocalMedia", [path, root]),
    localArtifactPreviewUrl: async () => "",
    openLocalArtifact: openMobileArtifact,
    pickFiles,
    filePathFor: () => "",
    openExternal: async (url: string) => { shell().openUrl(url); return true; },
    writeClipboardText: async (value: string) => { await navigator.clipboard.writeText(value); return true; },
    readClipboardText: computerOnly,
    revealPath: async () => false,
    setModelProviderKey: computerOnly,
    addModel: computerOnly,
    updateModel: computerOnly,
    deleteModel: computerOnly,
    testModel: computerOnly,
    discoverModels: computerOnly,
  };
  Reflect.set(window, "loom", bridge);
}

export const startPairing = () => shell().startPairing();
export const cancelPairing = () => shell().cancelPairing();
export const selectDevice = (deviceId: string) => shell().selectDevice(deviceId);
export const unpair = () => shell().unpair();
export function fetchAccountDevices(): Promise<AccountDevices> {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    deviceReads.set(id, { resolve, reject });
    shell().fetchDevices(id);
  });
}
export const readConnectionLog = (): ConnectionEvent[] => (JSON.parse(shell().connectionLog()) as ConnectionEvent[]).reverse();
export const reportBusyThreads = (ids: string[]) => shell().setBusyThreads(JSON.stringify(ids));
