import {
  Check,
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
import type { ProjectRecord, ThreadRecord, TranscriptItem } from "../types/loom";
import "./project-details-panel.css";
import "./project-details-git.css";

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

interface ProjectGitDiffResult {
  projectId: string;
  projectName?: string;
  root: string;
  path: string;
  paths: string[];
  diff: string;
  changedFiles: ProjectGitFile[];
  truncated: boolean;
  error?: string;
}

interface ProjectGitActionResult {
  projectId: string;
  projectName?: string;
  root: string;
  path?: string;
  message?: string;
  before?: string;
  commitSha?: string;
  summary?: string;
  git: ProjectWorkspaceStatus["git"];
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

function gitFileKey(file: ProjectGitFile): string {
  return `${file.status}:${file.path}`;
}

function fileBaseName(path: string): string {
  const normalized = String(path || "").replaceAll("\\", "/");
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "workspace";
}

function fileIsStaged(file: ProjectGitFile): boolean {
  const index = String(file.index ?? "").trim();
  const status = String(file.status || "").padEnd(2, " ");
  return Boolean(index && index !== "?") || Boolean(status[0].trim() && status[0] !== "?");
}

function fileIsUnstaged(file: ProjectGitFile): boolean {
  const working = String(file.workingTree ?? "").trim();
  const status = String(file.status || "").trim();
  return status === "??" || Boolean(working && working !== "?") || status.includes("?");
}

function suggestedCommitMessage(files: ProjectGitFile[]): string {
  if (!files.length) return "chore: update project workspace";
  if (files.length === 1) return `chore: update ${fileBaseName(files[0].path)}`;
  const added = files.filter((file) => fileStatusLabel(file) === "新增").length;
  const deleted = files.filter((file) => fileStatusLabel(file) === "删除").length;
  if (added > deleted && added >= Math.ceil(files.length / 2)) return `feat: add ${added} project files`;
  if (deleted > added && deleted >= Math.ceil(files.length / 2)) return `chore: remove ${deleted} project files`;
  return `chore: update ${files.length} project files`;
}

function formatSize(value?: number): string {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 102.4) / 10} KB`;
  return `${Math.round(bytes / 1024 / 102.4) / 10} MB`;
}

function reviewDiffItem(projectId: string, result: ProjectGitDiffResult, fallbackPath = ""): TranscriptItem {
  const now = new Date().toISOString();
  const paths = result.paths?.length
    ? result.paths
    : result.path
      ? [result.path]
      : fallbackPath
        ? [fallbackPath]
        : [];
  return {
    id: `project-diff:${projectId}:${Date.now()}:${Math.random().toString(36).slice(2)}`,
    threadId: `project:${projectId}`,
    turnId: null,
    type: "file_edit",
    status: "completed",
    diff: result.diff || "",
    paths,
    createdAt: now,
    updatedAt: now,
  };
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
  const [reviewLoadingPath, setReviewLoadingPath] = useState("");
  const [gitBusy, setGitBusy] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const [commitNotice, setCommitNotice] = useState("");
  const projectId = project?.id ?? "";

  const loadWorkspaceStatus = useCallback(async (quiet = false): Promise<ProjectWorkspaceStatus | null> => {
    if (!open || !projectId) return null;
    if (!quiet) setWorkspaceLoading(true);
    setWorkspaceError("");
    try {
      const client = bridge();
      if (!client) throw new Error("Loom bridge is unavailable");
      const status = await client.call<ProjectWorkspaceStatus>("project/workspace_status", { projectId });
      setWorkspaceStatus(status);
      return status;
    } catch (cause) {
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      if (!quiet) setWorkspaceLoading(false);
    }
  }, [open, projectId]);

  useEffect(() => {
    setDraft(project?.instructions ?? "");
    setNotice("");
    setSaving(false);
    setReviewLoadingPath("");
    setGitBusy("");
    setCommitNotice("");
  }, [project?.id, project?.instructions]);

  useEffect(() => {
    if (!open || !projectId) {
      setWorkspaceStatus(null);
      setWorkspaceError("");
      setWorkspaceLoading(false);
      setReviewLoadingPath("");
      setGitBusy("");
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
  const stagedFiles = changedFiles.filter(fileIsStaged);
  const unstagedFiles = changedFiles.filter(fileIsUnstaged);
  const treeEntries = workspaceStatus?.tree.entries ?? [];
  const gitBlocked = Boolean(gitBusy || workspaceLoading || busyThreads.length || !git?.isRepo);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 2200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (commitMessage.trim() || !stagedFiles.length) return;
    setCommitMessage(suggestedCommitMessage(stagedFiles));
  }, [commitMessage, stagedFiles]);

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

  const openProjectDiff = async (file?: ProjectGitFile) => {
    const path = String(file?.path || "").replaceAll("\\", "/");
    const loadingKey = path || "__all__";
    setReviewLoadingPath(loadingKey);
    setWorkspaceError("");
    try {
      const client = bridge();
      if (!client) throw new Error("Loom bridge is unavailable");
      const params: Record<string, unknown> = { projectId: project.id };
      if (path) params.path = path;
      const result = await client.call<ProjectGitDiffResult>("project/git_diff", params);
      const detail = {
        path: path || result.paths?.[0] || "",
        title: `${project.name} · Git diff`,
        subtitle: result.truncated ? "项目工作区当前 Git diff（已截断）" : "项目工作区当前 Git diff",
        items: [reviewDiffItem(project.id, result, path)],
      };
      window.dispatchEvent(new CustomEvent("loom:review-open-diff", { detail }));
      if (result.error) setWorkspaceError(result.error);
    } catch (cause) {
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setReviewLoadingPath("");
    }
  };

  const runGitAction = async (method: "project/git_stage" | "project/git_unstage", path = "") => {
    const key = `${method}:${path || "all"}`;
    setGitBusy(key);
    setWorkspaceError("");
    setCommitNotice("");
    try {
      const client = bridge();
      if (!client) throw new Error("Loom bridge is unavailable");
      const params: Record<string, unknown> = { projectId: project.id };
      if (path) params.path = path;
      const result = await client.call<ProjectGitActionResult>(method, params);
      setWorkspaceStatus((current) => current ? { ...current, git: result.git } : current);
      await loadWorkspaceStatus(true);
    } catch (cause) {
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setGitBusy("");
    }
  };

  const commitStaged = async () => {
    const message = commitMessage.trim();
    if (!message || !stagedFiles.length || gitBlocked) return;
    setGitBusy("project/git_commit");
    setWorkspaceError("");
    setCommitNotice("");
    try {
      const client = bridge();
      if (!client) throw new Error("Loom bridge is unavailable");
      const result = await client.call<ProjectGitActionResult>("project/git_commit", {
        projectId: project.id,
        message,
      });
      setWorkspaceStatus((current) => current ? { ...current, git: result.git } : current);
      setCommitNotice(result.commitSha ? `已提交 ${result.commitSha}` : "已提交");
      setCommitMessage("");
      await loadWorkspaceStatus(true);
    } catch (cause) {
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setGitBusy("");
    }
  };

  const renderGitFile = (file: ProjectGitFile, kind: "staged" | "unstaged") => {
    const path = String(file.path || "").replaceAll("\\", "/");
    const loading = reviewLoadingPath === path;
    const actionMethod = kind === "staged" ? "project/git_unstage" : "project/git_stage";
    const actionLabel = kind === "staged" ? "取消暂存" : "暂存";
    const actionKey = `${actionMethod}:${path || "all"}`;
    return (
      <div className="project-stage-file" key={`${kind}:${gitFileKey(file)}`}>
        <button
          type="button"
          className="project-stage-file-main"
          onClick={() => void openProjectDiff(file)}
          disabled={Boolean(reviewLoadingPath)}
          title="在右侧审查栏查看 diff"
        >
          <span className={`project-git-badge ${kind}`}>{loading ? "读取" : fileStatusLabel(file)}</span>
          <code>{path}</code>
          <ExternalLink size={12} strokeWidth={1.9} />
        </button>
        <button
          type="button"
          className="project-stage-file-action"
          onClick={() => void runGitAction(actionMethod, path)}
          disabled={gitBlocked || Boolean(reviewLoadingPath)}
          title={actionLabel}
        >
          {gitBusy === actionKey ? "处理中" : actionLabel}
        </button>
      </div>
    );
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
            <span>项目内有任务正在运行，移动、删除、提交等危险操作会被保护。先让任务结束再调整归属或提交代码。</span>
          </div>
        ) : null}

        <section className="project-workspace-card">
          <div className="project-card-heading">
            <div>
              <span>Workspace</span>
              <strong>工作区状态</strong>
            </div>
            <button type="button" onClick={() => void loadWorkspaceStatus()} disabled={workspaceLoading || Boolean(gitBusy)}>
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
              <small>{git?.changedCount ? `${stagedFiles.length} staged · ${unstagedFiles.length} unstaged` : "工作区暂无 Git 变更"}</small>
            </div>
          </div>

          {workspaceError || workspaceStatus?.error || git?.error ? (
            <div className="project-workspace-warning">
              {workspaceError || workspaceStatus?.error || git?.error}
            </div>
          ) : null}

          <div className="project-changed-block">
            <div className="project-subheading project-git-subheading">
              <div>
                <span>当前变更</span>
                <small>{changedFiles.length ? `${changedFiles.length}${git?.truncated ? "+" : ""} 个文件` : "clean"}</small>
              </div>
              {changedFiles.length ? (
                <div className="project-git-toolbar">
                  <button type="button" onClick={() => void runGitAction("project/git_stage")} disabled={gitBlocked || !unstagedFiles.length}>
                    {gitBusy === "project/git_stage:all" ? "处理中" : "暂存全部"}
                  </button>
                  <button type="button" onClick={() => void runGitAction("project/git_unstage")} disabled={gitBlocked || !stagedFiles.length}>
                    {gitBusy === "project/git_unstage:all" ? "处理中" : "取消暂存"}
                  </button>
                </div>
              ) : null}
            </div>

            {changedFiles.length ? (
              <div className="project-stage-columns">
                <section className="project-stage-section staged">
                  <header>
                    <span>Staged</span>
                    <small>{stagedFiles.length}</small>
                  </header>
                  {stagedFiles.length ? stagedFiles.slice(0, 12).map((file) => renderGitFile(file, "staged")) : (
                    <p className="project-muted-line">还没有已暂存文件。</p>
                  )}
                </section>
                <section className="project-stage-section unstaged">
                  <header>
                    <span>Unstaged</span>
                    <small>{unstagedFiles.length}</small>
                  </header>
                  {unstagedFiles.length ? unstagedFiles.slice(0, 12).map((file) => renderGitFile(file, "unstaged")) : (
                    <p className="project-muted-line">没有未暂存变更。</p>
                  )}
                </section>
              </div>
            ) : (
              <p className="project-muted-line">没有检测到 Git 工作区变更。</p>
            )}

            {git?.isRepo ? (
              <div className="project-commit-box">
                <div className="project-commit-heading">
                  <div>
                    <span>Commit</span>
                    <strong>提交已暂存变更</strong>
                  </div>
                  <small>{commitNotice || "只提交 staged，不会 push 到远端"}</small>
                </div>
                <textarea
                  value={commitMessage}
                  onChange={(event) => setCommitMessage(event.target.value)}
                  placeholder="例如：feat: improve project workspace panel"
                  maxLength={500}
                  disabled={Boolean(gitBusy)}
                  aria-label="Git commit message"
                />
                <div className="project-commit-actions">
                  <button type="button" onClick={() => setCommitMessage(suggestedCommitMessage(stagedFiles))} disabled={!stagedFiles.length || Boolean(gitBusy)}>
                    生成信息
                  </button>
                  <button
                    type="button"
                    className="primary"
                    onClick={() => void commitStaged()}
                    disabled={!stagedFiles.length || !commitMessage.trim() || gitBlocked}
                  >
                    <Check size={13} strokeWidth={1.9} />
                    {gitBusy === "project/git_commit" ? "提交中" : "提交"}
                  </button>
                </div>
              </div>
            ) : null}
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
            <small>Git commit</small>
            <small>项目级模型</small>
            <small>项目级权限</small>
          </div>
        </section>
      </div>
    </aside>
  );
}
