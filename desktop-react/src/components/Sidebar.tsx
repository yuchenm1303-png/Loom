import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  ChevronRight,
  Copy,
  Ellipsis,
  Eye,
  EyeOff,
  Folder,
  FolderOpen,
  FolderPlus,
  GitFork,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import type { ProjectRecord, ThreadRecord } from "../types/loom";
import "./sidebar.css";

type ThreadView = "active" | "archived";
type Notice = { kind: "success" | "error"; text: string };
type ContextMenuState = { threadId: string; x: number; y: number };

// Sidebar copy follows the interface language, like the rest of the shell.
const SIDEBAR_COPY = {
  en: {
    addProject: "Add project",
    archive: "Archive",
    archiveEmpty: "Archive is empty",
    archived: "Conversation archived",
    backToConversations: "Back to conversations",
    busyMove: "This conversation is running. Move it once it finishes.",
    clearSearch: "Clear search",
    collapse: (name: string) => `Collapse ${name}`,
    conversationActions: "Conversation actions",
    conversations: "Conversations",
    copy: "Copy",
    copied: (label: string) => `Copied ${label}`,
    copyFailed: (reason: string) => `Could not copy: ${reason}`,
    copyId: "Copy conversation ID",
    copyPath: "Copy workspace path",
    copyTitle: "Copy title",
    current: "Current",
    deleted: "Conversation deleted",
    deleteConfirm: "Click again to delete",
    deleteConversation: "Delete conversation",
    expand: (name: string) => `Expand ${name}`,
    fork: "Fork",
    forked: "Forked into a new conversation",
    idLabel: "conversation ID",
    markRead: "Mark as read",
    markUnread: "Mark as unread",
    moreActions: "More actions",
    movedIn: (name: string) => `Moved to ${name}`,
    movedOut: "Removed from project",
    moveToProject: "Move to project",
    newConversation: "New conversation",
    newInProject: (name: string) => `New conversation in ${name}`,
    noConversations: "No conversations yet",
    noMatches: "No matching conversations",
    noProjects: "No projects yet",
    openArchive: "Open archive",
    openProject: (root: string) => `Open project details · ${root}`,
    pathLabel: "workspace path",
    pin: "Pin",
    project: "project",
    projectAdded: "Project added",
    projectAddFailed: "Could not add the project.",
    projectRemoved: (name: string) => `${name} removed from the list`,
    projectRemoveFailed: "Could not remove the project.",
    projectRenamed: "Project renamed",
    projectRenameFailed: "Could not rename the project.",
    projects: "Projects",
    putInProject: "Add to project",
    recent: "Recent",
    removeFromProject: "Remove from project",
    removeProject: (name: string) => `Remove ${name}`,
    removeProjectConfirm: (name: string, count: number) => [
      `Remove ${name} from Loom's project list?`,
      "The folder and its files are not touched.",
      ...(count ? [`${count} conversation${count === 1 ? "" : "s"} stay in Loom and become unfiled.`] : []),
    ].join("\n\n"),
    rename: "Rename",
    renamed: "Conversation renamed",
    renameConversation: "Rename conversation",
    renameFailed: (reason: string) => `Could not rename: ${reason}`,
    renameProject: (name: string) => `Rename ${name}`,
    restore: "Restore",
    restored: "Conversation restored",
    running: "Running",
    search: "Search conversations",
    searchArchived: "Search archived",
    titleLabel: "title",
    unpin: "Unpin",
    unread: "Unread",
    untitled: "New conversation",
  },
  zh: {
    addProject: "添加项目",
    archive: "归档",
    archiveEmpty: "归档为空",
    archived: "会话已归档",
    backToConversations: "返回对话",
    busyMove: "当前任务运行中，结束后才能移动这个对话到项目。",
    clearSearch: "清除搜索",
    collapse: (name: string) => `折叠 ${name}`,
    conversationActions: "对话操作",
    conversations: "对话",
    copy: "复制",
    copied: (label: string) => `${label}已复制`,
    copyFailed: (reason: string) => `复制失败：${reason}`,
    copyId: "复制会话 ID",
    copyPath: "复制工作区路径",
    copyTitle: "复制标题",
    current: "当前",
    deleted: "会话已删除",
    deleteConfirm: "再次点击确认删除",
    deleteConversation: "删除会话",
    expand: (name: string) => `展开 ${name}`,
    fork: "分叉",
    forked: "已创建分叉会话",
    idLabel: "会话 ID",
    markRead: "标记为已读",
    markUnread: "标记为未读",
    moreActions: "更多操作",
    movedIn: (name: string) => `已放入项目「${name}」`,
    movedOut: "已移出项目",
    moveToProject: "移动到项目",
    newConversation: "新对话",
    newInProject: (name: string) => `在 ${name} 中新建对话`,
    noConversations: "还没有对话",
    noMatches: "没有匹配的对话",
    noProjects: "暂无项目",
    openArchive: "打开归档",
    openProject: (root: string) => `打开项目详情 · ${root}`,
    pathLabel: "工作区路径",
    pin: "置顶",
    project: "项目",
    projectAdded: "项目已添加",
    projectAddFailed: "无法添加项目。",
    projectRemoved: (name: string) => `已从列表移除 ${name}`,
    projectRemoveFailed: "无法移除项目。",
    projectRenamed: "项目已重命名",
    projectRenameFailed: "无法重命名项目。",
    projects: "项目",
    putInProject: "放入项目",
    recent: "最近",
    removeFromProject: "移出项目",
    removeProject: (name: string) => `移除 ${name}`,
    removeProjectConfirm: (name: string, count: number) => [
      `从 Loom 的项目列表中移除「${name}」？`,
      "文件夹及其中的文件不会被改动。",
      ...(count ? [`其中 ${count} 个对话会保留在 Loom 中，变为未归入项目。`] : []),
    ].join("\n\n"),
    rename: "重命名",
    renamed: "会话已重命名",
    renameConversation: "重命名对话",
    renameFailed: (reason: string) => `重命名失败：${reason}`,
    renameProject: (name: string) => `重命名 ${name}`,
    restore: "恢复",
    restored: "会话已恢复",
    running: "运行中",
    search: "搜索对话",
    searchArchived: "搜索归档",
    titleLabel: "标题",
    unpin: "取消置顶",
    unread: "未读",
    untitled: "新对话",
  },
} as const;

interface SidebarProps {
  threads: ThreadRecord[];
  projects: ProjectRecord[];
  projectsSupported: boolean;
  activeId?: string;
  activeProjectId?: string;
  threadView: ThreadView;
  archivedCount: number;
  onOpen(threadId: string): Promise<void> | void;
  onPrefetch?(threadId: string): void;
  onNew(workspace?: string, projectId?: string): Promise<void> | void;
  onOpenProject(projectId: string): Promise<void> | void;
  onAddProject(root: string): Promise<ProjectRecord | void>;
  onRenameProject(projectId: string, name: string): Promise<void>;
  onRemoveProject(projectId: string): Promise<void>;
  onMoveProject(threadId: string, projectId: string): Promise<void>;
  onRename(threadId: string, title: string): Promise<void>;
  onArchive(threadId: string, archived: boolean): Promise<void>;
  onDelete(threadId: string): Promise<void>;
  onFork(threadId: string): Promise<void>;
  onViewChange(view: ThreadView): Promise<void> | void;
}

const PINNED_STORAGE_KEY = "loom.sidebar.pinnedThreads";
const UNREAD_STORAGE_KEY = "loom.sidebar.unreadThreads";
const COLLAPSED_PROJECTS_STORAGE_KEY = "loom.sidebar.collapsedProjects";

function relativeTime(value: string): string {
  const stamp = Date.parse(value);
  if (!Number.isFinite(stamp)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - stamp) / 1000));
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function normalizeWorkspace(workspace?: string): string {
  const value = (workspace || "").trim();
  if (!value) return "";
  return value.replaceAll("\\", "/").replace(/\/+$/, "");
}

function workspaceLabel(workspace?: string): string {
  const normalized = normalizeWorkspace(workspace);
  if (!normalized) return "Other";
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || "Other";
}

function workspaceParentLabel(workspace?: string): string {
  const normalized = normalizeWorkspace(workspace);
  const parts = normalized.split("/").filter(Boolean);
  return parts.length > 1 ? parts.at(-2) || "" : "";
}

function threadIsBusy(thread: ThreadRecord): boolean {
  return thread.status === "running" || thread.status === "waiting_approval";
}

function readStoredIds(key: string): Set<string> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || "[]") as unknown;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((value): value is string => typeof value === "string" && Boolean(value)));
  } catch {
    return new Set();
  }
}

function persistIds(key: string, values: Set<string>): void {
  try {
    window.localStorage.setItem(key, JSON.stringify([...values]));
  } catch {
    // Keep the in-memory sidebar state usable when localStorage is unavailable.
  }
}

function updateStoredSet(
  key: string,
  setter: Dispatch<SetStateAction<Set<string>>>,
  id: string,
  enabled: boolean,
): void {
  setter((current) => {
    const next = new Set(current);
    if (enabled) next.add(id);
    else next.delete(id);
    persistIds(key, next);
    return next;
  });
}

async function writeClipboard(value: string): Promise<void> {
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

/** Server error text, with the running-thread refusal replaced by `busyMove`. */
function errorText(cause: unknown, busyMove: string): string {
  const text = cause instanceof Error ? cause.message : String(cause);
  if (text.includes("cannot move a running thread between projects")) return busyMove;
  return text;
}

function sortThreads(rows: ThreadRecord[], pinnedIds: Set<string>): ThreadRecord[] {
  return [...rows].sort((left, right) => {
    const pinDelta = Number(pinnedIds.has(right.id)) - Number(pinnedIds.has(left.id));
    if (pinDelta) return pinDelta;
    return Date.parse(right.updatedAt || "") - Date.parse(left.updatedAt || "");
  });
}

function filterThreads(threads: ThreadRecord[], query: string): ThreadRecord[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return threads;
  return threads.filter((thread) => `${thread.title} ${thread.workspace}`.toLowerCase().includes(needle));
}

function threadBelongsToProject(thread: ThreadRecord, projectIds: Set<string>): boolean {
  const projectId = (thread.projectId || "").trim();
  return Boolean(projectId && projectIds.has(projectId));
}

export function Sidebar({
  threads,
  activeId,
  activeProjectId,
  threadView,
  archivedCount,
  onOpen,
  onPrefetch,
  onNew,
  onOpenProject,
  projects,
  projectsSupported,
  onAddProject,
  onRenameProject,
  onRemoveProject,
  onMoveProject,
  onRename,
  onArchive,
  onDelete,
  onFork,
  onViewChange,
}: SidebarProps) {
  const { language } = useI18n();
  const copy = SIDEBAR_COPY[language === "zh-CN" ? "zh" : "en"];
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() => readStoredIds(PINNED_STORAGE_KEY));
  const [unreadIds, setUnreadIds] = useState<Set<string>>(() => readStoredIds(UNREAD_STORAGE_KEY));
  const [collapsedProjectIds, setCollapsedProjectIds] = useState<Set<string>>(() => readStoredIds(COLLAPSED_PROJECTS_STORAGE_KEY));
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [contextMenuClosing, setContextMenuClosing] = useState(false);
  const contextMenuCloseTimerRef = useRef<number | null>(null);
  const [renamingProjectId, setRenamingProjectId] = useState("");
  const [projectRenameValue, setProjectRenameValue] = useState("");
  const [copyExpanded, setCopyExpanded] = useState(false);
  const [projectExpanded, setProjectExpanded] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [busyThreadId, setBusyThreadId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const renameRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const renameCommittingRef = useRef(false);
  const prefetchTimerRef = useRef<number | null>(null);

  const cancelScheduledPrefetch = useCallback(() => {
    if (prefetchTimerRef.current !== null) {
      window.clearTimeout(prefetchTimerRef.current);
      prefetchTimerRef.current = null;
    }
  }, []);

  const schedulePrefetch = useCallback((threadId: string) => {
    cancelScheduledPrefetch();
    prefetchTimerRef.current = window.setTimeout(() => {
      prefetchTimerRef.current = null;
      onPrefetch?.(threadId);
    }, 90);
  }, [cancelScheduledPrefetch, onPrefetch]);

  useEffect(() => cancelScheduledPrefetch, [cancelScheduledPrefetch]);

  const closeContextMenu = useCallback((immediate = false) => {
    if (contextMenuCloseTimerRef.current !== null) {
      window.clearTimeout(contextMenuCloseTimerRef.current);
      contextMenuCloseTimerRef.current = null;
    }
    const reduced = document.documentElement.dataset.loomReducedMotion === "true"
      || Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
    if (immediate || reduced) {
      setContextMenu(null);
      setContextMenuClosing(false);
      return;
    }
    setContextMenuClosing(true);
    contextMenuCloseTimerRef.current = window.setTimeout(() => {
      contextMenuCloseTimerRef.current = null;
      setContextMenu(null);
      setContextMenuClosing(false);
    }, 215);
  }, []);

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeId) ?? null,
    [activeId, threads],
  );

  const menuThread = useMemo(
    () => contextMenu ? threads.find((thread) => thread.id === contextMenu.threadId) ?? null : null,
    [contextMenu, threads],
  );

  const filtered = useMemo(() => filterThreads(threads, query), [query, threads]);
  const projectIds = useMemo(() => new Set(projects.map((project) => project.id)), [projects]);

  const projectSections = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return projects
      .map((project) => {
        const rows = filtered.filter((thread) => thread.projectId === project.id);
        return { project, threads: sortThreads(rows, pinnedIds) };
      })
      .filter(({ project, threads: rows }) => {
        if (!needle) return true;
        return project.name.toLowerCase().includes(needle) || (project.root || "").toLowerCase().includes(needle) || rows.length > 0;
      });
  }, [filtered, pinnedIds, projects, query]);

  const normalThreads = useMemo(() => (
    sortThreads(filtered.filter((thread) => !threadBelongsToProject(thread, projectIds)), pinnedIds)
  ), [filtered, pinnedIds, projectIds]);

  const legacyWorkspaceGroups = useMemo(() => {
    if (projectsSupported && threadView !== "archived") return [];
    const byWorkspace = new Map<string, { key: string; label: string; workspace: string; threads: ThreadRecord[] }>();
    for (const thread of filtered) {
      const normalized = normalizeWorkspace(thread.workspace);
      const key = normalized || "__other__";
      const existing = byWorkspace.get(key);
      if (existing) existing.threads.push(thread);
      else byWorkspace.set(key, { key, label: workspaceLabel(thread.workspace), workspace: thread.workspace || "", threads: [thread] });
    }
    const derived = [...byWorkspace.values()];
    const labelCounts = new Map<string, number>();
    for (const group of derived) labelCounts.set(group.label, (labelCounts.get(group.label) ?? 0) + 1);
    return derived.map((group) => ({
      ...group,
      displayLabel: (labelCounts.get(group.label) ?? 0) > 1
        ? `${group.label} · ${workspaceParentLabel(group.workspace) || "workspace"}`
        : group.label,
      threads: sortThreads(group.threads, pinnedIds),
    }));
  }, [filtered, pinnedIds, projectsSupported, threadView]);

  const focusSearch = () => {
    setSearchOpen(true);
    requestAnimationFrame(() => {
      searchRef.current?.focus();
      searchRef.current?.select();
    });
  };

  const closeSearch = () => {
    setQuery("");
    setSearchOpen(false);
  };

  const togglePinned = (threadId: string) => {
    updateStoredSet(PINNED_STORAGE_KEY, setPinnedIds, threadId, !pinnedIds.has(threadId));
  };

  const toggleUnread = (threadId: string) => {
    updateStoredSet(UNREAD_STORAGE_KEY, setUnreadIds, threadId, !unreadIds.has(threadId));
  };

  const toggleProjectCollapsed = (projectId: string) => {
    setCollapsedProjectIds((current) => {
      const next = new Set(current);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      persistIds(COLLAPSED_PROJECTS_STORAGE_KEY, next);
      return next;
    });
  };

  useEffect(() => {
    setCollapsedProjectIds((current) => {
      const next = new Set([...current].filter((projectId) => projectIds.has(projectId)));
      if (next.size === current.size) return current;
      persistIds(COLLAPSED_PROJECTS_STORAGE_KEY, next);
      return next;
    });
  }, [projectIds]);

  useEffect(() => {
    const activeProjectIdFromThread = (activeThread?.projectId || "").trim();
    if (!activeProjectIdFromThread) return;
    setCollapsedProjectIds((current) => {
      if (!current.has(activeProjectIdFromThread)) return current;
      const next = new Set(current);
      next.delete(activeProjectIdFromThread);
      persistIds(COLLAPSED_PROJECTS_STORAGE_KEY, next);
      return next;
    });
  }, [activeThread?.id, activeThread?.projectId]);

  const markRead = (threadId: string) => {
    if (unreadIds.has(threadId)) updateStoredSet(UNREAD_STORAGE_KEY, setUnreadIds, threadId, false);
  };

  const openThread = async (thread: ThreadRecord) => {
    markRead(thread.id);
    closeContextMenu();
    await onOpen(thread.id);
  };

  const beginRename = (thread: ThreadRecord) => {
    closeContextMenu();
    setDeleteArmed(false);
    setRenamingId(thread.id);
    setRenameValue(thread.title || copy.untitled);
    requestAnimationFrame(() => {
      renameRef.current?.focus();
      renameRef.current?.select();
    });
  };

  const commitRename = async (thread: ThreadRecord) => {
    if (renameCommittingRef.current || renamingId !== thread.id) return;
    const title = renameValue.trim();
    setRenamingId(null);
    if (!title || title === thread.title) return;
    renameCommittingRef.current = true;
    setBusyThreadId(thread.id);
    try {
      await onRename(thread.id, title);
      setNotice({ kind: "success", text: copy.renamed });
    } catch (cause) {
      setNotice({ kind: "error", text: copy.renameFailed(errorText(cause, copy.busyMove)) });
    } finally {
      renameCommittingRef.current = false;
      setBusyThreadId(null);
    }
  };

  const runRemoteAction = async (
    thread: ThreadRecord,
    successText: string,
    action: () => Promise<void>,
  ) => {
    closeContextMenu();
    setCopyExpanded(false);
    setProjectExpanded(false);
    setDeleteArmed(false);
    setBusyThreadId(thread.id);
    try {
      await action();
      setNotice({ kind: "success", text: successText });
    } catch (cause) {
      setNotice({ kind: "error", text: errorText(cause, copy.busyMove) });
    } finally {
      setBusyThreadId(null);
    }
  };

  const archiveThread = (thread: ThreadRecord) => {
    const nextArchived = !Boolean(thread.archived);
    return runRemoteAction(
      thread,
      nextArchived ? copy.archived : copy.restored,
      () => onArchive(thread.id, nextArchived),
    );
  };

  const forkThread = (thread: ThreadRecord) => runRemoteAction(
    thread,
    copy.forked,
    () => onFork(thread.id),
  );

  const deleteThread = (thread: ThreadRecord) => runRemoteAction(
    thread,
    copy.deleted,
    async () => {
      await onDelete(thread.id);
      updateStoredSet(PINNED_STORAGE_KEY, setPinnedIds, thread.id, false);
      updateStoredSet(UNREAD_STORAGE_KEY, setUnreadIds, thread.id, false);
    },
  );

  const moveThreadProject = (thread: ThreadRecord, projectId: string) => {
    if (threadIsBusy(thread)) {
      closeContextMenu();
      setProjectExpanded(false);
      setNotice({ kind: "error", text: copy.busyMove });
      return Promise.resolve();
    }
    const currentProjectId = (thread.projectId || "").trim();
    if (projectId === currentProjectId) {
      closeContextMenu();
      setProjectExpanded(false);
      return Promise.resolve();
    }
    const targetProject = projects.find((project) => project.id === projectId);
    const successText = projectId
      ? copy.movedIn(targetProject?.name || copy.project)
      : copy.movedOut;
    return runRemoteAction(thread, successText, () => onMoveProject(thread.id, projectId));
  };

  const copyThreadValue = async (thread: ThreadRecord, label: string, value: string) => {
    try {
      await writeClipboard(value);
      setNotice({ kind: "success", text: copy.copied(label) });
      closeContextMenu();
      setCopyExpanded(false);
      setProjectExpanded(false);
    } catch (cause) {
      setNotice({ kind: "error", text: copy.copyFailed(errorText(cause, copy.busyMove)) });
    }
  };

  const addProject = async () => {
    const picked = await window.loom?.pickDirectory?.();
    if (!picked) return;
    try {
      await onAddProject(picked);
      setNotice({ kind: "success", text: copy.projectAdded });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : copy.projectAddFailed });
    }
  };

  const beginProjectRename = (projectId: string) => {
    const project = projects.find((entry) => entry.id === projectId);
    if (!project) return;
    setRenamingProjectId(projectId);
    setProjectRenameValue(project.name);
  };

  const commitProjectRename = async () => {
    const projectId = renamingProjectId;
    const next = projectRenameValue.trim();
    const project = projects.find((entry) => entry.id === projectId);
    setRenamingProjectId("");
    if (!project || !next || next === project.name) return;
    try {
      await onRenameProject(projectId, next);
      setNotice({ kind: "success", text: copy.projectRenamed });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : copy.projectRenameFailed });
    }
  };

  const confirmRemoveProject = async (projectId: string) => {
    const project = projects.find((entry) => entry.id === projectId);
    if (!project) return;
    if (!window.confirm(copy.removeProjectConfirm(project.name, project.threadCount))) return;
    try {
      await onRemoveProject(projectId);
      setNotice({ kind: "success", text: copy.projectRemoved(project.name) });
      setCollapsedProjectIds((current) => {
        const next = new Set(current);
        next.delete(projectId);
        persistIds(COLLAPSED_PROJECTS_STORAGE_KEY, next);
        return next;
      });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : copy.projectRemoveFailed });
    }
  };

  const openContextMenu = (thread: ThreadRecord, x: number, y: number) => {
    if (contextMenuCloseTimerRef.current !== null) window.clearTimeout(contextMenuCloseTimerRef.current);
    contextMenuCloseTimerRef.current = null;
    setContextMenuClosing(false);
    const width = 264;
    const height = 430;
    setCopyExpanded(false);
    setProjectExpanded(false);
    setDeleteArmed(false);
    setContextMenu({
      threadId: thread.id,
      x: Math.max(8, Math.min(x, window.innerWidth - width - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - height - 8)),
    });
  };

  useEffect(() => () => {
    if (contextMenuCloseTimerRef.current !== null) window.clearTimeout(contextMenuCloseTimerRef.current);
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2400);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!contextMenu) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      closeContextMenu();
    };
    const close = () => closeContextMenu();
    window.addEventListener("pointerdown", handlePointerDown, true);
    window.addEventListener("blur", close);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown, true);
      window.removeEventListener("blur", close);
      window.removeEventListener("resize", close);
    };
  }, [contextMenu]);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      const commandKey = event.metaKey || event.ctrlKey;

      if (commandKey && !event.altKey && !event.shiftKey && key === "k") {
        event.preventDefault();
        focusSearch();
        return;
      }

      if (commandKey && !event.altKey && !event.shiftKey && key === "n") {
        event.preventDefault();
        void onNew();
        return;
      }

      if (event.key === "Escape" && contextMenu) {
        event.preventDefault();
        closeContextMenu();
        return;
      }

      const target = event.target as HTMLElement | null;
      const editing = Boolean(target?.matches("input, textarea, [contenteditable='true']"));
      if (editing || !activeThread) return;

      if (event.ctrlKey && event.altKey && !event.shiftKey && key === "r") {
        event.preventDefault();
        beginRename(activeThread);
      } else if (event.ctrlKey && event.altKey && !event.shiftKey && key === "p") {
        event.preventDefault();
        togglePinned(activeThread.id);
      } else if (event.ctrlKey && event.shiftKey && !event.altKey && key === "u") {
        event.preventDefault();
        toggleUnread(activeThread.id);
      } else if (event.ctrlKey && event.shiftKey && !event.altKey && key === "a" && !threadIsBusy(activeThread)) {
        event.preventDefault();
        void archiveThread(activeThread);
      }
    };

    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [activeThread, contextMenu, onNew, pinnedIds, unreadIds]);

  const renderThreadRow = (thread: ThreadRecord) => {
    const active = thread.id === activeId;
    const running = threadIsBusy(thread);
    const pinned = pinnedIds.has(thread.id);
    const unread = unreadIds.has(thread.id);
    const busy = busyThreadId === thread.id;
    const time = relativeTime(thread.updatedAt);
    const renaming = renamingId === thread.id;

    return (
      <div
        key={thread.id}
        className={`compact-thread-row ${active ? "active" : ""} ${pinned ? "pinned" : ""} ${unread ? "unread" : ""} ${renaming ? "renaming" : ""}`}
        data-loom-own-menu=""
        onContextMenu={(event) => {
          event.preventDefault();
          openContextMenu(thread, event.clientX, event.clientY);
        }}
      >
        {renaming ? (
          <div className="compact-thread-main rename-main">
            <input
              ref={renameRef}
              className="compact-thread-rename"
              value={renameValue}
              maxLength={120}
              onChange={(event) => setRenameValue(event.target.value)}
              onBlur={() => void commitRename(thread)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  event.currentTarget.blur();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  setRenamingId(null);
                }
              }}
              aria-label={copy.renameConversation}
            />
            <span className={`compact-thread-dot ${running ? "running" : unread ? "unread" : ""}`} aria-hidden="true" />
          </div>
        ) : (
          <button
            className="compact-thread-main"
            onPointerEnter={() => schedulePrefetch(thread.id)}
            onPointerLeave={cancelScheduledPrefetch}
            onPointerDown={() => {
              cancelScheduledPrefetch();
              onPrefetch?.(thread.id);
            }}
            onFocus={() => onPrefetch?.(thread.id)}
            onClick={() => void openThread(thread)}
            type="button"
            title={`${thread.title || copy.untitled}${time ? ` · ${time}` : ""}`}
            aria-current={active ? "page" : undefined}
          >
            <span className="compact-thread-title">{thread.title || copy.untitled}</span>
            <span className={`compact-thread-dot ${running ? "running" : unread ? "unread" : ""}`} aria-hidden="true" />
            {running ? <span className="sr-only">{copy.running}</span> : null}
            {unread ? <span className="sr-only">{copy.unread}</span> : null}
          </button>
        )}

        <div className="thread-quick-actions" aria-label={copy.conversationActions}>
          <button
            type="button"
            className={pinned ? "is-active" : ""}
            onClick={() => togglePinned(thread.id)}
            title={pinned ? copy.unpin : copy.pin}
            aria-label={pinned ? copy.unpin : copy.pin}
            disabled={busy}
          >
            {/* Filled when pinned: the button doubles as the row's pinned mark. */}
            <Pin size={13} strokeWidth={1.8} fill={pinned ? "currentColor" : "none"} />
          </button>
          <button
            type="button"
            onClick={() => void archiveThread(thread)}
            title={thread.archived ? copy.restore : copy.archive}
            aria-label={thread.archived ? copy.restore : copy.archive}
            disabled={busy || running}
          >
            {thread.archived ? <ArchiveRestore size={13} strokeWidth={1.8} /> : <Archive size={13} strokeWidth={1.8} />}
          </button>
          <button
            type="button"
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              openContextMenu(thread, rect.right + 7, rect.top - 5);
            }}
            title={copy.moreActions}
            aria-label={copy.moreActions}
            disabled={busy}
          >
            <Ellipsis size={14} strokeWidth={1.9} />
          </button>
        </div>
      </div>
    );
  };

  const renderProjects = () => {
    if (!projectsSupported || threadView === "archived") return null;
    if (!projectSections.length) return null;

    return (
      <section className="sidebar-section project-section" aria-label={copy.projects}>
        <div className="sidebar-section-title">
          <span>{copy.projects}</span>
          <button type="button" onClick={() => void addProject()} title={copy.addProject} aria-label={copy.addProject}>
            <Plus size={13} strokeWidth={1.8} />
          </button>
        </div>

        <div className="project-list">
          {projectSections.map(({ project, threads: projectThreads }) => {
            const collapsed = collapsedProjectIds.has(project.id);
            const selected = activeProjectId === project.id;
            const visibleCount = projectThreads.length || project.threadCount || 0;
            return (
              <section className={`project-group ${collapsed ? "is-collapsed" : ""} ${selected ? "is-selected" : ""}`} key={project.id}>
                <div className="project-group-row">
                  {renamingProjectId === project.id ? (
                    <input
                      className="project-rename-input"
                      autoFocus
                      value={projectRenameValue}
                      maxLength={60}
                      onChange={(event) => setProjectRenameValue(event.target.value)}
                      onBlur={() => void commitProjectRename()}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          event.currentTarget.blur();
                        } else if (event.key === "Escape") {
                          setRenamingProjectId("");
                        }
                      }}
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        className="project-disclosure-button"
                        onClick={() => toggleProjectCollapsed(project.id)}
                        title={collapsed ? copy.expand(project.name) : copy.collapse(project.name)}
                        aria-label={collapsed ? copy.expand(project.name) : copy.collapse(project.name)}
                        aria-expanded={!collapsed}
                      >
                        <ChevronRight className="project-disclosure-chevron" size={14} strokeWidth={1.9} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="project-group-main"
                        onClick={() => void onOpenProject(project.id)}
                        title={copy.openProject(project.root)}
                        aria-current={selected ? "page" : undefined}
                      >
                        {collapsed
                          ? <Folder size={16} strokeWidth={1.75} aria-hidden="true" />
                          : <FolderOpen size={16} strokeWidth={1.75} aria-hidden="true" />}
                        <span>{project.name}</span>
                        {visibleCount ? <small>{visibleCount}</small> : null}
                      </button>
                    </>
                  )}

                  <div className="project-group-actions" aria-label={copy.projects}>
                    <button type="button" onClick={() => void onNew(project.root || undefined, project.id)} title={copy.newInProject(project.name)} aria-label={copy.newInProject(project.name)}>
                      <Plus size={14} strokeWidth={1.8} />
                    </button>
                    <button type="button" onClick={() => beginProjectRename(project.id)} title={copy.renameProject(project.name)} aria-label={copy.renameProject(project.name)}>
                      <Pencil size={13.5} strokeWidth={1.8} />
                    </button>
                    <button type="button" className="danger" onClick={() => void confirmRemoveProject(project.id)} title={copy.removeProject(project.name)} aria-label={copy.removeProject(project.name)}>
                      <Trash2 size={13.5} strokeWidth={1.8} />
                    </button>
                  </div>
                </div>

                {projectThreads.length ? (
                  <div className={`project-thread-list-shell ${collapsed ? "" : "open"}`.trim()} aria-hidden={collapsed}>
                    <div className="project-thread-list-inner">
                      <div className="project-thread-list">
                        {projectThreads.map(renderThreadRow)}
                      </div>
                    </div>
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      </section>
    );
  };

  const renderRecent = () => {
    if (!projectsSupported || threadView === "archived") return null;
    if (!normalThreads.length) return null;
    return (
      <section className="sidebar-section recent-section" aria-label={copy.recent}>
        <div className="sidebar-section-title"><span>{copy.recent}</span></div>
        <div className="workspace-thread-list recent-thread-list">
          {normalThreads.map(renderThreadRow)}
        </div>
      </section>
    );
  };

  return (
    <aside className="sidebar compact-sidebar codex-sidebar">
      <div className="codex-sidebar-topbar">
        <div className="codex-sidebar-brand">
          <span className="codex-sidebar-brand-mark" aria-hidden="true">
            <span className="codex-sidebar-brand-aura" />
            <span className="codex-sidebar-brand-orbit codex-sidebar-brand-orbit-a codex-sidebar-brand-orbit-back" />
            <span className="codex-sidebar-brand-orbit codex-sidebar-brand-orbit-b codex-sidebar-brand-orbit-back" />
            <span className="codex-sidebar-brand-core" />
            <span className="codex-sidebar-brand-orbit codex-sidebar-brand-orbit-a codex-sidebar-brand-orbit-front" />
            <span className="codex-sidebar-brand-orbit codex-sidebar-brand-orbit-b codex-sidebar-brand-orbit-front" />
            <span className="codex-sidebar-brand-spark codex-sidebar-brand-spark-a" />
            <span className="codex-sidebar-brand-spark codex-sidebar-brand-spark-b" />
          </span>
          <span className="codex-sidebar-brand-label">{threadView === "archived" ? copy.archive : "Loom"}</span>
        </div>
        <div className="compact-sidebar-actions">
          <button
            type="button"
            onClick={searchOpen ? closeSearch : focusSearch}
            className={searchOpen ? "active" : ""}
            title={`${copy.search} · Ctrl K`}
            aria-label={copy.search}
          >
            <Search size={16} strokeWidth={1.85} />
          </button>
        </div>
      </div>

      {threadView !== "archived" ? (
        <div className="codex-sidebar-primary">
          <button
            type="button"
            className="sidebar-new-conversation"
            onClick={() => void onNew()}
            title={`${copy.newConversation} · Ctrl N`}
          >
            <svg className="sidebar-action-icon sidebar-compose-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path className="sidebar-compose-frame" d="M12 4H6.8A2.8 2.8 0 0 0 4 6.8v10.4A2.8 2.8 0 0 0 6.8 20h10.4a2.8 2.8 0 0 0 2.8-2.8V12" />
              <path className="sidebar-compose-pencil" d="m14.1 5.9 4-4a1.55 1.55 0 0 1 2.2 2.2l-4 4-5.15 1.05L12.2 4Z" />
            </svg>
            <span>{copy.newConversation}</span>
          </button>
        </div>
      ) : null}

      <div className={`compact-search-shell ${searchOpen ? "open" : ""}`} aria-hidden={!searchOpen}>
        <div className="compact-search">
          <Search size={14} strokeWidth={1.8} aria-hidden="true" />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                closeSearch();
              }
            }}
            placeholder={threadView === "archived" ? copy.searchArchived : copy.search}
            aria-label={copy.search}
            tabIndex={searchOpen ? 0 : -1}
          />
          {query ? (
            <button type="button" onClick={() => setQuery("")} aria-label={copy.clearSearch}>
              <X size={13} strokeWidth={1.9} />
            </button>
          ) : null}
        </div>
      </div>

      <div className="compact-thread-scroll" aria-label={threadView === "archived" ? copy.archive : copy.conversations}>
        {renderProjects()}
        {renderRecent()}

        {(!projectsSupported || threadView === "archived") ? (
          <>
            {legacyWorkspaceGroups.map((group) => (
              <section className="workspace-group" key={group.key}>
                <div className="workspace-group-header">
                  <span className="workspace-group-label" title={group.workspace || group.displayLabel}>{group.displayLabel}</span>
                  {group.threads.length ? <span className="workspace-group-count">{group.threads.length}</span> : null}
                  {threadView !== "archived" ? (
                    <button
                      type="button"
                      onClick={() => void onNew(group.workspace || undefined)}
                      title={copy.newInProject(group.displayLabel)}
                      aria-label={copy.newInProject(group.displayLabel)}
                    >
                      <Plus size={15} strokeWidth={1.7} />
                    </button>
                  ) : null}
                </div>
                <div className="workspace-thread-list">
                  {group.threads.map(renderThreadRow)}
                </div>
              </section>
            ))}
          </>
        ) : null}

        {projectsSupported && threadView !== "archived" && !projectSections.length && !normalThreads.length ? (
          <div className="compact-sidebar-empty">
            <span>{query ? copy.noMatches : copy.noConversations}</span>
            <button type="button" onClick={query ? () => setQuery("") : () => void onNew()}>
              {query ? copy.clearSearch : copy.newConversation}
            </button>
          </div>
        ) : null}

        {(!projectsSupported || threadView === "archived") && !legacyWorkspaceGroups.length ? (
          <div className="compact-sidebar-empty">
            <span>{query ? copy.noMatches : threadView === "archived" ? copy.archiveEmpty : copy.noConversations}</span>
            <button
              type="button"
              onClick={query ? () => setQuery("") : threadView === "archived" ? () => void onViewChange("active") : () => void onNew()}
            >
              {query ? copy.clearSearch : threadView === "archived" ? copy.backToConversations : copy.newConversation}
            </button>
          </div>
        ) : null}
      </div>

      {notice ? <div className={`sidebar-notice ${notice.kind}`}>{notice.text}</div> : null}

      <div className="compact-sidebar-footer">
        <button
          type="button"
          onClick={() => void onViewChange(threadView === "archived" ? "active" : "archived")}
          title={threadView === "archived" ? copy.backToConversations : copy.openArchive}
        >
          {threadView === "archived" ? <ArrowLeft size={14} strokeWidth={1.7} /> : <Archive size={14} strokeWidth={1.7} />}
          <span>{threadView === "archived" ? copy.conversations : copy.archive}</span>
          {threadView === "active" && archivedCount > 0 ? <span className="archive-count">{archivedCount}</span> : null}
        </button>
      </div>

      {contextMenu && menuThread ? createPortal(
        <div
          ref={menuRef}
          className={`thread-context-menu ${contextMenuClosing ? "is-closing" : ""}`.trim()}
          style={{ left: contextMenu.x, top: contextMenu.y }}
          role="menu"
          aria-label={copy.conversationActions}
        >
          <button type="button" role="menuitem" onClick={() => beginRename(menuThread)}>
            <Pencil size={16} strokeWidth={1.75} />
            <span>{copy.rename}</span>
            <kbd>Ctrl+Alt+R</kbd>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              togglePinned(menuThread.id);
              closeContextMenu();
            }}
          >
            {pinnedIds.has(menuThread.id) ? <PinOff size={16} strokeWidth={1.75} /> : <Pin size={16} strokeWidth={1.75} />}
            <span>{pinnedIds.has(menuThread.id) ? copy.unpin : copy.pin}</span>
            <kbd>Ctrl+Alt+P</kbd>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              toggleUnread(menuThread.id);
              closeContextMenu();
            }}
          >
            {unreadIds.has(menuThread.id) ? <Eye size={16} strokeWidth={1.75} /> : <EyeOff size={16} strokeWidth={1.75} />}
            <span>{unreadIds.has(menuThread.id) ? copy.markRead : copy.markUnread}</span>
            <kbd>Ctrl+Shift+U</kbd>
          </button>

          {projectsSupported && (projects.length > 0 || Boolean(menuThread.projectId)) ? (
            <>
              <button
                type="button"
                role="menuitem"
                className={projectExpanded ? "submenu-open" : ""}
                disabled={threadIsBusy(menuThread)}
                title={threadIsBusy(menuThread) ? copy.busyMove : undefined}
                onClick={() => {
                  setProjectExpanded((current) => !current);
                  setCopyExpanded(false);
                }}
              >
                {menuThread.projectId ? <Folder size={16} strokeWidth={1.75} /> : <FolderPlus size={16} strokeWidth={1.75} />}
                <span>{menuThread.projectId ? copy.moveToProject : copy.putInProject}</span>
                <ChevronRight className="menu-chevron" size={15} strokeWidth={1.75} />
              </button>
              {projectExpanded ? (
                <div className="thread-project-submenu" role="group" aria-label={copy.moveToProject}>
                  {menuThread.projectId ? (
                    <button
                      type="button"
                      className="thread-project-remove-option"
                      onClick={() => void moveThreadProject(menuThread, "")}
                      disabled={threadIsBusy(menuThread)}
                    >
                      <X size={14} strokeWidth={1.8} />
                      <span>{copy.removeFromProject}</span>
                    </button>
                  ) : null}
                  {menuThread.projectId && projects.length ? <div className="thread-project-submenu-separator" /> : null}
                  {projects.map((project) => {
                    const selected = project.id === menuThread.projectId;
                    return (
                      <button
                        type="button"
                        key={project.id}
                        className={selected ? "selected" : ""}
                        onClick={() => void moveThreadProject(menuThread, project.id)}
                        disabled={selected || threadIsBusy(menuThread)}
                        title={project.root}
                      >
                        <Folder size={14} strokeWidth={1.75} />
                        <span>{project.name}</span>
                        {selected ? <small>{copy.current}</small> : null}
                      </button>
                    );
                  })}
                  {!projects.length && !menuThread.projectId ? <div className="thread-project-submenu-empty">{copy.noProjects}</div> : null}
                </div>
              ) : null}
            </>
          ) : null}

          <button
            type="button"
            role="menuitem"
            disabled={threadIsBusy(menuThread)}
            onClick={() => void archiveThread(menuThread)}
          >
            {menuThread.archived ? <ArchiveRestore size={16} strokeWidth={1.75} /> : <Archive size={16} strokeWidth={1.75} />}
            <span>{menuThread.archived ? copy.restore : copy.archive}</span>
            <kbd>Ctrl+Shift+A</kbd>
          </button>

          <div className="thread-menu-separator" />

          <button
            type="button"
            role="menuitem"
            className={copyExpanded ? "submenu-open" : ""}
            onClick={() => {
              setCopyExpanded((current) => !current);
              setProjectExpanded(false);
            }}
          >
            <Copy size={16} strokeWidth={1.75} />
            <span>{copy.copy}</span>
            <ChevronRight className="menu-chevron" size={15} strokeWidth={1.75} />
          </button>
          {copyExpanded ? (
            <div className="thread-copy-submenu" role="group" aria-label={copy.copy}>
              <button type="button" onClick={() => void copyThreadValue(menuThread, copy.titleLabel, menuThread.title || copy.untitled)}>{copy.copyTitle}</button>
              <button type="button" onClick={() => void copyThreadValue(menuThread, copy.idLabel, menuThread.id)}>{copy.copyId}</button>
              <button type="button" onClick={() => void copyThreadValue(menuThread, copy.pathLabel, menuThread.workspace || "")}>{copy.copyPath}</button>
            </div>
          ) : null}

          <button
            type="button"
            role="menuitem"
            disabled={threadIsBusy(menuThread)}
            onClick={() => void forkThread(menuThread)}
          >
            <GitFork size={16} strokeWidth={1.75} />
            <span>{copy.fork}</span>
          </button>

          <div className="thread-menu-separator" />

          <button
            type="button"
            role="menuitem"
            className={`destructive ${deleteArmed ? "armed" : ""}`}
            disabled={threadIsBusy(menuThread)}
            onClick={() => {
              if (!deleteArmed) {
                setDeleteArmed(true);
                return;
              }
              void deleteThread(menuThread);
            }}
          >
            <Trash2 size={16} strokeWidth={1.75} />
            <span>{deleteArmed ? copy.deleteConfirm : copy.deleteConversation}</span>
          </button>
        </div>,
        document.body,
      ) : null}
    </aside>
  );
}
