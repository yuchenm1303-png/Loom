import { BrowserWindow } from "electron";
import { installWindowChrome } from "./windowChrome.js";
import { installRendererContentSecurity, type RendererDocument } from "./contentSecurity.js";
import { trustRendererDocument } from "./rendererIpc.js";

const windows = new Map<string, BrowserWindow>();
const pending = new Map<string, Promise<void>>();
async function focus(threadId: string, window: BrowserWindow): Promise<boolean> {
  await pending.get(threadId);
  if (window.isDestroyed()) return false;
  if (window.isMinimized()) window.restore();
  window.show(); window.focus(); return true;
}

export async function openConversationWindow(threadId: string, options: {
  document: RendererDocument; preload: string; backgroundColor: string;
  readThread(id: string): Promise<unknown>;
  onClosed?(window: BrowserWindow): void;
}): Promise<boolean> {
  if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(threadId)) throw new Error("Invalid conversation id");
  const existing = windows.get(threadId);
  if (existing && !existing.isDestroyed()) {
    return focus(threadId, existing);
  }
  await options.readThread(threadId);
  // Another IPC request may have opened it while the read was in flight.
  const raced = windows.get(threadId);
  if (raced && !raced.isDestroyed()) return focus(threadId, raced);
  if (windows.size >= 8) throw new Error("Close a conversation window before opening another (8 windows maximum)");
  const window = new BrowserWindow({ width: 1280, height: 900, minWidth: 1040, minHeight: 680, frame: false,
    title: "Loom", backgroundColor: options.backgroundColor, show: false, autoHideMenuBar: true,
    webPreferences: { preload: options.preload, contextIsolation: true, nodeIntegration: false, sandbox: true } });
  windows.set(threadId, window);
  const url = new URL(options.document.url);
  url.hash = new URLSearchParams({ thread: threadId }).toString();
  trustRendererDocument(window, options.document.url);
  installRendererContentSecurity(window, options.document);
  installWindowChrome(window);
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, target) => {
    if (target.split("#", 1)[0] !== options.document.url.split("#", 1)[0]) event.preventDefault();
  });
  window.once("closed", () => {
    if (windows.get(threadId) === window) windows.delete(threadId);
    options.onClosed?.(window);
  });
  try {
    const loading = window.loadURL(url.toString());
    pending.set(threadId, loading);
    await loading;
    return await focus(threadId, window);
  } catch (error) {
    if (!window.isDestroyed()) window.destroy();
    windows.delete(threadId);
    throw error;
  } finally { pending.delete(threadId); }
}
