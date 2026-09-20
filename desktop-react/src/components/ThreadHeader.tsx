import {
  Archive,
  Check,
  Copy,
  FileDiff,
  Folder,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Settings,
  UserRound,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import "./thread-header.css";
import "./thread-review-entry.css";

interface ThreadHeaderProps {
  title: string;
  workspace: string;
  connection: "connecting" | "ready" | "error";
  status?: string;
  running: boolean;
  archived: boolean;
  model?: string;
  permissionMode?: string;
  sidebarOpen: boolean;
  inspectorOpen: boolean;
  reviewOpen: boolean;
  reviewCount?: number;
  accountAuthenticated?: boolean;
  onOpenAccount(): void;
  onOpenSettings(): void;
  onToggleSidebar(): void;
  onToggleInspector(): void;
  onToggleReview(): void;
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
  archived,
  sidebarOpen,
  inspectorOpen,
  reviewOpen,
  reviewCount = 0,
  accountAuthenticated = false,
  onOpenAccount,
  onOpenSettings,
  onToggleSidebar,
  onToggleInspector,
  onToggleReview,
}: ThreadHeaderProps) {
  const { language, t } = useI18n();
  const [copied, setCopied] = useState(false);

  const state = useMemo(() => {
    if (archived) return { label: t("common.archived"), tone: "archived" };
    if (status === "waiting_approval") return { label: t("common.approval"), tone: "approval" };
    if (running) return { label: t("common.working"), tone: "working" };
    if (connection === "error") return { label: language === "zh-CN" ? "连接中断" : "Disconnected", tone: "error" };
    if (connection === "connecting") return { label: t("common.connecting"), tone: "connecting" };
    return { label: t("common.ready"), tone: "ready" };
  }, [archived, connection, running, status, t, language]);

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
  const settingsLabel = t("common.openSettings");
  const inspectorLabel = inspectorOpen ? t("common.hideInspector") : t("common.openInspector");
  const sidebarLabel = sidebarOpen
    ? (language === "zh-CN" ? "收起会话侧栏" : "Hide conversation sidebar")
    : (language === "zh-CN" ? "展开会话侧栏" : "Open conversation sidebar");
  const reviewLabel = language === "zh-CN" ? "审查" : "Review";
  const reviewTitle = language === "zh-CN" ? "审查当前对话中的文件更改" : "Review file changes from this conversation";

  return (
    <header className="thread-header polished-thread-header">
      <div className="thread-header-leading">
        <button
          type="button"
          className={`thread-header-icon-button panel-toggle-button ${sidebarOpen ? "active" : ""}`}
          onClick={onToggleSidebar}
          title={sidebarLabel}
          aria-label={sidebarLabel}
          aria-pressed={sidebarOpen}
        >
          {sidebarOpen
            ? <PanelLeftClose size={16} strokeWidth={1.75} />
            : <PanelLeftOpen size={16} strokeWidth={1.75} />}
        </button>
      </div>

      <div className="thread-header-main">
        <div className="thread-header-copy">
          <div className="thread-title-line">
            <strong title={title}>{title}</strong>
            <span className={`thread-state-dot ${state.tone}`} aria-hidden="true" />
          </div>

          <button
            type="button"
            className={`workspace-path-button ${copied ? "copied" : ""}`}
            onClick={() => void copyWorkspace()}
            title={workspace ? t("common.copyWorkspacePath", { path: workspace }) : localWorkspace}
            disabled={!workspace}
          >
            <Folder size={12.5} strokeWidth={1.75} aria-hidden="true" />
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

        <button
          type="button"
          className={`thread-review-button ${reviewOpen ? "active" : ""}`}
          onClick={onToggleReview}
          title={reviewTitle}
          aria-label={reviewTitle}
          aria-pressed={reviewOpen}
        >
          <FileDiff size={14.5} strokeWidth={1.8} />
          <span className="thread-review-label">{reviewLabel}</span>
          {reviewCount > 0 ? <span className="thread-review-count">{reviewCount}</span> : null}
        </button>

        <span className="thread-header-divider" aria-hidden="true" />

        <button
          type="button"
          className={`thread-header-icon-button thread-account-button ${accountAuthenticated ? "signed-in" : ""}`}
          onClick={onOpenAccount}
          title={language === "zh-CN" ? "Loom 账号" : "Loom account"}
          aria-label={language === "zh-CN" ? "Loom 账号" : "Loom account"}
        >
          <UserRound size={16} strokeWidth={1.75} />
          {accountAuthenticated ? <span className="thread-account-dot" aria-hidden="true" /> : null}
        </button>

        <button
          type="button"
          className="thread-header-icon-button"
          onClick={onOpenSettings}
          title={settingsLabel}
          aria-label={settingsLabel}
        >
          <Settings size={16} strokeWidth={1.75} />
        </button>

        <button
          type="button"
          className={`thread-header-icon-button panel-toggle-button ${inspectorOpen ? "active" : ""}`}
          onClick={onToggleInspector}
          title={inspectorLabel}
          aria-label={inspectorLabel}
          aria-pressed={inspectorOpen}
        >
          {inspectorOpen
            ? <PanelRightClose size={16} strokeWidth={1.75} />
            : <PanelRightOpen size={16} strokeWidth={1.75} />}
        </button>
      </div>
    </header>
  );
}
