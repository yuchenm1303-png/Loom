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
  aiAllowed?: boolean;
}
export interface EmbeddedBrowserState {
  tabs: EmbeddedBrowserTab[];
  activeId: string | null;
}
export type EmbeddedBrowserCommand =
  | { action: "snapshot" }
  | { action: "allow-ai"; tabId: string; enabled: boolean }
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
  private readonly aiAllowed = new Set<string>();
  private readonly aiLeases = new Map<string,string>();
  private readonly aiSignatures = new Map<string,string>();
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

  private addTab(url: string, activate = true, existingView?: WebContentsView, skipLoad = false): void {
    if (this.tabs.size >= MAX_TABS) throw new Error("Maximum of 16 browser tabs.");
    const id = crypto.randomUUID();
    const view = existingView ?? new WebContentsView({
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
    view.webContents.setWindowOpenHandler((details) => {
      // Preserve window.opener for real OAuth sign-in popups. A plain loadURL
      // into a new tab would break popup-based Google/GitHub authentication.
      if ((details.url !== "about:blank" && !validNavigation(details.url))
          || this.tabs.size >= MAX_TABS) return { action: "deny" };
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          webPreferences: {
            partition: PARTITION,
            sandbox: true,
            contextIsolation: true,
            nodeIntegration: false,
            webSecurity: true,
          },
        },
        createWindow: (options) => {
          // In foreground popups Chromium has already constructed a WebContents
          // with the correct opener relationship. Adopt it rather than opening
          // an unrelated tab and losing OAuth postMessage callbacks.
          const existing = (options as typeof options & { webContents?: Electron.WebContents }).webContents;
          const popup = existing
            ? new WebContentsView({ webContents: existing })
            : new WebContentsView({ webPreferences: {
              partition: PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true,
            } });
          this.addTab(details.url === "about:blank" ? "" : details.url, details.disposition !== "background-tab", popup, true);
          if (!existing && validNavigation(details.url)) {
            void popup.webContents.loadURL(details.url).catch(() => {});
          }
          return popup.webContents;
        },
      };
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
    if (url && !skipLoad) void view.webContents.loadURL(url).catch(error => {
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
    this.aiAllowed.delete(id);
    this.aiLeases.delete(id);
    this.aiSignatures.delete(id);
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
      tabs: this.order.map(id => ({ ...this.tabs.get(id)!.state, aiAllowed: this.aiAllowed.has(id) })),
      activeId: this.activeId,
    };
  }

  async command(command: EmbeddedBrowserCommand): Promise<EmbeddedBrowserState> {
    if (this.disposed) throw new Error("Browser workspace is closed.");
    const active = this.tabs.get(this.activeId || "");
    switch (command.action) {
      case "snapshot": break;
      case "allow-ai":
        if (!this.tabs.has(command.tabId)) throw new Error("Missing browser tab.");
        if (command.enabled) this.aiAllowed.add(command.tabId);
        else { this.aiAllowed.delete(command.tabId); this.aiLeases.delete(command.tabId); this.aiSignatures.delete(command.tabId); }
        break;
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


  private async agentDom(entry: InternalTab): Promise<{ dom: string; signature: string; viewport: unknown }> {
    if (!entry.state.url || !validNavigation(entry.state.url)) {
      return { dom: "", signature: "", viewport: {} };
    }
    const script = "(() => {"
      + "const nodes=[...document.querySelectorAll('a[href],button,input,textarea,select,[role=\"button\"],[contenteditable=\"true\"],[tabindex]')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'}).slice(0,250);"
      + "const dom=(document.body?.innerText||'').slice(0,8000)+'\\nElements:\\n'+nodes.map((e,i)=>i+': <'+e.tagName.toLowerCase()+'> '+(e.getAttribute('aria-label')||e.innerText||e.getAttribute('placeholder')||'').trim().slice(0,140)).join('\\n');"
      + "const signature=nodes.map(e=>e.tagName+':'+(e.getAttribute('href')||'')+':'+(e.getAttribute('aria-label')||'')+':'+e.outerHTML.slice(0,100)).join('|').slice(0,20000);"
      + "return {dom,signature,viewport:{width:innerWidth,height:innerHeight}}"
      + "})()";
    const payload = await entry.view.webContents.executeJavaScript(script);
    return { dom: String(payload?.dom || "").slice(0,29000),
      signature: String(payload?.signature || ""),
      viewport: payload?.viewport || {} };
  }

  private async agentElement(entry: InternalTab, index: number, signature: string,
    action: string, value: string, clear: boolean): Promise<void> {
    if (!Number.isInteger(index) || index < 0 || index > 249) {
      throw new Error("Invalid element index; refresh browser_state.");
    }
    const current = await this.agentDom(entry);
    if (current.signature !== signature) throw new Error("Page elements changed; refresh browser_state.");
    const argumentsJson = JSON.stringify({ index, action, value, clear });
    const script = "(() => {const opts=" + argumentsJson + ";"
      + "const nodes=[...document.querySelectorAll('a[href],button,input,textarea,select,[role=\"button\"],[contenteditable=\"true\"],[tabindex]')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'}).slice(0,250);"
      + "const e=nodes[opts.index];if(!e)return false;"
      + "if(opts.action==='click'){e.click();return true}"
      + "if(opts.action==='hover'){e.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));return true}"
      + "if(opts.action==='select_option'){if(!(e instanceof HTMLSelectElement))return false;e.value=opts.value;e.dispatchEvent(new Event('change',{bubbles:true}));return true}"
      + "if(opts.action==='type_text'){e.focus();if(opts.clear&&'value' in e){const p=e instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;const setter=Object.getOwnPropertyDescriptor(p,'value')?.set;if(setter)setter.call(e,'');else e.value='';e.dispatchEvent(new Event('input',{bubbles:true}))}return true}"
      + "return false})()";
    const found = await entry.view.webContents.executeJavaScript(script);
    if (!found) throw new Error("Element is no longer available; refresh browser_state.");
    if (action === "type_text" && value) await entry.view.webContents.insertText(value);
  }

  // Browser-use tool handlers still enforce user entitlements, approval and URL
  // policy; this is the additional local consent/lease boundary in Electron.
  async agentCommand(action: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const owner = String(args.session_id || "");
    if (!owner || owner.length > 180) throw new Error("Missing AI session.");
    if (action === "finish_turn" || action === "release_tabs") {
      if (action === "release_tabs") {
        for (const [id, leasedTo] of this.aiLeases) {
          if (leasedTo === owner) this.aiLeases.delete(id);
        }
      }
      return { released: 1 };
    }
    const tabId = String(args.tab_id || this.activeId || "");
    let entry = this.tabs.get(tabId);
    if (!entry || !this.aiAllowed.has(tabId)) {
      throw new Error("Enable AI for this tab inside Loom Browser first.");
    }
    const lease = this.aiLeases.get(tabId);
    if (lease && lease !== owner) throw new Error("Tab is controlled by another task.");
    this.aiLeases.set(tabId, owner);
    const wc = entry.view.webContents;
    switch (action) {
      case "state": case "tabs": break;
      case "navigate": {
        const url = normalizeBrowserAddress(String(args.url || ""));
        if (args.new_tab === true) {
          this.addTab(url, true);
          const newId = this.activeId || "";
          this.aiAllowed.add(newId);
          this.aiLeases.set(newId, owner);
          entry = this.tabs.get(newId)!;
        } else {
          await wc.loadURL(url);
        }
        break;
      }
      case "switch_tab": this.activeId = tabId; this.syncView(); break;
      case "go_back": if (wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack(); break;
      case "go_forward": if (wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward(); break;
      case "refresh": wc.reload(); break;
      case "click": case "type_text": case "hover": case "select_option":
        await this.agentElement(entry, Number(args.index), this.aiSignatures.get(tabId) || "",
          action, action === "type_text" ? String(args.text || "") : String(args.value || ""), args.clear !== false);
        break;
      case "send_text": {
        const value = String(args.text || "");
        if (!value || value.length > 8000) throw new Error("Invalid input text.");
        await wc.insertText(value);
        break;
      }
      case "scroll": {
        const amount = Math.max(1, Math.min(3000, Number(args.amount) || 400));
        const signed = args.direction === "up" ? -amount : amount;
        await wc.executeJavaScript("window.scrollBy(0," + signed + ")");
        break;
      }
      case "press_key": {
        const key = String(args.key || "");
        if (!/^[A-Za-z0-9]$|^(Enter|Tab|Escape|Backspace|ArrowUp|ArrowDown|ArrowLeft|ArrowRight)$/.test(key)) {
          throw new Error("Unsupported keyboard key.");
        }
        wc.sendInputEvent({ type:"keyDown", keyCode:key });
        wc.sendInputEvent({ type:"keyUp", keyCode:key });
        break;
      }
      case "click_at": {
        const x = Number(args.x), y = Number(args.y);
        if (![x,y].every(Number.isFinite) || x < 0 || y < 0 || x > 10000 || y > 10000) {
          throw new Error("Invalid click coordinates.");
        }
        wc.sendInputEvent({ type:"mouseDown", x, y, button:"left", clickCount:1 });
        wc.sendInputEvent({ type:"mouseUp", x, y, button:"left", clickCount:1 });
        break;
      }
      case "find_text": {
        const value = String(args.text || "").slice(0,500);
        const found = await wc.executeJavaScript(
          "(document.body?.innerText||'').includes(" + JSON.stringify(value) + ")");
        if (!found) return { found:false };
        break;
      }
      case "screenshot": {
        const image = await wc.capturePage();
        return { png_base64: image.toPNG().toString("base64") };
      }
      default: throw new Error("Unsupported embedded browser action: " + action);
    }
    entry = this.tabs.get(this.activeId || "") || entry;
    if (!this.aiAllowed.has(entry.state.id)) throw new Error("AI authorization was revoked.");
    const dom = await this.agentDom(entry);
    this.aiSignatures.set(entry.state.id, dom.signature);
    this.publish();
    return {
      url: entry.state.url || "about:blank",
      title: entry.state.title,
      dom: dom.dom,
      tabs: this.order.filter(id => this.aiAllowed.has(id)).map(id => {
        const tab = this.tabs.get(id)!;
        return { tab_id:id, window_id:"loom", title:tab.state.title,
          url:tab.state.url || "about:blank", active:id === this.activeId, current_window:true };
      }),
      page_info: { tab_id:entry.state.id, window_id:"loom",
        session_id:owner, broker_id:String(args.broker_id || ""), viewport:dom.viewport },
      errors:[],
    };
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
