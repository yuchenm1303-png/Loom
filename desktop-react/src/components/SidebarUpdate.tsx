import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { ArrowUp, Check, RefreshCw } from "./icons";
import type { SoftwareUpdateState } from "./softwareUpdateStatus";
import "./sidebar-update.css";

interface UpdateBridge {
  getUpdateStatus(): Promise<SoftwareUpdateState>;
  checkForUpdates(): Promise<SoftwareUpdateState>;
  installUpdate(): Promise<{ accepted: boolean; state: SoftwareUpdateState }>;
  onUpdateStatus(listener: (state: SoftwareUpdateState) => void): () => void;
}

/** A small, always reachable update action below the conversation list. */
export function SidebarUpdate() {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [state, setState] = useState<SoftwareUpdateState | null>(null);
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [upToDate, setUpToDate] = useState(false);
  const actionLock = useRef(false);
  const updates = window.loom as typeof window.loom & Partial<UpdateBridge>;

  useEffect(() => {
    if (!updates.getUpdateStatus || !updates.onUpdateStatus) return;
    let disposed = false;
    let receivedEvent = false;
    const unsubscribe = updates.onUpdateStatus((next) => {
      receivedEvent = true;
      if (!disposed) setState(next);
    });
    void updates.getUpdateStatus().then((next) => {
      if (!disposed && !receivedEvent) setState(next);
    }).catch(() => { /* Unsupported browser previews have no desktop updater. */ });
    return () => { disposed = true; unsubscribe(); };
  }, [updates]);

  // "Up to date" is a short-lived answer to a click: it takes the button's
  // place for a moment instead of pushing a new line under the footer.
  useEffect(() => {
    if (!upToDate) return;
    const timer = window.setTimeout(() => setUpToDate(false), 2600);
    return () => window.clearTimeout(timer);
  }, [upToDate]);

  if (!state?.enabled) return null;
  const ready = state.phase === "downloaded" && state.accountUpdatesAllowed !== false;
  const downloading = state.phase === "available" || state.phase === "downloading";
  const busy = pending || downloading || state.phase === "checking";
  const percent = Math.round(Math.max(0, Math.min(100, Number(state.percent) || 0)));
  // Labels stay short so the row never wraps at the compact sidebar width;
  // the tooltip carries the full sentence.
  const label = downloading ? (zh ? `下载 ${percent}%` : `${percent}%`)
    : state.phase === "checking" ? (zh ? "检查中…" : "Checking…")
    : pending ? (zh ? "更新中…" : "Updating…")
    : ready ? (zh ? "重启更新" : "Restart")
    : upToDate ? (zh ? "已是最新" : "Up to date")
    : (zh ? "更新" : "Update");
  const tone = ready ? "ready" : busy ? "busy" : upToDate ? "done" : "idle";
  const hint = state.error || (ready
    ? (zh ? "点击安装更新并重启 Loom" : "Install the update and restart Loom")
    : downloading ? (zh ? "正在后台下载更新，可继续使用 Loom" : "Downloading in the background")
    : (zh ? "检查并下载 Loom 更新" : "Check for and download Loom updates"));

  async function update() {
    if (actionLock.current || busy) return;
    actionLock.current = true;
    setPending(true);
    setFeedback("");
    setUpToDate(false);
    try {
      if (ready && updates.installUpdate) {
        const result = await updates.installUpdate();
        setState(result.state);
        if (!result.accepted) setFeedback(result.state.error || (zh ? "暂时无法重启，请稍后再试。" : "Cannot restart yet. Try again shortly."));
      } else if (updates.checkForUpdates) {
        const next = await updates.checkForUpdates();
        setState(next);
        if (next.error) setFeedback(next.error);
        else if (next.phase === "up-to-date") setUpToDate(true);
      }
    } catch (cause) {
      setFeedback(cause instanceof Error ? cause.message : String(cause));
    } finally {
      actionLock.current = false;
      setPending(false);
    }
  }

  return (
    <div className="sidebar-update">
      <button type="button" className="sidebar-update-button" data-tone={tone} disabled={busy}
        aria-busy={busy} title={feedback || hint} onClick={() => void update()}>
        {busy ? <RefreshCw size={14} spinning />
          : upToDate ? <Check size={14} />
          : <ArrowUp className="sidebar-update-arrow" size={14} />}
        <span role="status">{label}</span>
      </button>
      <span className="sidebar-update-feedback" role="alert">{feedback}</span>
    </div>
  );
}
