import {
  Archive,
  Check,
  ChevronDown,
  Copy,
  Cpu,
  Folder,
  FolderInput,
  FolderMinus,
  MessageSquareText,
  PanelRightClose,
  PanelRightOpen,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import type { ProjectRecord } from "../types/loom";
import "./thread-header.css";
import "./thread-project-menu.css";

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
  projects: ProjectRecord[];
  projectsSupported: boolean;
  currentProjectId?: string;
  projectMoveDisabled?: boolean;
  onMoveProject(projectId: string): Promise<void>;
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
  projects,
  projectsSupported,
  currentProjectId,
  projectMoveDisabled = false,
  onMoveProject,
  onOpenSettings,
  onToggleInspector,
}: ThreadHeaderProps) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [movingProject, setMovingProject] = useState(false);
  const [projectError, setProjectError] = useState("");
  const [projectMenuPosition, setProjectMenuPosition] = useState({ top: 0, left: 0 });
  const projectTriggerRef = useRef<HTMLButtonElement>(null);
  const projectMenuRef = useRef<HTMLDivElement>(null);

  const normalizedProjectId = (currentProjectId || "").trim();
  const currentProject = useMemo(
    () => projects.find((project) => project.id === normalizedProjectId) ?? null,
    [normalizedProjectId, projects],
  );

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

  useEffect(() => {
    if (!projectMenuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      const node = event.target as Node;
      if (projectTriggerRef.current?.contains(node) || projectMenuRef.current?.contains(node)) return;
      setProjectMenuOpen(false);
      setProjectError("");
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setProjectMenuOpen(false);
        setProjectError("");
      }
    };
    const close = () => {
      setProjectMenuOpen(false);
      setProjectError("");
    };
    window.addEventListener("pointerdown", handlePointerDown, true);
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown, true);
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", close);
    };
  }, [projectMenuOpen]);

  useEffect(() => {
    setProjectMenuOpen(false);
    setProjectError("");
  }, [normalizedProjectId, title]);

  const copyWorkspace = async () => {
    if (!workspace) return;
    try {
      await copyText(workspace);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const toggleProjectMenu = () => {
    if (projectMoveDisabled || movingProject) return;
    if (projectMenuOpen) {
      setProjectMenuOpen(false);
      setProjectError("");
      return;
    }
    const rect = projectTriggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const width = 276;
    const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
    setProjectMenuPosition({ top: rect.bottom + 7, left });
    setProjectError("");
    setProjectMenuOpen(true);
  };

  const moveToProject = async (projectId: string) => {
    if (projectId === normalizedProjectId) {
      setProjectMenuOpen(false);
      return;
    }
    setMovingProject(true);
    setProjectError("");
    try {
      await onMoveProject(projectId);
      setProjectMenuOpen(false);
    } catch (cause) {
      setProjectError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setMovingProject(false);
    }
  };

  const workspaceFallback = t("common.workspace");
  const localWorkspace = t("common.localWorkspace");
  const modelLabel = model || t("common.defaultModel");
  const permissionLabel = formatPermission(permissionMode, t("common.defaultAccess"));
  const settingsLabel = t("common.openSettings");
  const inspectorLabel = inspectorOpen ? t("common.hideInspector") : t("common.openInspector");
  const showProjectControl = projectsSupported && (projects.length > 0 || Boolean(normalizedProjectId));

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

        {showProjectControl ? (
          <button
            ref={projectTriggerRef}
            type="button"
            className={`thread-project-button ${normalizedProjectId ? "assigned" : ""} ${projectMenuOpen ? "open" : ""}`}
            onClick={toggleProjectMenu}
            title={currentProject ? `移动项目 · ${currentProject.name}` : "放入项目"}
            aria-label={currentProject ? `移动会话到其他项目，当前项目 ${currentProject.name}` : "放入项目"}
            aria-haspopup="menu"
            aria-expanded={projectMenuOpen}
            disabled={projectMoveDisabled || movingProject}
          >
            <FolderInput size={13.5} strokeWidth={1.75} aria-hidden="true" />
            <span>{currentProject?.name || "放入项目"}</span>
            <ChevronDown size={12.5} strokeWidth={1.8} aria-hidden="true" />
          </button>
        ) : null}

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

      {projectMenuOpen ? createPortal(
        <div
          ref={projectMenuRef}
          className="thread-project-menu"
          role="menu"
          aria-label="移动到项目"
          style={{ top: projectMenuPosition.top, left: projectMenuPosition.left }}
        >
          <div className="thread-project-menu-title">移动到项目</div>
          <button
            type="button"
            className={`thread-project-option ${!normalizedProjectId ? "selected" : ""}`}
            onClick={() => void moveToProject("")}
            disabled={movingProject}
            role="menuitemradio"
            aria-checked={!normalizedProjectId}
          >
            <span className="thread-project-option-icon"><FolderMinus size={15} strokeWidth={1.75} /></span>
            <span className="thread-project-option-copy">
              <strong>无项目</strong>
              <small>保留当前工作目录</small>
            </span>
            {!normalizedProjectId ? <Check size={14} strokeWidth={2} className="thread-project-check" /> : null}
          </button>

          {projects.length ? <div className="thread-project-menu-separator" /> : null}

          <div className="thread-project-menu-list">
            {projects.map((project) => {
              const selected = project.id === normalizedProjectId;
              return (
                <button
                  key={project.id}
                  type="button"
                  className={`thread-project-option ${selected ? "selected" : ""}`}
                  onClick={() => void moveToProject(project.id)}
                  disabled={movingProject}
                  role="menuitemradio"
                  aria-checked={selected}
                  title={project.root}
                >
                  <span className="thread-project-option-icon"><Folder size={15} strokeWidth={1.75} /></span>
                  <span className="thread-project-option-copy">
                    <strong>{project.name}</strong>
                    <small>{project.root}</small>
                  </span>
                  {selected ? <Check size={14} strokeWidth={2} className="thread-project-check" /> : null}
                </button>
              );
            })}
          </div>

          {projectError ? <div className="thread-project-menu-error">{projectError}</div> : null}
        </div>,
        document.body,
      ) : null}
    </header>
  );
}
