import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, Globe2, Plus, RotateCw, X, Search, Camera, ExternalLink, Code2, Minus, Ellipsis } from "./icons";
import { useI18n } from "../i18n";
import { PanelHeader } from "./PanelHeader";
import { browserActivityOpenCommand, type BrowserActivityTarget } from "./browserActivityModel";
import "./browser-activity-card.css";
import "./browser-workspace.css";

export interface EmbeddedBrowserTab {
  id: string;
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  error?: string;
  aiAllowed?: boolean;
  agentOwned?: boolean;
  zoomPercent?: number;
  findResult?: { matches: number; activeMatchOrdinal: number };
}
export interface EmbeddedBrowserState {
  tabs: EmbeddedBrowserTab[];
  activeId: string | null;
}
export type EmbeddedBrowserCommand =
  | { action: "snapshot" }
  | { action: "zoom"; percent: number }
  | { action: "find"; text: string; forward?: boolean; next?: boolean }
  | { action: "stop-find" | "copy-screenshot" | "devtools" }
  | { action: "allow-ai"; tabId: string; enabled: boolean }
  | { action: "bounds"; bounds: { x: number; y: number; width: number; height: number } | null }
  | { action: "new-tab"; url?: string }
  | { action: "select-tab" | "close-tab"; tabId: string }
  | { action: "navigate"; url: string }
  | { action: "back" | "forward" | "reload" | "stop" | "home" };

const EMPTY: EmbeddedBrowserState = { tabs: [], activeId: null };

interface BrowserWorkspaceProps {
  hidden?: boolean;
  target?: BrowserActivityTarget | null;
  workspace?: string;
  onStartPreview?(): Promise<void> | void;
  previewDisabled?: boolean;
  onClose(): void;
}

export function BrowserWorkspace({ hidden = false, target, workspace, onStartPreview, previewDisabled, onClose }: BrowserWorkspaceProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const bridge = window.loom.embeddedBrowser;
  const [state, setState] = useState<EmbeddedBrowserState>(EMPTY);
  const [address, setAddress] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<BrowserActivityTarget | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [findOpen, setFindOpen] = useState(false);
  const [findText, setFindText] = useState("");
  const [dockOverlay, setDockOverlay] = useState(false);
  const [feedback, setFeedback] = useState("");
  const findRef = useRef<HTMLInputElement>(null);
  const toolsRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const addressRef = useRef<HTMLInputElement>(null);
  const active = useMemo(() => state.tabs.find(tab => tab.id === state.activeId), [state]);
  const pageError = error || active?.error || "";
  const localServerOffline = /ERR_CONNECTION_REFUSED|ERR_CONNECTION_CLOSED/.test(pageError)
    && /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?=[:/]|$)/i.test(active?.url || "");

  useEffect(() => {
    const disposeFind = window.loom.onEmbeddedBrowserFind?.(() => { setFindOpen(true); requestAnimationFrame(() => findRef.current?.focus()); });
    const overlay = (event: Event) => setDockOverlay(Boolean((event as CustomEvent).detail));
    window.addEventListener("loom:tool-overlay", overlay);
    return () => { disposeFind?.(); window.removeEventListener("loom:tool-overlay", overlay); };
  }, []);
  useEffect(() => {
    if (hidden) { setToolsOpen(false); setFindOpen(false); }
  }, [hidden]);
  useEffect(() => { if (findOpen) findRef.current?.focus(); }, [findOpen]);
  useEffect(() => {
    if (!toolsOpen) return;
    const dismiss = (event: PointerEvent) => { if (!toolsRef.current?.contains(event.target as Node)) setToolsOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setToolsOpen(false); toolsRef.current?.querySelector<HTMLButtonElement>("button")?.focus(); } };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [toolsOpen]);
  useEffect(() => {
    if (!feedback) return;
    const timer = window.setTimeout(() => setFeedback(""), 3200);
    return () => window.clearTimeout(timer);
  }, [feedback]);

  const send = useCallback(async (command: EmbeddedBrowserCommand): Promise<boolean> => {
    if (!bridge) return false;
    try {
      const next = await bridge(command);
      // Navigation events can arrive before this RPC returns. Snapshot is still
      // useful as the authoritative ordering for tab create/close/select.
      setState(next);
      setError("");
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    }
  }, [bridge]);

  useEffect(() => {
    if (!findOpen || hidden) return;
    const timer = window.setTimeout(() => void send({ action: "find", text: findText }), 160);
    return () => window.clearTimeout(timer);
  }, [findOpen, findText, active?.id, hidden, send]);

  useEffect(() => {
    if (!bridge) return;
    const disposeState = window.loom.onEmbeddedBrowserChange?.(setState);
    const disposeFocus = window.loom.onEmbeddedBrowserFocusAddress?.(() => addressRef.current?.focus());
    void send({ action: "snapshot" });
    return () => { disposeState?.(); disposeFocus?.(); };
  }, [bridge, send]);

  useEffect(() => {
    if (!bridge || !target) return;
    let cancelled = false;
    void bridge({ action: "snapshot" }).then(async next => {
      if (cancelled) return;
      const command = browserActivityOpenCommand(target, next.tabs);
      const selected = await bridge(command);
      if (cancelled) return;
      setState(selected);
      setError("");
      setNotice(command.action === "new-tab" ? target : null);
    }).catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { cancelled = true; };
  }, [bridge, target]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 6500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    setAddress(active?.url || "");
  }, [active?.id, active?.url]);

  // A native WebContentsView is attached over this rectangle by Electron.
  // The view is detached when the browser is hidden, or another Loom overlay
  // opens, so native Chromium cannot punch through Settings or dialogs.
  useLayoutEffect(() => {
    if (!bridge || hidden || toolsOpen || dockOverlay) {
      void bridge?.({ action: "bounds", bounds: null }).catch(() => {});
      return;
    }
    const element = viewportRef.current;
    if (!element) return;
    let frame = 0;
    let suspended = false;
    const dock = element.closest(".pl-dock");
    const sync = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        // Follow the shared sidebar's actual motion, including its CSS fallback.
        // Native views cannot participate in renderer snapshots or transforms.
        const moving = document.body.classList.contains("loom-panel-motion")
          || dock?.getAnimations().some(animation => animation.playState === "running");
        if (moving) {
          if (!suspended) void bridge({ action: "bounds", bounds: null }).catch(() => {});
          suspended = true;
          sync();
          return;
        }
        suspended = false;
        const rect = element.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        void bridge({
          action: "bounds",
          bounds: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
        }).catch(() => {});
      });
    };
    const resize = new ResizeObserver(sync);
    const motion = new MutationObserver(sync);
    motion.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    resize.observe(element);
    window.addEventListener("resize", sync);
    window.addEventListener("scroll", sync, true);
    sync();
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      motion.disconnect();
      window.removeEventListener("resize", sync);
      window.removeEventListener("scroll", sync, true);
      void bridge({ action: "bounds", bounds: null }).catch(() => {});
    };
  }, [bridge, hidden, toolsOpen, dockOverlay]);

  useEffect(() => {
    if (hidden) return;
    const keydown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "l") { event.preventDefault(); addressRef.current?.focus(); addressRef.current?.select(); }
      else if (key === "f") { event.preventDefault(); setFindOpen(true); findRef.current?.focus(); }
      else if (["-", "=", "+", "0"].includes(key)) { event.preventDefault(); void send({ action: "zoom", percent: key === "0" ? 100 : Math.max(50, Math.min(200, (active?.zoomPercent || 100) + (key === "-" ? -10 : 10))) }); }
      else if (key === "t") { event.preventDefault(); void send({ action: "new-tab" }); }
      else if (key === "w") {
        event.preventDefault();
        if (state.activeId) void send({ action: "close-tab", tabId: state.activeId });
      }
    };
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  }, [hidden, send, state.activeId, active?.zoomPercent]);

  const navigate = (event: FormEvent) => {
    event.preventDefault();
    if (address.trim()) void send({ action: "navigate", url: address });
  };

  if (!bridge) {
    return <section className="browser-workspace browser-unavailable">
      <p>{zh ? "内置浏览器仅在 Loom 桌面端可用。" : "The embedded browser is available in Loom Desktop."}</p>
      <button onClick={onClose}>{zh ? "返回对话" : "Back to chat"}</button>
    </section>;
  }

  return (
    <section className="browser-workspace" aria-label={zh ? "内置浏览器" : "Embedded browser"}>
      <PanelHeader
        mark={<Globe2 size={17} />}
        title={zh ? "浏览器" : "Browser"}
        subtitle={workspace ? (zh ? "项目浏览与预览" : "Project browsing & preview") : undefined}
        closeLabel={zh ? "关闭浏览器" : "Close browser"}
        onClose={onClose}
      />
      {notice ? <div className="browser-session-notice" role="status">
        <span>{notice.connection === "embedded"
          ? (zh ? "原标签页已不可用，已在新标签页重新打开。" : "The original tab is unavailable. Reopened in a new tab.")
          : (zh ? "已打开页面，外部浏览器的登录状态不会同步。" : "Opened in Loom browser with a separate session. External browser sign-in is not shared.")}</span>
        <button type="button" aria-label={zh ? "关闭提示" : "Dismiss"} onClick={() => setNotice(null)}><X size={13}/></button>
      </div> : null}
      <div className="browser-tabs" role="tablist" aria-label={zh ? "浏览器标签页" : "Browser tabs"}>
        {state.tabs.map(tab => (
          <div key={tab.id} className={"browser-tab " + (tab.id === state.activeId ? "active" : "")}>
            <button
              type="button"
              role="tab"
              aria-selected={tab.id === state.activeId}
              className="browser-tab-select"
              title={tab.title || tab.url}
              onClick={() => void send({ action: "select-tab", tabId: tab.id })}
            >
              <Globe2 size={13} aria-hidden />
              <span>{tab.title || (zh ? "新标签页" : "New tab")}</span>
              {tab.loading ? <span className="browser-tab-loading" aria-label="Loading" /> : null}
            </button>
            <button
              className="browser-tab-close"
              type="button"
              aria-label={(zh ? "关闭 " : "Close ") + (tab.title || "tab")}
              onClick={() => void send({ action: "close-tab", tabId: tab.id })}
            >
              <X size={13} />
            </button>
          </div>
        ))}
        <button type="button" className="browser-icon-button browser-add-tab" title={zh ? "新标签页" : "New tab"}
          aria-label={zh ? "新标签页" : "New tab"}
          onClick={() => void send({ action: "new-tab" })}><Plus size={16} /></button>
        <div className="browser-tabs-spacer" />

      </div>
      <div className="browser-navigation">
        <button type="button" className="browser-icon-button" disabled={!active?.canGoBack}
          title={zh ? "后退" : "Back"} aria-label={zh ? "后退" : "Back"}
          onClick={() => void send({ action: "back" })}><ArrowLeft size={17}/></button>
        <button type="button" className="browser-icon-button" disabled={!active?.canGoForward}
          title={zh ? "前进" : "Forward"} aria-label={zh ? "前进" : "Forward"}
          onClick={() => void send({ action: "forward" })}><ArrowRight size={17}/></button>
        <button type="button" className="browser-icon-button"
          title={active?.loading ? (zh ? "停止" : "Stop") : (zh ? "刷新" : "Reload")}
          aria-label={active?.loading ? (zh ? "停止" : "Stop") : (zh ? "刷新" : "Reload")}
          onClick={() => void send({ action: active?.loading ? "stop" : "reload" })}><RotateCw size={16} className={active?.loading ? "browser-spinning" : ""}/></button>
        <button type="button" className="browser-icon-button" title={zh ? "主页" : "Home"}
          aria-label={zh ? "主页" : "Home"}
          onClick={() => void send({ action: "home" })}><Globe2 size={16}/></button>
        <form className="browser-address-form" onSubmit={navigate}>
          <Globe2 size={15} aria-hidden />
          <input ref={addressRef} type="text" autoComplete="off" spellCheck={false}
            aria-label={zh ? "网址或搜索内容" : "Address or search"}
            placeholder={zh ? "输入网址或搜索" : "Search or enter address"} value={address}
            onChange={event => setAddress(event.target.value)}
            onKeyDown={event => { if (event.key === "Escape") { setAddress(active?.url || ""); addressRef.current?.blur(); } }}
          />
        </form>
        <button type="button" className={"browser-ai-control " + (active?.aiAllowed ? "enabled" : "")} disabled={!active}
          aria-pressed={Boolean(active?.aiAllowed)} title={zh ? "仅允许 AI 操作当前标签页" : "Allow AI to control this tab only"}
          onClick={() => active && void send({ action: "allow-ai", tabId: active.id, enabled: !active.aiAllowed })}>
          <span className="browser-ai-indicator" />{active?.aiAllowed ? (zh ? "AI 已授权" : "AI allowed") : (zh ? "授权 AI" : "Allow AI")}
        </button>
        <div className="browser-tools" ref={toolsRef}>
          <button type="button" className="browser-icon-button" aria-label={zh ? "页面工具" : "Page tools"} title={zh ? "页面工具" : "Page tools"}
            aria-expanded={toolsOpen} onClick={() => setToolsOpen(value => !value)}><Ellipsis size={16}/></button>
          {toolsOpen ? <div className="browser-tools-menu" aria-label={zh ? "页面工具" : "Page tools"}>
            <span className="browser-tools-caption">{zh ? "页面" : "PAGE"}</span>
            <div className="browser-zoom"><span>{zh ? "缩放" : "Zoom"}</span>
              <button type="button" aria-label={zh ? "缩小" : "Zoom out"} disabled={!active?.url || (active.zoomPercent || 100) <= 50} onClick={() => void send({ action: "zoom", percent: Math.max(50, (active?.zoomPercent || 100) - 10) })}><Minus size={14}/></button>
              <button type="button" disabled={!active?.url} onClick={() => void send({ action: "zoom", percent: 100 })}>{active?.zoomPercent || 100}%</button>
              <button type="button" aria-label={zh ? "放大" : "Zoom in"} disabled={!active?.url || (active.zoomPercent || 100) >= 200} onClick={() => void send({ action: "zoom", percent: Math.min(200, (active?.zoomPercent || 100) + 10) })}><Plus size={14}/></button>
            </div>
            <button type="button" disabled={!active?.url} onClick={() => { setToolsOpen(false); setFindOpen(true); }}><Search size={15}/>{zh ? "页内查找" : "Find in page"}<kbd>Ctrl F</kbd></button>
            <button type="button" disabled={!active?.url} onClick={async () => { setToolsOpen(false); if (await send({ action: "copy-screenshot" })) setFeedback(zh ? "截图已复制，可粘贴到对话" : "Screenshot copied. Paste it into the conversation."); }}><Camera size={15}/>{zh ? "复制页面截图" : "Copy screenshot"}</button>
            <button type="button" disabled={!active?.url} onClick={() => { setToolsOpen(false); void window.loom.openExternal(active!.url).catch(cause => setError(String(cause))); }}><ExternalLink size={15}/>{zh ? "在外部浏览器打开" : "Open externally"}</button>
            <button type="button" disabled={!active?.url} onClick={() => { setToolsOpen(false); void send({ action: "devtools" }); }}><Code2 size={15}/>{zh ? "开发者工具" : "Developer tools"}</button>
            <p>{zh ? "独立的网站资料；AI 授权仅对当前标签有效。" : "Separate website profile. AI access applies to this tab only."}</p>
          </div> : null}
        </div>
      </div>
      {findOpen ? <div className="browser-find" role="search">
        <Search size={14}/><input ref={findRef} value={findText} aria-label={zh ? "页内查找" : "Find in page"} placeholder={zh ? "查找页面内容" : "Find on this page"}
          onChange={event => setFindText(event.target.value)} onKeyDown={event => {
            if (event.key === "Enter") { event.preventDefault(); void send({ action: "find", text: findText, next: true, forward: !event.shiftKey }); }
            if (event.key === "Escape") { setFindOpen(false); void send({ action: "stop-find" }); }
          }}/><span aria-live="polite">{findText && active?.findResult ? `${active.findResult.activeMatchOrdinal}/${active.findResult.matches}` : ""}</span>
        <button type="button" aria-label={zh ? "上一个结果" : "Previous match"} onClick={() => void send({ action: "find", text: findText, next: true, forward: false })}><ArrowLeft size={14}/></button>
        <button type="button" aria-label={zh ? "下一个结果" : "Next match"} onClick={() => void send({ action: "find", text: findText, next: true })}><ArrowRight size={14}/></button>
        <button type="button" aria-label={zh ? "关闭查找" : "Close find"} onClick={() => { setFindOpen(false); void send({ action: "stop-find" }); }}><X size={14}/></button>
      </div> : null}
      {feedback ? <div className="browser-feedback" role="status">{feedback}</div> : null}
      {pageError ? <div className="browser-error" role="alert">{localServerOffline
        ? (zh ? "本地预览服务未运行，页面暂时无法打开。重新启动生成此页面的预览服务后，点击刷新。" : "The local preview server is unavailable. Restart the preview server, then reload this page.")
        : pageError}</div> : null}
      <div className="browser-viewport" ref={viewportRef}>
        {!active?.url ? <div className="browser-new-tab">
          <Globe2 size={38} strokeWidth={1.15}/>
          <span>{zh ? "浏览、预览，继续工作" : "Browse. Preview. Keep going."}</span>
          <small>{zh ? "打开一个网址，或让 Loom 启动当前项目的预览。" : "Open a URL, or ask Loom to start your project preview."}</small>
          <form className="browser-start-form" onSubmit={navigate}><input aria-label={zh ? "打开网址" : "Open a URL"} value={address} onChange={event => setAddress(event.target.value)} placeholder="https://"/><button type="submit" disabled={!address.trim()}>{zh ? "打开" : "Open"}<ArrowRight size={14}/></button></form>
          {workspace && onStartPreview ? <button type="button" className="browser-start-preview" disabled={previewDisabled || !active?.aiAllowed} title={!active?.aiAllowed ? (zh ? "请先在地址栏旁授权 AI 操作此标签" : "Allow AI access above before starting the preview") : undefined} onClick={async () => { try { await onStartPreview(); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } }}><Code2 size={16}/>{zh ? "让 Loom 启动项目预览" : "Ask Loom to start project preview"}</button> : null}
          <small>{zh ? "网站会话保留在本机。启动预览前，请在地址栏旁授权 AI。" : "Website sessions stay here. Allow AI access above to start a project preview."}</small>
        </div> : null}
      </div>
    </section>
  );
}
