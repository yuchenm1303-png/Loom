import {
  ExternalLink,
  File,
  FileText,
  Folder,
  FolderOpen,
  GitBranch,
  MessageSquareText,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ProjectRecord, ThreadRecord } from "../types/loom";
import "./project-details-panel.css";

interface ProjectDetailsPanelProps {
  project: ProjectRecord | null;
  threads: ThreadRecord[];
  activeThreadId?: string;
  open: boolean;
  onClose(): void;
  onNewThread(project: ProjectRecord): Promise<void> | void;
  onOpenThread(threadId: string): Promise<void> | void;
  onSetInstructions(projectId: string, instructions: string): Promise<ProjectRecord> | void;
}

interface ProjectGitFile {
  path: string;
  status: string;
  index?: string;
  workingTree?: string;
  raw?: string;
}

interface ProjectWorkspaceTreeEntry {
  path: string;
  name: string;
  type: "directory" | "file" | "symlink" | string;
  depth: number;
  size?: number;
}

interface ProjectWorkspaceStatus {
  projectId: string;
  root: string;
  exists: boolean;
  isDirectory: boolean;
  error?: string;
  git: {
    available: boolean;
    isRepo: boolean;
    branch: string;
    summary: string;
    changedCount: number;
    changedFiles: ProjectGitFile[];
    truncated: boolean;
    error?: string;
  };
  tree: {
    entries: ProjectWorkspaceTreeEntry[];
    truncated: boolean;
    limit: number;
    maxDepth: number;
  };
}

type LoomBridge = {
  call<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
};

function threadIsBusy(thread: ThreadRecord): boolean {
  return thread.status === "running" || thread.status === "waiting_approval";
}

function relativeTime(value?: string): string {
  const stamp = Date.parse(String(value || ""));
  if (!Number.isFinite(stamp)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - stamp) / 1000));
  if (seconds < 60) return "刚刚";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function displayPath(root: string): string {
  return String(root || "").replaceAll("\\", "/");
}

function reviewPath(path?: string): void {
  window.dispatchEvent(
    new CustomEvent("loom:review-open", {
      detail: { path: String(path || "").replaceAll("\\", "/") },
    }),
  );
}

function bridge(): LoomBridge | null {
  return Reflect.get(window, "loom") as LoomBridge | null;
}

function fileStatusLabel(file: ProjectGitFile): string {
  const status = String(file.status || "").trim();
  if (status === "??") return "新增";
  if (status.includes("D")) return "删除";
  if (status.includes("R")) return "重命名";
  if (status.includes("A")) return "新增";
  if (status.includes("M")) return "修改";
  return status || "变更";
}

function formatSize(value?: number): string {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 102.4) / 10} KB`;
  return `${Math.round(bytes / 1024 / 102.4) / 10} MB`;
}

export function ProjectDetailsPanel({
  project,
  threads,
  activeThreadId,
  open,
  onClose,
  onNewThread,
  onOpenThread,
  onSetInstructions,
}: ProjectDetailsPanelProps) {
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [workspaceStatus, setWorkspaceStatus] = useState<ProjectWorkspaceStatus | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const projectId = project?.id ?? "";

  const loadWorkspaceStatus = useCallback(async (quiet = false) => {
    if (!open || !projectId) return;
    if (!quiet) setWorkspaceLoading(true);
    setWorkspaceError("");
    try {
      const client = bridge();
      if (!client) throw new Error("Loom bridge is unavailable");
      const status = await client.call<ProjectWorkspaceStatus>("project/workspace_status", { projectId });
      setWorkspaceStatus(status);
    } catch (cause) {
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (!quiet) setWorkspaceLoading(false);
    }
  }, [open, projectId]);

  useEffect(() => {
    setDraft(project?.instructions ?? "");
    setNotice("");
    setSaving(false);
  }, [project?.id, project?.instructions]);

  useEffect(() => {
    if (!open || !projectId) {
      setWorkspaceStatus(null);
      setWorkspaceError("");
      setWorkspaceLoading(false);
      return undefined;
    }
    void loadWorkspaceStatus();
    const timer = window.setInterval(() => void loadWorkspaceStatus(true), 12000);
    return () => window.clearInterval(timer);
  }, [loadWorkspaceStatus, open, projectId]);

  const projectThreads = useMemo(() => {
    if (!project) return [];
    return threads
      .filter((thread) => thread.projectId === project.id)
      .sort((left, right) => Date.parse(right.updatedAt || "") - Date.parse(left.updatedAt || ""));
  }, [project, threads]);

  const busyThreads = projectThreads.filter(threadIsBusy);
  const dirty = Boolean(project && draft !== (project.instructions ?? ""));
  const instructionsLength = draft.length;
  const git = workspaceStatus?.git;
  const changedFiles = git?.changedFiles ?? [];
  const treeEntries = workspaceStatus?.tree.entries ?? [];

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 2200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (!open || !project) return null;

  const saveInstructions = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      await onSetInstructions(project.id, draft);
      setNotice("项目 Instructions 已保存，下一轮自动生效");
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <aside className="project-details-panel" aria-label="Project details">
      <header className="project-details-header">
        <div className="project-details-mark" aria-hidden="true">
          <Folder size={18} strokeWidth={1.85} />
        </div>
        <div className="project-details-title">
          <span>项目</span>
          <strong title={project.name}>{project.name}</strong>
          <code title={project.root}>{displayPath(project.root)}</code>
        </div>
        <button type="button" className="project-details-close" onClick={onClose} aria-label="Close project details">
          <X size={16} strokeWidth={1.9} />
        </button>
      </header>

      <div className="project-details-body">
        <div className="project-overview-grid" aria-label="Project overview">
          <div className="project-overview-card">
            <span>会话</span>
            <strong>{projectThreads.length}</strong>
            <small>{busyThreads.length ? `${busyThreads.length} 个运行中` : "当前空闲"}</small>
          </div>
          <div className="project-overview-card">
            <span>Instructions</span>
            <strong>{project.instructions?.trim() ? "已配置" : "未配置"}</strong>
            <small>{instructionsLength} / 12000</small>
          </div>
        </div>

        {busyThreads.length ? (
          <div className="project-running-guard" role="status">
            <ShieldAlert size={15} strokeWidth={1.85} />
            <span>项目内有任务正在运行，移动、删除等危险操作会被保护。先让任务结束再调整归属。</span>
          </div>
        ) : null}

        <section className="project-workspace-card">
          <div className="project-card-heading">
            <div>
              <span>Workspace</span>
              <strong>工作区状态</strong>
            </div>
            <button type="button" onClick={() => void loadWorkspaceStatus()} disabled={workspaceLoading}>
              <RefreshCw size={14} strokeWidth={1.85} />
              {workspaceLoading ? "刷新中" : "刷新"}
            </button>
          </div>

          <div className="project-workspace-grid">
            <div className="project-workspace-stat">
              <GitBranch size={15} strokeWidth={1.8} />
              <span>Git</span>
              <strong>{git?.isRepo ? git.branch || "detached" : "未接入"}</strong>
              <small>{git?.summary || "当前项目不是 Git 仓库或 Git 不可用"}</small>
            </div>
            <div className="project-workspace-stat">
              <FileText size={15} strokeWidth={1.8} />
              <span>变更</span>
              <strong>{git?.changedCount ?? 0}</strong>
              <small>{git?.changedCount ? "点击文件可打开右侧 Review" : "工作区暂无 Git 变更"}</small>
            </div>
          </div>

          {workspaceError || workspaceStatus?.error || git?.error ? (
            <div className="project-workspace-warning">
              {workspaceError || workspaceStatus?.error || git?.error}
            </div>
          ) : null}

          <div className="project-changed-block">
            <div className="project-subheading">
              <span>当前变更</span>
              <small>{changedFiles.length ? `${changedFiles.length}${git?.truncated ? "+" : ""} 个文件` : "clean"}</small>
            </div>
            {changedFiles.length ? (
              <div className="project-changed-files">
                {changedFiles.slice(0, 8).map((file) => (
                  <button
                    key={`${file.status}:${file.path}`}
                    type="button"
                    className="project-changed-file"
                    onClick={() => reviewPath(file.path)}
                    title="在右侧审查栏查看"
                  >
                    <span>{fileStatusLabel(file)}</span>
                    <code>{file.path}</code>
                    <ExternalLink size={12} strokeWidth={1.9} />
                  </button>
                ))}
              </div>
            ) : (
              <p className="project-muted-line">没有检测到 Git 工作区变更。</p>
            )}
          </div>

          <div className="project-file-tree-block">
            <div className="project-subheading">
              <span>文件树</span>
              <small>{workspaceStatus?.tree.truncated ? `仅显示前 ${workspaceStatus.tree.limit} 项` : `${treeEntries.length} 项`}</small>
            </div>
            {treeEntries.length ? (
              <div className="project-file-tree">
                {treeEntries.slice(0, 36).map((entry) => {
                  const directory = entry.type === "directory";
                  return (
                    <div
                      key={entry.path}
                      className={`project-file-entry ${directory ? "directory" : "file"}`}
                      style={{ paddingLeft: `${8 + Math.max(0, Number(entry.depth || 0)) * 14}px` }}
                      title={entry.path}
                    >
                      {directory ? <FolderOpen size={13} strokeWidth={1.8} /> : <File size={13} strokeWidth={1.8} />}
                      <span>{entry.name}</span>
                      <small>{directory ? "" : formatSize(entry.size)}</small>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="project-muted-line">还没有可显示的文件。</p>
            )}
          </div>
        </section>

        <section className="project-instructions-card">
          <div className="project-card-heading">
            <div>
              <span>Project Instructions</span>
              <strong>项目级说明</strong>
            </div>
            <FileText size={16} strokeWidth={1.8} aria-hidden="true" />
          </div>
          <p>
            写给这个项目里所有任务看的固定背景、约束和偏好。保存后会从下一轮开始自动注入 Agent 运行上下文。
          </p>
          <textarea
            value={draft}
            maxLength={12000}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="例如：这个项目是 Loom 桌面端。所有修改直接提交 main；优先保持 Codex 风格；不要改动 unrelated 代码。"
            aria-label="Project instructions"
          />
          <div className="project-instructions-actions">
            <span>{notice || (dirty ? "有未保存修改" : "已同步，下一轮自动生效")}</span>
            <button type="button" onClick={() => setDraft(project.instructions ?? "")} disabled={!dirty || saving}>
              <RotateCcw size={14} strokeWidth={1.85} />
              还原
            </button>
            <button type="button" className="primary" onClick={() => void saveInstructions()} disabled={!dirty || saving}>
              <Save size={14} strokeWidth={1.85} />
              {saving ? "保存中" : "保存"}
            </button>
          </div>
        </section>

        <section className="project-thread-card">
          <div className="project-card-heading">
            <div>
              <span>Threads</span>
              <strong>项目会话</strong>
            </div>
            <button type="button" onClick={() => void onNewThread(project)}>
              <Plus size={14} strokeWidth={1.9} />
              新对话
            </button>
          </div>

          {projectThreads.length ? (
            <div className="project-thread-list-panel">
              {projectThreads.slice(0, 14).map((thread) => {
                const busy = threadIsBusy(thread);
                const selected = thread.id === activeThreadId;
                return (
                  <button
                    key={thread.id}
                    type="button"
                    className={`project-thread-entry ${selected ? "selected" : ""}`}
                    onClick={() => void onOpenThread(thread.id)}
                  >
                    <span className={`project-thread-dot ${busy ? "busy" : ""}`} aria-hidden="true" />
                    <MessageSquareText size={14} strokeWidth={1.85} aria-hidden="true" />
                    <span>{thread.title || "New conversation"}</span>
                    <small>{busy ? "运行中" : relativeTime(thread.updatedAt)}</small>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="project-empty-threads">
              <span>这个项目还没有会话。</span>
              <button type="button" onClick={() => void onNewThread(project)}>创建项目会话</button>
            </div>
          )}
        </section>

        <section className="project-roadmap-card">
          <span>下一步能力</span>
          <div>
            <small>AGENTS.md</small>
            <small>Git diff</small>
            <small>项目级模型</small>
            <small>项目级权限</small>
          </div>
        </section>
      </div>
    </aside>
  );
}
