import { app, BrowserWindow, session, WebContentsView } from "electron";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { normalizeBrowserAddress } from "./browserAddress.js";

/**
 * A native, sandboxed Electron webContents for each tab. No <webview> tag, no
 * page preload, no Node integration and no untrusted iframe in the Loom shell.
 * This is a USER browser, not a backdoor into Loom's Agent permission model.
 */
export interface EmbeddedBrowserTab {
  id: string;
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  error?: string;
}
export interface EmbeddedBrowserState {
  tabs: EmbeddedBrowserTab[];
  activeId: string | null;
}
export type EmbeddedBrowserCommand =
  | { action: "snapshot" }
  | { action: "bounds"; bounds: { x: number; y: number; width: number; height: number } | null }
  | { action: "new-tab"; url?: string }
  | { action: "select-tab" | "close-tab"; tabId: string }
  | { action: "navigate"; url: string }
  | { action: "back" | "forward" | "reload" | "stop" | "home" };

interface InternalTab {
  view: WebContentsView;
  state: EmbeddedBrowserTab;
}
const PARTITION = "persist:loom-embedded-browser";
const MAX_TABS = 16;
const STATE_FILE = "embedded-browser-tabs.json";

function validNavigation(url: string): boolean {
  try {
    const parsed = new URL(url);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && Boolean(parsed.hostname);
  } catch {
    return false;
  }
}
function tabLabel(url: string): string {
  if (!url || url === "about:blank") return "New tab";
  try { return new URL(url).hostname; } catch { return "New tab"; }
}

export class EmbeddedBrowserManager {
  private readonly tabs = new Map<string, InternalTab>();
  private readonly order: string[] = [];
  private readonly statePath: string;
  private activeId: string | null = null;
  private viewport: { x: number; y: number; width: number; height: number } | null = null;
  private attached: WebContentsView | null = null;
  private disposed = false;

  constructor(private readonly window: BrowserWindow) {
    this.statePath = path.join(app.getPath("userData"), STATE_FILE);
    const browserSession = session.fromPartition(PARTITION);
    // Site content must never request native OS privileges without a dedicated
    // audited user prompt. Ordinary browsing, cookies, and downloads still work.
    browserSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    browserSession.setPermissionCheckHandler(() => false);
    const restored = this.readRestoredTabs();
    for (const url of restored.urls) this.addTab(url, false);
    if (!this.order.length) this.addTab("", false);
    this.activeId = this.order.includes(restored.activeId) ? restored.activeId : this.order[0];
    window.on("resize", () => this.syncView());
    window.on("closed", () => this.dispose());
  }

  private readRestoredTabs(): { urls: string[]; activeId: string } {
    try {
      const data = JSON.parse(fs.readFileSync(this.statePath, "utf8")) as { urls?: unknown; active?: unknown };
      const urls = Array.isArray(data.urls)
        ? data.urls.filter((url): url is string => typeof url === "string" && validNavigation(url)).slice(0, MAX_TABS)
        : [];
      return { urls, activeId: "" };
    } catch { return { urls: [], activeId: "" }; }
  }

  private saveTabs(): void {
    if (this.disposed) return;
    // URLs, not cookies/passwords. Electron persists site cookies in its isolated partition.
    const urls = this.order.map(id => this.tabs.get(id)?.state.url || "").filter(validNavigation);
    try {
      const folder = path.dirname(this.statePath);
      fs.mkdirSync(folder, { recursive: true });
      const temporary = this.statePath + ".tmp";
      fs.writeFileSync(temporary, JSON.stringify({ urls }), { encoding: "utf8", mode: 0o600 });
      fs.renameSync(temporary, this.statePath);
    } catch { /* Browsing remains available if persistence is unwritable. */ }
  }

  private addTab(url: string, activate = true): void {
    if (this.tabs.size >= MAX_TABS) throw new Error("Maximum of 16 browser tabs.");
    const id = crypto.randomUUID();
    const view = new WebContentsView({
      webPreferences: {
        partition: PARTITION,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        webSecurity: true,
      },
    });
    view.setBackgroundColor("#101114");
    const state: EmbeddedBrowserTab = {
      id, url: url || "", title: tabLabel(url), loading: false, canGoBack: false, canGoForward: false,
    };
    this.tabs.set(id, { view, state });
    this.order.push(id);
    const update = () => {
      if (!this.tabs.has(id)) return;
      const history = view.webContents.navigationHistory;
      state.canGoBack = history.canGoBack();
      state.canGoForward = history.canGoForward();
      this.publish();
    };
    view.webContents.on("did-start-loading", () => { state.loading = true; state.error = undefined; update(); });
    view.webContents.on("did-stop-loading", () => { state.loading = false; update(); });
    view.webContents.on("page-title-updated", (_event, title) => { state.title = title || tabLabel(state.url); update(); });
    view.webContents.on("did-navigate", (_event, nextUrl) => {
      state.url = nextUrl === "about:blank" ? "" : nextUrl;
      state.title = tabLabel(state.url);
      this.saveTabs();
      this.syncView();
      update();
    });
    view.webContents.on("did-navigate-in-page", (_event, nextUrl, isMainFrame) => {
      if (!isMainFrame) return;
      state.url = nextUrl;
      this.saveTabs();
      this.syncView();
      update();
    });
    view.webContents.on("did-fail-load", (_event, code, description, _url, isMainFrame) => {
      if (!isMainFrame || code === -3) return; // -3 = aborted by navigation
      state.error = description;
      state.loading = false;
      update();
    });
    view.webContents.on("will-navigate", (event, nextUrl) => {
      if (!validNavigation(nextUrl)) event.preventDefault();
    });
    view.webContents.setWindowOpenHandler(({ url: target }) => {
      if (validNavigation(target) && this.tabs.size < MAX_TABS) this.addTab(target);
      return { action: "deny" };
    });
    view.webContents.on("before-input-event", (event, input) => {
      if (input.type !== "keyDown" || !(input.control || input.meta)) return;
      const key = input.key.toLowerCase();
      if (!["l", "t", "w"].includes(key)) return;
      event.preventDefault();
      if (key === "t") this.addTab("");
      else if (key === "w") this.closeTab(id);
      else this.window.webContents.send("loom:embedded-browser-focus-address");
    });
    if (activate) {
      this.activeId = id;
      this.syncView();
    }
    if (url) void view.webContents.loadURL(url).catch(error => {
      state.error = String(error);
      state.loading = false;
      update();
    });
    this.saveTabs();
    this.publish();
  }

  private closeTab(id: string): void {
    const entry = this.tabs.get(id);
    if (!entry) return;
    if (this.attached === entry.view) this.detach();
    this.tabs.delete(id);
    const index = this.order.indexOf(id);
    if (index !== -1) this.order.splice(index, 1);
    entry.view.webContents.close();
    if (!this.order.length) this.addTab("", false);
    if (this.activeId === id) this.activeId = this.order[Math.min(index, this.order.length - 1)];
    this.syncView();
    this.saveTabs();
    this.publish();
  }

  private detach(): void {
    if (this.attached && !this.window.isDestroyed()) {
      this.window.contentView.removeChildView(this.attached);
    }
    this.attached = null;
  }

  private syncView(): void {
    if (this.disposed || this.window.isDestroyed()) return;
    const active = this.tabs.get(this.activeId || "");
    const target = this.viewport && active?.state.url ? active.view : null;
    if (target !== this.attached) {
      this.detach();
      if (target) {
        this.window.contentView.addChildView(target);
        this.attached = target;
      }
    }
    if (!this.viewport || !this.attached) return;
    const [width, height] = this.window.getContentSize();
    const x = Math.max(0, Math.min(width, Math.floor(this.viewport.x)));
    const y = Math.max(0, Math.min(height, Math.floor(this.viewport.y)));
    const w = Math.max(0, Math.min(width - x, Math.floor(this.viewport.width)));
    const h = Math.max(0, Math.min(height - y, Math.floor(this.viewport.height)));
    this.attached.setBounds({ x, y, width: w, height: h });
  }

  private publish(): void {
    if (!this.disposed && !this.window.isDestroyed() && !this.window.webContents.isDestroyed()) {
      this.window.webContents.send("loom:embedded-browser-state", this.snapshot());
    }
  }

  snapshot(): EmbeddedBrowserState {
    return {
      tabs: this.order.map(id => ({ ...this.tabs.get(id)!.state })),
      activeId: this.activeId,
    };
  }

  async command(command: EmbeddedBrowserCommand): Promise<EmbeddedBrowserState> {
    if (this.disposed) throw new Error("Browser workspace is closed.");
    const active = this.tabs.get(this.activeId || "");
    switch (command.action) {
      case "snapshot": break;
      case "bounds": {
        const bounds = command.bounds;
        if (!bounds) this.viewport = null;
        else {
          const values = [bounds.x, bounds.y, bounds.width, bounds.height];
          if (!values.every(Number.isFinite) || bounds.width < 0 || bounds.height < 0) {
            throw new Error("Invalid browser viewport.");
          }
          this.viewport = bounds;
        }
        this.syncView();
        break;
      }
      case "new-tab":
        this.addTab(command.url ? normalizeBrowserAddress(command.url) : "");
        break;
      case "select-tab":
        if (!this.tabs.has(command.tabId)) throw new Error("Browser tab is missing.");
        this.activeId = command.tabId;
        this.syncView();
        break;
      case "close-tab": this.closeTab(command.tabId); break;
      case "navigate":
        if (!active) break;
        {
          const url = normalizeBrowserAddress(command.url);
          active.state.error = undefined;
          active.state.url = url;
          this.syncView();
          await active.view.webContents.loadURL(url).catch(error => {
            active.state.error = String(error);
          });
        }
        break;
      case "home":
        if (active) {
          active.state.error = undefined;
          active.state.url = "https://www.google.com";
          this.syncView();
          await active.view.webContents.loadURL("https://www.google.com").catch(error => {
            active.state.error = String(error);
          });
        }
        break;
      case "back": if (active?.view.webContents.navigationHistory.canGoBack()) active.view.webContents.navigationHistory.goBack(); break;
      case "forward": if (active?.view.webContents.navigationHistory.canGoForward()) active.view.webContents.navigationHistory.goForward(); break;
      case "reload": active?.view.webContents.reload(); break;
      case "stop": active?.view.webContents.stop(); break;
      default: throw new Error("Unknown browser command.");
    }
    this.publish();
    return this.snapshot();
  }

  dispose(): void {
    if (this.disposed) return;
    this.saveTabs();
    this.detach();
    this.disposed = true;
    for (const entry of this.tabs.values()) {
      if (!entry.view.webContents.isDestroyed()) entry.view.webContents.close();
    }
    this.tabs.clear();
    this.order.length = 0;
  }
}
