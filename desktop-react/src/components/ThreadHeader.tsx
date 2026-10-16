import { Archive, Check, Copy, SidebarTriangle } from "./icons";
import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import type { ContextCompactionProgress, ContextReport } from "../types/loom";
import { ContextMeter } from "./ContextMeter";
import { AnimatedThreadTitle } from "./AnimatedThreadTitle";
import "./thread-header.css";
import "./thread-review-entry.css";

interface ThreadHeaderProps {
  title: string;
  workspace: string;
  connection: "connecting" | "ready" | "error";
  status?: string;
  running: boolean;
  loading?: boolean;
  archived: boolean;
  sidebarOpen: boolean;
  onToggleSidebar(): void;
  model?: string;
  permissionMode?: string;
  context?: ContextReport | null;
  compacting?: boolean;
  compactionProgress?: ContextCompactionProgress | null;
  onCompactContext?(): void;
}

function workspaceName(workspace: string, fallback: string): string {
  const normalized = workspace.replaceAll("\\", "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || fallback;
}

async function copyText(value: string): Promise<void> {
  if (!value) return;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}


export function ThreadHeader({
  title,
  workspace,
  connection,
  status,
  running,
  loading = false,
  archived,
  sidebarOpen,
  onToggleSidebar,
  context = null,
  compacting = false,
  compactionProgress = null,
  onCompactContext,
}: ThreadHeaderProps) {
  const { language, t } = useI18n();
  const [copied, setCopied] = useState(false);

  const state = useMemo(() => {
    if (loading) return { label: language === "zh-CN" ? "加载中" : "Loading", tone: "connecting" };
    if (archived) return { label: t("common.archived"), tone: "archived" };
    if (status === "waiting_approval") return { label: t("common.approval"), tone: "approval" };
    if (running) return { label: t("common.working"), tone: "working" };
    if (connection === "error") return { label: language === "zh-CN" ? "连接中断" : "Disconnected", tone: "error" };
    if (connection === "connecting") return { label: t("common.connecting"), tone: "connecting" };
    return { label: t("common.ready"), tone: "ready" };
  }, [archived, connection, loading, running, status, t, language]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1400);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copyWorkspace = async () => {
    if (!workspace) return;
    try {
      await copyText(workspace);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const workspaceFallback = t("common.workspace");
  const localWorkspace = t("common.localWorkspace");
  const sidebarLabel = sidebarOpen
    ? (language === "zh-CN" ? "收起会话侧栏" : "Hide conversation sidebar")
    : (language === "zh-CN" ? "展开会话侧栏" : "Open conversation sidebar");
  return (
    <header className="thread-header polished-thread-header">
      <div className="thread-header-leading">
        <button
          type="button"
          className={`thread-header-icon-button panel-toggle-button thread-sidebar-triangle ${sidebarOpen ? "active is-open" : ""}`}
          onClick={onToggleSidebar}
          title={sidebarLabel}
          aria-label={sidebarLabel}
          aria-pressed={sidebarOpen}
        >
          <SidebarTriangle className="thread-sidebar-triangle-mark" viewBox="0 0 12 14" fill="currentColor" stroke="none" focusable="false" />
        </button>
      </div>
      <div className="thread-header-main">
        <div className="thread-header-copy">
          <div className="thread-title-line">
            <strong title={title}><AnimatedThreadTitle title={title} /></strong>
          </div>

          <button
            type="button"
            className={`workspace-path-button ${copied ? "copied" : ""}`}
            onClick={() => void copyWorkspace()}
            title={workspace ? t("common.copyWorkspacePath", { path: workspace }) : localWorkspace}
            disabled={!workspace}
          >
            <span className="workspace-leaf">{workspace ? workspaceName(workspace, workspaceFallback) : localWorkspace}</span>
            {workspace ? <span className="workspace-full-path">{workspace}</span> : null}
            <span className="workspace-copy-icon" aria-hidden="true">
              {copied ? <Check size={11.5} strokeWidth={2.1} /> : <Copy size={11.5} strokeWidth={1.8} />}
            </span>
          </button>
        </div>
      </div>

      <div className="thread-header-actions polished-thread-header-actions">
        <span className={`thread-status-chip ${state.tone}`} title={`Conversation status: ${state.label}`}>
          {archived ? <Archive size={12.5} strokeWidth={1.8} /> : <span className="thread-status-orb" aria-hidden="true" />}
          <span className="thread-status-label">{state.label}</span>
        </span>

        {onCompactContext ? (
          <ContextMeter
            report={context}
            compacting={compacting}
            progress={compactionProgress}
            busy={running || loading}
            onCompact={onCompactContext}
          />
        ) : null}

      </div>
    </header>
  );
}
