import {
  Archive,
  Check,
  Copy,
  FileDiff,
  GitFork,
  PanelsTopLeft,
  PanelRightClose,
  PanelRightOpen,
  Settings,
  UserRound,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import type { ContextCompactionProgress, ContextReport } from "../types/loom";
import { ContextMeter } from "./ContextMeter";
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
  artifactOpen?: boolean;
  artifactCount?: number;
  agentsOpen?: boolean;
  agentCount?: number;
  accountAuthenticated?: boolean;
  accountLabel?: string;
  accountAvatar?: string;
  context?: ContextReport | null;
  compacting?: boolean;
  compactionProgress?: ContextCompactionProgress | null;
  onCompactContext?(): void;
  onOpenProfile(): void;
  onOpenAccount(): void;
  onOpenSettings(): void;
  onToggleSidebar(): void;
  onToggleInspector(): void;
  onToggleReview(): void;
  onToggleArtifacts(): void;
  onToggleAgents(): void;
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


// Keep workspace icons on the same grid with a consistent stroke weight.
function WorkspaceEntryIcon({ kind }: { kind: "agents" | "review" | "preview" }) {
  const Icon = { agents: GitFork, review: FileDiff, preview: PanelsTopLeft }[kind];
  return <Icon className="header-workspace-icon" data-workspace-icon={kind} size={18} strokeWidth={1.75} aria-hidden="true" focusable="false" />;
}
function ActivityTraceIcon({ size = 16 }: { size?: number }) {
  const trace = "M2.25 10h3.2l1.8-5.6 3.25 11.15 2.2-7.2 1.55 3.05h3.5";
  return (
    <svg
      className="header-activity-trace"
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.55"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path className="header-activity-base" d={trace} />
      <path className="header-activity-runner" d={trace} />
    </svg>
  );
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
  artifactOpen = false,
  artifactCount = 0,
  agentsOpen = false,
  agentCount = 0,
  accountAuthenticated = false,
  accountLabel = "",
  accountAvatar = "",
  context = null,
  compacting = false,
  compactionProgress = null,
  onCompactContext,
  onOpenProfile,
  onOpenAccount,
  onOpenSettings,
  onToggleSidebar,
  onToggleInspector,
  onToggleReview,
  onToggleArtifacts,
  onToggleAgents,
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
  const artifactLabel = language === "zh-CN" ? "产物" : "Artifacts";
  const artifactTitle = artifactCount > 0
    ? (language === "zh-CN" ? `查看产物 · ${artifactCount} 个文件` : `View artifacts · ${artifactCount} files`)
    : (language === "zh-CN" ? "打开产物侧栏" : "Open artifacts");
  const agentsLabel = language === "zh-CN" ? "子代理" : "Agents";
  const agentsTitle = language === "zh-CN" ? "打开子代理工作区" : "Open sub-agent workspace";
  const profileTitle = language === "zh-CN" ? "个人主页与使用洞察" : "Profile & usage insights";
  const accountTitle = accountAuthenticated
    ? [
        language === "zh-CN" ? "Loom 账号" : "Loom account",
        accountLabel,
      ].filter(Boolean).join(" · ")
    : (language === "zh-CN" ? "登录 Loom" : "Sign in to Loom");

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
          <svg className="thread-sidebar-triangle-mark" viewBox="0 0 12 14" aria-hidden="true">
            <path d="M2.55 1.42c-.82-.48-1.85.11-1.85 1.06v9.04c0 .95 1.03 1.54 1.85 1.06l7.82-4.52c.82-.47.82-1.65 0-2.12L2.55 1.42Z" />
          </svg>
        </button>
      </div>

      <div className="thread-header-main">
        <div className="thread-header-copy">
          <div className="thread-title-line">
            <strong title={title}>{title}</strong>
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
            busy={running}
            onCompact={onCompactContext}
          />
        ) : null}

        <button
          type="button"
          className={`thread-review-button header-workspace-entry thread-agent-button ${agentsOpen ? "active" : ""} ${agentCount > 0 ? "has-agents" : "is-empty"}`}
          onClick={onToggleAgents}
          title={agentsTitle}
          aria-label={agentsTitle}
          aria-pressed={agentsOpen}
        >
          <WorkspaceEntryIcon kind="agents" />
          <span className="thread-agent-label">{agentsLabel}</span>
          {agentCount > 0 ? <span className="thread-agent-count">{agentCount}</span> : null}
        </button>

        <button
          type="button"
          className={`thread-review-button header-workspace-entry thread-review-check-button ${reviewOpen ? "active" : ""}`}
          onClick={onToggleReview}
          title={reviewTitle}
          aria-label={reviewTitle}
          aria-pressed={reviewOpen}
        >
          <WorkspaceEntryIcon kind="review" />
          <span className="thread-review-label">{reviewLabel}</span>
          {reviewCount > 0 ? <span className="thread-review-count">{reviewCount}</span> : null}
        </button>

        <button
          type="button"
          className={`thread-review-button header-workspace-entry thread-artifact-button ${artifactOpen ? "active" : ""} ${artifactCount > 0 ? "has-artifacts" : "is-empty"}`}
          onClick={onToggleArtifacts}
          title={artifactTitle}
          aria-label={artifactTitle}
          aria-pressed={artifactOpen}
        >
          <WorkspaceEntryIcon kind="preview" />
          <span className="thread-artifact-label">{artifactLabel}</span>
          {artifactCount > 0 ? <span className="thread-artifact-count">{artifactCount}</span> : null}
        </button>

        <span className="thread-header-divider" aria-hidden="true" />

        <button
          type="button"
          className="thread-header-icon-button thread-profile-button"
          onClick={onOpenProfile}
          title={profileTitle}
          aria-label={profileTitle}
        >
          <ActivityTraceIcon size={17} />
        </button>

        <button
          type="button"
          className={`thread-header-icon-button thread-account-button ${accountAuthenticated ? "signed-in" : ""}`}
          onClick={onOpenAccount}
          title={accountTitle}
          aria-label={accountTitle}
        >
          {accountAuthenticated && accountAvatar ? (
            <img className="thread-account-avatar-image" src={accountAvatar} alt="" aria-hidden="true" />
          ) : accountAuthenticated && accountLabel ? (
            <span className="thread-account-initial" aria-hidden="true">
              {accountLabel.slice(0, 1).toUpperCase()}
            </span>
          ) : (
            <UserRound size={16} strokeWidth={1.75} />
          )}
          {accountAuthenticated ? <span className="thread-account-dot" aria-hidden="true" /> : null}
        </button>

        <button
          type="button"
          className="thread-header-icon-button thread-settings-button"
          onClick={onOpenSettings}
          title={settingsLabel}
          aria-label={settingsLabel}
        >
          <Settings size={16} strokeWidth={1.75} />
        </button>

        <button
          type="button"
          className={`thread-header-icon-button panel-toggle-button thread-inspector-button ${inspectorOpen ? "active" : ""}`}
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
