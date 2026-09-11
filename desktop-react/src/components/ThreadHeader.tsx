import {
  Archive,
  Check,
  Copy,
  Cpu,
  Folder,
  MessageSquareText,
  PanelRightClose,
  PanelRightOpen,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import "./thread-header.css";

interface ThreadHeaderProps {
  title: string;
  workspace: string;
  connection: "connecting" | "ready" | "error";
  status?: string;
  running: boolean;
  archived: boolean;
  model?: string;
  permissionMode?: string;
  inspectorOpen: boolean;
  onOpenSettings(): void;
  onToggleInspector(): void;
}

function workspaceName(workspace: string, fallback: string): string {
  const normalized = workspace.replaceAll("\\", "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || fallback;
}

function formatPermission(value: string | undefined, fallback: string): string {
  if (!value) return fallback;
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
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
  model,
  permissionMode,
  inspectorOpen,
  onOpenSettings,
  onToggleInspector,
}: ThreadHeaderProps) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const state = useMemo(() => {
    if (archived) return { label: t("common.archived"), tone: "archived" };
    if (status === "waiting_approval") return { label: t("common.approval"), tone: "approval" };
    if (running) return { label: t("common.working"), tone: "working" };
    if (connection === "connecting") return { label: t("common.connecting"), tone: "connecting" };
    return { label: t("common.ready"), tone: "ready" };
  }, [archived, connection, running, status, t]);

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
  const modelLabel = model || t("common.defaultModel");
  const permissionLabel = formatPermission(permissionMode, t("common.defaultAccess"));
  const settingsLabel = t("common.openSettings");
  const inspectorLabel = inspectorOpen ? t("common.hideInspector") : t("common.openInspector");

  return (
    <header className="thread-header polished-thread-header">
      <div className="thread-header-main">
        <div className="thread-header-mark" aria-hidden="true">
          <MessageSquareText size={15} strokeWidth={1.8} />
        </div>

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
          <span>{state.label}</span>
        </span>

        <div className="thread-header-meta">
          <span className="thread-meta-chip model-chip" title={modelLabel}>
            <Cpu size={12.5} strokeWidth={1.75} />
            <span>{modelLabel}</span>
          </span>
          <span className="thread-meta-chip permission-chip" title={`${t("common.permission")}: ${permissionLabel}`}>
            <ShieldCheck size={12.5} strokeWidth={1.75} />
            <span>{permissionLabel}</span>
          </span>
        </div>

        <span className="thread-header-divider" aria-hidden="true" />

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
          className={`thread-header-icon-button ${inspectorOpen ? "active" : ""}`}
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
