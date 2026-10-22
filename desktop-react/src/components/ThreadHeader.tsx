import { Archive, Check, Copy, PanelLeft } from "./icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import type { ContextCompactionProgress, ContextReport } from "../types/loom";
import { ContextMeter } from "./ContextMeter";
import { AnimatedThreadTitle } from "./AnimatedThreadTitle";
import { NewConversationRow } from "./NewConversationRow";
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
  /** Offered beside the toggle while the sidebar, and the quill in its top row, are away. */
  onNewConversation?(): Promise<void> | void;
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
  onNewConversation,
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

  // The toggle lives in two places, the sidebar's top row and here, on the same spot. When it moves, the keyboard
  // follows it instead of falling back to the page (only if it was on the toggle that just went away).
  const toggleRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(sidebarOpen);
  useEffect(() => {
    if (wasOpen.current === sidebarOpen) return;
    wasOpen.current = sidebarOpen;
    const active = document.activeElement;
    const lost = !active || active === document.body;
    if (!sidebarOpen && (lost || active?.closest(".sidebar"))) toggleRef.current?.focus({ preventScroll: true });
    if (sidebarOpen && lost) document.querySelector<HTMLElement>(".codex-sidebar-topbar .sidebar-toggle")?.focus({ preventScroll: true });
  }, [sidebarOpen]);

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
  const openSidebarLabel = language === "zh-CN" ? "展开侧栏" : "Show sidebar";
  const newConversationLabel = language === "zh-CN" ? "新对话" : "New conversation";
  return (
    <header className={`thread-header polished-thread-header${sidebarOpen ? "" : " has-sidebar-toggle"}`}>
      {/* While the sidebar is open its own top row carries the toggle and the quill; once it is closed they wait
          here, the toggle on the same spot at the window's corner. */}
      {sidebarOpen ? null : (
        <div className="thread-header-leading">
          <button
            ref={toggleRef}
            type="button"
            className="thread-header-icon-button panel-toggle-button sidebar-toggle"
            data-sidebar-control="toggle"
            onClick={onToggleSidebar}
            title={openSidebarLabel}
            aria-label={openSidebarLabel}
          >
            <PanelLeft size={17} strokeWidth={1.75} aria-hidden="true" />
          </button>
          {onNewConversation ? (
            <NewConversationRow
              className="thread-header-icon-button thread-new-conversation"
              label={newConversationLabel}
              title={`${newConversationLabel} · Ctrl N`}
              onNew={onNewConversation}
              iconOnly
            />
          ) : null}
        </div>
      )}
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
