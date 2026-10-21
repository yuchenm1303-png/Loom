import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, Globe2, Plus, RotateCw, X } from "./icons";
import { useI18n } from "../i18n";
import { PanelHeader } from "./PanelHeader";
import { matchingBrowserTab, type BrowserActivityTarget } from "./browserActivityModel";
import "./browser-workspace.css";
import "./browser-activity-card.css";

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

const EMPTY: EmbeddedBrowserState = { tabs: [], activeId: null };

interface BrowserWorkspaceProps {
  hidden?: boolean;
  target?: BrowserActivityTarget | null;
  onClose(): void;
}

export function BrowserWorkspace({ hidden = false, target, onClose }: BrowserWorkspaceProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const bridge = window.loom.embeddedBrowser;
  const [state, setState] = useState<EmbeddedBrowserState>(EMPTY);
  const [address, setAddress] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<BrowserActivityTarget | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const addressRef = useRef<HTMLInputElement>(null);
  const active = useMemo(() => state.tabs.find(tab => tab.id === state.activeId), [state]);

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
      const tabId = target.status !== "closed" ? matchingBrowserTab(target, next.tabs) : null;
      if (tabId) {
        setNotice(null);
        const selected = await bridge({ action: "select-tab", tabId });
        if (!cancelled) setState(selected);
      } else setNotice(target);
    }).catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { cancelled = true; };
  }, [bridge, target]);

  useEffect(() => {
    setAddress(active?.url || "");
  }, [active?.id, active?.url]);

  // A native WebContentsView is attached over this rectangle by Electron.
  // The view is detached when the browser is hidden, or another Loom overlay
  // opens, so native Chromium cannot punch through Settings or dialogs.
  useLayoutEffect(() => {
    if (!bridge || hidden) {
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
  }, [bridge, hidden]);

  useEffect(() => {
    if (hidden) return;
    const keydown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "l") { event.preventDefault(); addressRef.current?.focus(); addressRef.current?.select(); }
      else if (key === "t") { event.preventDefault(); void send({ action: "new-tab" }); }
      else if (key === "w") {
        event.preventDefault();
        if (state.activeId) void send({ action: "close-tab", tabId: state.activeId });
      }
    };
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  }, [hidden, send, state.activeId]);

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
        closeLabel={zh ? "关闭浏览器" : "Close browser"}
        onClose={onClose}
      />
      {notice ? <div className="browser-session-notice" role="status">
        <strong>{notice.title || notice.url}</strong>
        <p>{notice.connection === "embedded"
          ? (zh ? "原标签页已关闭或当前不可用。可以在新标签页重新打开网址。" : "The original tab is closed or unavailable. You can reopen its URL in a new tab.")
          : (zh ? "这个页面由外部浏览器操作，现场仍在原窗口。在内置浏览器打开会创建新页面，不会沿用原会话。" : "This session is in an external browser. Opening here creates a new page with a separate session.")}</p>
        <button type="button" onClick={async () => { if (await send({ action: "new-tab", url: notice.url })) setNotice(null); }}>{zh ? "在新标签页打开" : "Open in new tab"}</button>
        <button type="button" onClick={() => setNotice(null)}>{zh ? "关闭提示" : "Dismiss"}</button>
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
        {active ? (
          <button
            type="button"
            className={"browser-ai-control " + (active.aiAllowed ? "enabled" : "")}
            aria-pressed={Boolean(active.aiAllowed)}
            aria-label={zh ? "允许或撤销 AI 操作此标签" : "Grant or revoke AI access to this tab"}
            title={zh ? "仅授权当前标签，仍受 Browser Use 权限和审批限制" : "This tab only; Browser Use permissions and approvals still apply"}
            onClick={() => void send({ action: "allow-ai", tabId: active.id, enabled: !active.aiAllowed })}
          >
            <span className="browser-ai-indicator" />
            {active.aiAllowed ? (zh ? "AI 已授权" : "AI allowed") : (zh ? "允许 AI 操作" : "Allow AI")}
          </button>
        ) : null}
        <span className="browser-isolated-label" title={zh ? "独立浏览器资料，网站无权访问 Loom 数据" : "Isolated browser profile"}>
          <Globe2 size={13} /> {zh ? "独立浏览" : "Isolated"}
        </span>
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
      </div>
      {error || active?.error ? <div className="browser-error" role="alert">{error || active?.error}</div> : null}
      <div className="browser-viewport" ref={viewportRef}>
        {!active?.url ? <div className="browser-new-tab">
          <Globe2 size={38} strokeWidth={1.15}/>
          <span>Loom Browser</span>
          <small>{zh ? "在上方输入网址开始浏览" : "Enter a URL above to get started"}</small>
        </div> : null}
      </div>
    </section>
  );
}
