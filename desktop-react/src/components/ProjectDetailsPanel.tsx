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
} from "./icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { RETAIN } from "../motion/tokens";
import { startVisiblePolling } from "../visiblePolling";
import { withProjectReadTimeout } from "../state/projectRead";
import type { ProjectRecord, ThreadRecord, TranscriptItem } from "../types/loom";
import { ProjectAgentFilesCard } from "./ProjectAgentFilesCard";
import { ProjectMemoryCard } from "./ProjectMemoryCard";
import { PanelHeader } from "./PanelHeader";
import { ProjectRepositoryCard } from "./ProjectRepositoryCard";
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
  onGitChanged?(): void;
  onOpenFiles?(): void;
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

function relativeTime(value: string | undefined, zh: boolean): string {
  const stamp = Date.parse(String(value || ""));
  if (!Number.isFinite(stamp)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - stamp) / 1000));
  if (seconds < 60) return zh ? "刚刚" : "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return zh ? `${minutes} 分钟前` : `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return zh ? `${hours} 小时前` : `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return zh ? `${days} 天前` : `${days} d ago`;
}

function displayPath(root: string): string {
  return String(root || "").replaceAll("\\", "/");
}

function bridge(): LoomBridge | null {
  return Reflect.get(window, "loom") as LoomBridge | null;
}

type FileStatusKind = "added" | "deleted" | "renamed" | "modified" | "changed";

function fileStatusKind(file: ProjectGitFile): FileStatusKind {
  const status = String(file.status || "").trim();
  if (status === "??") return "added";
  if (status.includes("D")) return "deleted";
  if (status.includes("R")) return "renamed";
  if (status.includes("A")) return "added";
  if (status.includes("M")) return "modified";
  return "changed";
}

const FILE_STATUS_TEXT: Record<FileStatusKind, [zh: string, en: string]> = {
  added: ["新增", "Added"],
  deleted: ["删除", "Deleted"],
  renamed: ["重命名", "Renamed"],
  modified: ["修改", "Modified"],
  changed: ["变更", "Changed"],
};

function fileStatusLabel(file: ProjectGitFile, zh: boolean): string {
  const [chinese, english] = FILE_STATUS_TEXT[fileStatusKind(file)];
  return zh ? chinese : english;
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
  if (file.index !== undefined || file.workingTree !== undefined) {
    return Boolean(index && index !== "?");
  }
  const status = String(file.status || "").padEnd(2, " ");
  return Boolean(status[0].trim() && status[0] !== "?");
}

function fileIsUnstaged(file: ProjectGitFile): boolean {
  const working = String(file.workingTree ?? "").trim();
  const status = String(file.status || "").trim();
  if (file.index !== undefined || file.workingTree !== undefined) {
    return status === "??" || Boolean(working && working !== "?");
  }
  const pair = String(file.status || "").padEnd(2, " ");
  return status === "??" || Boolean(pair[1].trim() && pair[1] !== "?") || status.includes("?");
}

function suggestedCommitMessage(files: ProjectGitFile[]): string {
  if (!files.length) return "chore: update project workspace";
  if (files.length === 1) return `chore: update ${fileBaseName(files[0].path)}`;
  const added = files.filter((file) => fileStatusKind(file) === "added").length;
  const deleted = files.filter((file) => fileStatusKind(file) === "deleted").length;
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
  project: projectProp,
  threads,
  activeThreadId,
  open,
  onClose,
  onNewThread,
  onOpenThread,
  onSetInstructions,
  onGitChanged,
  onOpenFiles,
}: ProjectDetailsPanelProps) {
  const zh = useI18n().language === "zh-CN";
  const presence = useMotionPresence(open && Boolean(projectProp), RETAIN.panel);
  const lastProjectRef = useRef<ProjectRecord | null>(projectProp);
  if (projectProp) lastProjectRef.current = projectProp;
  const project = projectProp ?? lastProjectRef.current;
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
  const workspaceRequest = useRef(0);
  const projectEpoch = useRef(0);

  const loadWorkspaceStatus = useCallback(async (quiet = false): Promise<ProjectWorkspaceStatus | null> => {
    if (!open || !projectId) return null;
    const request = ++workspaceRequest.current;
    if (!quiet) setWorkspaceLoading(true);
    setWorkspaceError("");
    try {
      const client = bridge();
      if (!client) throw new Error("Loom bridge is unavailable");
      const status = await withProjectReadTimeout(client.call<ProjectWorkspaceStatus>("project/workspace_status", { projectId }));
      if (request !== workspaceRequest.current) return null;
      setWorkspaceStatus(status);
      return status;
    } catch (cause) {
      if (request !== workspaceRequest.current) return null;
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      if (request === workspaceRequest.current) setWorkspaceLoading(false);
    }
  }, [open, projectId]);

  useEffect(() => {
    ++projectEpoch.current;
    ++workspaceRequest.current;
    setWorkspaceStatus(null);
    setWorkspaceError("");
    setWorkspaceLoading(false);
    setReviewLoadingPath("");
    setGitBusy("");
    setSaving(false);
    return () => { ++workspaceRequest.current; ++projectEpoch.current; };
  }, [projectId, open]);

  useEffect(() => {
    setDraft(project?.instructions ?? "");
    setNotice("");
    setSaving(false);
    setReviewLoadingPath("");
    setGitBusy("");
    setCommitNotice("");
  }, [project?.id, project?.instructions]);

  useEffect(() => {
    if (!presence.mounted || !projectId) {
      setWorkspaceStatus(null);
      setWorkspaceError("");
      setWorkspaceLoading(false);
      setReviewLoadingPath("");
      setGitBusy("");
      return undefined;
    }
    if (!open) return undefined;
    let initial = true;
    return startVisiblePolling(() => {
      const result = loadWorkspaceStatus(!initial);
      initial = false;
      return result;
    }, 12000);
  }, [loadWorkspaceStatus, open, presence.mounted, projectId]);

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

  if (!presence.mounted || !project) return null;

  const saveInstructions = async () => {
    const epoch = projectEpoch.current;
    if (!dirty || saving) return;
    setSaving(true);
    try {
      await onSetInstructions(project.id, draft);
      if (epoch !== projectEpoch.current) return;
      setNotice(zh ? "项目说明已保存，下一轮起生效" : "Project instructions saved; they apply from the next turn");
    } catch (cause) {
      if (epoch !== projectEpoch.current) return;
      setNotice(cause instanceof Error ? cause.message : (zh ? "保存失败" : "Could not save"));
    } finally {
      if (epoch === projectEpoch.current) setSaving(false);
    }
  };

  const openProjectDiff = async (file?: ProjectGitFile) => {
    const epoch = projectEpoch.current;
    const path = String(file?.path || "").replaceAll("\\", "/");
    const loadingKey = path || "__all__";
    setReviewLoadingPath(loadingKey);
    setWorkspaceError("");
    try {
      const client = bridge();
      if (!client) throw new Error("Loom bridge is unavailable");
      const params: Record<string, unknown> = { projectId: project.id };
      if (path) params.path = path;
      const result = await withProjectReadTimeout(client.call<ProjectGitDiffResult>("project/git_diff", params));
      if (epoch !== projectEpoch.current) return;
      const detail = {
        path: path || result.paths?.[0] || "",
        title: `${project.name} · Git diff`,
        subtitle: result.truncated
          ? (zh ? "项目工作区当前的 Git diff（已截断）" : "The project workspace's current Git diff (truncated)")
          : (zh ? "项目工作区当前的 Git diff" : "The project workspace's current Git diff"),
        items: [reviewDiffItem(project.id, result, path)],
      };
      window.dispatchEvent(new CustomEvent("loom:review-open-diff", { detail }));
      if (result.error) setWorkspaceError(result.error);
    } catch (cause) {
      if (epoch !== projectEpoch.current) return;
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (epoch === projectEpoch.current) setReviewLoadingPath("");
    }
  };

  const runGitAction = async (method: "project/git_stage" | "project/git_unstage", path = "") => {
    const epoch = projectEpoch.current;
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
      if (epoch !== projectEpoch.current) return;
      setWorkspaceStatus((current) => current ? { ...current, git: result.git } : current);
      await loadWorkspaceStatus(true);
    } catch (cause) {
      if (epoch !== projectEpoch.current) return;
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (epoch === projectEpoch.current) setGitBusy("");
    }
  };

  const commitStaged = async () => {
    const epoch = projectEpoch.current;
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
      if (epoch !== projectEpoch.current) return;
      setWorkspaceStatus((current) => current ? { ...current, git: result.git } : current);
      setCommitNotice(result.commitSha ? (zh ? `已提交 ${result.commitSha}` : `Committed ${result.commitSha}`) : (zh ? "已提交" : "Committed"));
      setCommitMessage("");
      await loadWorkspaceStatus(true);
    } catch (cause) {
      if (epoch !== projectEpoch.current) return;
      setWorkspaceError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (epoch === projectEpoch.current) setGitBusy("");
    }
  };

  const renderGitFile = (file: ProjectGitFile, kind: "staged" | "unstaged") => {
    const path = String(file.path || "").replaceAll("\\", "/");
    const loading = reviewLoadingPath === path;
    const actionMethod = kind === "staged" ? "project/git_unstage" : "project/git_stage";
    const actionLabel = kind === "staged" ? (zh ? "取消暂存" : "Unstage") : (zh ? "暂存" : "Stage");
    const actionKey = `${actionMethod}:${path || "all"}`;
    return (
      <div className="project-stage-file" key={`${kind}:${gitFileKey(file)}`}>
        <button
          type="button"
          className="project-stage-file-main"
          onClick={() => void openProjectDiff(file)}
          disabled={Boolean(reviewLoadingPath)}
          title={zh ? "在审查栏查看 diff" : "Open the diff in Review"}
        >
          <span className={`project-git-badge ${kind}`}>{loading ? (zh ? "读取" : "Loading") : fileStatusLabel(file, zh)}</span>
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
          {gitBusy === actionKey ? (zh ? "处理中" : "Working") : actionLabel}
        </button>
      </div>
    );
  };

  return (
    <aside className="project-details-panel pl-dock" data-motion-phase={presence.phase} inert={!open} data-open={open ? "true" : "false"} aria-label={zh ? "项目详情" : "Project details"}>
      <PanelHeader
        className="project-details-header"
        mark={<Folder size={18} strokeWidth={1.85} />}
        markClassName="project-details-mark"
        title={project.name}
        titleHint={project.name}
        subtitle={displayPath(project.root)}
        subtitleCode
        subtitleHint={project.root}
        closeLabel={zh ? "关闭项目详情" : "Close project details"}
        closeClassName="project-details-close"
        onClose={onClose}
      />

      <div className="project-details-body">
        <div className="project-overview-grid" aria-label={zh ? "项目概览" : "Project overview"}>
          <div className="project-overview-card">
            <span>{zh ? "会话" : "Conversations"}</span>
            <strong>{projectThreads.length}</strong>
            <small>{busyThreads.length ? (zh ? `${busyThreads.length} 个运行中` : `${busyThreads.length} running`) : (zh ? "当前空闲" : "Idle")}</small>
          </div>
          <div className="project-overview-card">
            <span>{zh ? "项目说明" : "Instructions"}</span>
            <strong>{project.instructions?.trim() ? (zh ? "已配置" : "Set") : (zh ? "未配置" : "Not set")}</strong>
            <small>{instructionsLength} / 12000</small>
          </div>
        </div>

        {busyThreads.length ? (
          <div className="project-running-guard" role="status">
            <ShieldAlert size={15} strokeWidth={1.85} />
            <span>{zh ? "项目内有任务正在运行，移动、删除、提交等操作暂时受保护。等任务结束后再调整归属或提交代码。" : "A task is running in this project, so moving, deleting and committing are held until it ends."}</span>
          </div>
        ) : null}

        {projectId && open && <ProjectRepositoryCard key={projectId} projectId={projectId} blocked={Boolean(busyThreads.length || gitBusy)} onBusyChange={busy => setGitBusy(busy ? "repository" : "")} onNewThread={project => { void onNewThread(project); }} onChanged={() => { void loadWorkspaceStatus(); onGitChanged?.(); }}/ >}
        <section className="project-workspace-card">
          <div className="project-card-heading">
            <div>
              <strong>{zh ? "工作区" : "Workspace"}</strong>
            </div>
            <button type="button" onClick={() => void loadWorkspaceStatus()} disabled={workspaceLoading || Boolean(gitBusy)}>
              <RefreshCw size={14} strokeWidth={1.85} />
              {workspaceLoading ? (zh ? "刷新中" : "Refreshing") : (zh ? "刷新" : "Refresh")}
            </button>
          </div>

          <div className="project-workspace-grid">
            <div className="project-workspace-stat">
              <GitBranch size={15} strokeWidth={1.8} />
              <span>Git</span>
              <strong>{!git ? workspaceError ? (zh ? "读取失败" : "Unavailable") : (zh ? "读取中" : "Loading") : git.isRepo ? git.branch || "detached" : (zh ? "未接入" : "Not a repository")}</strong>
              <small>{git?.summary || (!git ? (zh ? "等待工作区状态" : "Waiting for the workspace") : (zh ? "当前项目不是 Git 仓库或 Git 不可用" : "This project is not a Git repository, or Git is unavailable"))}</small>
            </div>
            <div className="project-workspace-stat">
              <FileText size={15} strokeWidth={1.8} />
              <span>{zh ? "变更" : "Changes"}</span>
              <strong>{git?.changedCount ?? "—"}</strong>
              <small>{!git ? (zh ? "尚未获取变更状态" : "Not read yet") : git.changedCount ? (zh ? `${stagedFiles.length} 已暂存 · ${unstagedFiles.length} 未暂存` : `${stagedFiles.length} staged · ${unstagedFiles.length} unstaged`) : (zh ? "工作区没有 Git 变更" : "No Git changes")}</small>
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
                <span>{zh ? "当前变更" : "Current changes"}</span>
                <small>{!git ? (zh ? "状态未知" : "Unknown") : changedFiles.length ? (zh ? `${changedFiles.length}${git?.truncated ? "+" : ""} 个文件` : `${changedFiles.length}${git?.truncated ? "+" : ""} files`) : (zh ? "无变更" : "clean")}</small>
              </div>
              {changedFiles.length ? (
                <div className="project-git-toolbar">
                  <button type="button" onClick={() => void runGitAction("project/git_stage")} disabled={gitBlocked || !unstagedFiles.length}>
                    {gitBusy === "project/git_stage:all" ? (zh ? "处理中" : "Working") : (zh ? "暂存全部" : "Stage all")}
                  </button>
                  <button type="button" onClick={() => void runGitAction("project/git_unstage")} disabled={gitBlocked || !stagedFiles.length}>
                    {gitBusy === "project/git_unstage:all" ? (zh ? "处理中" : "Working") : (zh ? "取消暂存" : "Unstage all")}
                  </button>
                </div>
              ) : null}
            </div>

            {changedFiles.length ? (
              <div className="project-stage-columns">
                <section className="project-stage-section staged">
                  <header>
                    <span>{zh ? "已暂存" : "Staged"}</span>
                    <small>{stagedFiles.length}</small>
                  </header>
                  {stagedFiles.length ? stagedFiles.slice(0, 12).map((file) => renderGitFile(file, "staged")) : (
                    <p className="project-muted-line">{zh ? "还没有已暂存的文件。" : "Nothing staged yet."}</p>
                  )}
                </section>
                <section className="project-stage-section unstaged">
                  <header>
                    <span>{zh ? "未暂存" : "Unstaged"}</span>
                    <small>{unstagedFiles.length}</small>
                  </header>
                  {unstagedFiles.length ? unstagedFiles.slice(0, 12).map((file) => renderGitFile(file, "unstaged")) : (
                    <p className="project-muted-line">{zh ? "没有未暂存的变更。" : "No unstaged changes."}</p>
                  )}
                </section>
              </div>
            ) : (
              <p className="project-muted-line">{!git ? (zh ? "尚未读取工作区变更，稍候或刷新重试。" : "The workspace has not been read yet; wait or refresh.") : (zh ? "没有检测到 Git 工作区变更。" : "No changes in the Git workspace.")}</p>
            )}

            {git?.isRepo ? (
              <div className="project-commit-box">
                <div className="project-commit-heading">
                  <div>
                    <strong>{zh ? "提交已暂存的变更" : "Commit staged changes"}</strong>
                  </div>
                  <small>{commitNotice || (zh ? "只提交已暂存的文件，不会推送到远端" : "Commits only what is staged; nothing is pushed")}</small>
                </div>
                <textarea
                  value={commitMessage}
                  onChange={(event) => setCommitMessage(event.target.value)}
                  placeholder={zh ? "例如：feat: improve project workspace panel" : "e.g. feat: improve project workspace panel"}
                  maxLength={500}
                  disabled={Boolean(gitBusy)}
                  aria-label="Git commit message"
                />
                <div className="project-commit-actions">
                  <button type="button" onClick={() => setCommitMessage(suggestedCommitMessage(stagedFiles))} disabled={!stagedFiles.length || Boolean(gitBusy)}>
                    {zh ? "生成信息" : "Suggest message"}
                  </button>
                  <button
                    type="button"
                    className="primary"
                    onClick={() => void commitStaged()}
                    disabled={!stagedFiles.length || !commitMessage.trim() || gitBlocked}
                  >
                    <Check size={13} strokeWidth={1.9} />
                    {gitBusy === "project/git_commit" ? (zh ? "提交中" : "Committing") : (zh ? "提交" : "Commit")}
                  </button>
                </div>
              </div>
            ) : null}
          </div>

          <div className="project-file-tree-block">
            <div className="project-subheading">
              <span>{zh ? "文件" : "Files"}</span>
              <button className="project-files-launch" type="button" onClick={onOpenFiles} title={zh ? "打开完整的文件管理器" : "Open the full file manager"}>
                <FolderOpen size={14} strokeWidth={1.8} /> {zh ? "打开文件" : "Open files"}
              </button>
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
              <p className="project-muted-line">{!workspaceStatus ? workspaceError ? (zh ? "文件列表读取失败，请刷新重试。" : "Could not read the files; refresh to try again.") : (zh ? "正在读取文件列表…" : "Reading the files…") : (zh ? "还没有可显示的文件。" : "No files to show yet.")}</p>
            )}
          </div>
        </section>

        <section className="project-instructions-card">
          <div className="project-card-heading">
            <div>
              <strong>{zh ? "项目说明" : "Project instructions"}</strong>
            </div>
            <FileText size={16} strokeWidth={1.8} aria-hidden="true" />
          </div>
          <p>
            {zh
              ? "写给这个项目里所有任务看的固定背景、约束和偏好。保存后从下一轮起自动加入 Agent 的上下文。"
              : "Background, constraints and preferences every task in this project should know. Saved instructions join the agent's context from the next turn."}
          </p>
          <textarea
            value={draft}
            maxLength={12000}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={zh
              ? "例如：这个项目是 Loom 桌面端。所有修改直接提交 main；优先保持 Codex 风格；不要改动无关代码。"
              : "e.g. This is the Loom desktop app. Commit straight to main; keep the Codex style; leave unrelated code alone."}
            aria-label="Project instructions"
          />
          <div className="project-instructions-actions">
            <span>{notice || (dirty ? (zh ? "有未保存的修改" : "Unsaved changes") : (zh ? "已同步，下一轮起生效" : "Saved; applies from the next turn"))}</span>
            <button type="button" onClick={() => setDraft(project.instructions ?? "")} disabled={!dirty || saving}>
              <RotateCcw size={14} strokeWidth={1.85} />
              {zh ? "还原" : "Revert"}
            </button>
            <button type="button" className="primary" onClick={() => void saveInstructions()} disabled={!dirty || saving}>
              <Save size={14} strokeWidth={1.85} />
              {saving ? (zh ? "保存中" : "Saving") : (zh ? "保存" : "Save")}
            </button>
          </div>
        </section>

        <ProjectMemoryCard
          key={`${project.id}:${open}`}
          projectId={project.id}
          open={open}
          running={Boolean(busyThreads.length)}
        />

        <ProjectAgentFilesCard key={`${project.id}:${open}`} projectId={project.id} open={open} />

        <section className="project-thread-card">
          <div className="project-card-heading">
            <div>
              <strong>{zh ? "项目会话" : "Conversations"}</strong>
            </div>
            <button type="button" onClick={() => void onNewThread(project)}>
              <Plus size={14} strokeWidth={1.9} />
              {zh ? "新对话" : "New conversation"}
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
                    <span>{thread.title || (zh ? "新对话" : "New conversation")}</span>
                    <small>{busy ? (zh ? "运行中" : "Running") : relativeTime(thread.updatedAt, zh)}</small>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="project-empty-threads">
              <span>{zh ? "这个项目还没有会话。" : "No conversations in this project yet."}</span>
              <button type="button" onClick={() => void onNewThread(project)}>{zh ? "开始一个会话" : "Start a conversation"}</button>
            </div>
          )}
        </section>
      </div>
    </aside>
  );
}
