import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Copy,
  Ellipsis,
  Eye,
  EyeOff,
  Folder,
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
import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { createPortal } from "react-dom";
import type { ProjectRecord, ThreadRecord } from "../types/loom";
import "./sidebar.css";

type ThreadView = "active" | "archived";
type Notice = { kind: "success" | "error"; text: string };
type ContextMenuState = { threadId: string; x: number; y: number };

interface SidebarProps {
  threads: ThreadRecord[];
  projects: ProjectRecord[];
  projectsSupported: boolean;
  activeId?: string;
  threadView: ThreadView;
  archivedCount: number;
  onOpen(threadId: string): Promise<void> | void;
  onNew(workspace?: string, projectId?: string): Promise<void> | void;
  onAddProject(root: string): Promise<ProjectRecord | void>;
  onRenameProject(projectId: string, name: string): Promise<void>;
  onRemoveProject(projectId: string): Promise<void>;
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

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
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
  threadView,
  archivedCount,
  onOpen,
  onNew,
  projects,
  projectsSupported,
  onAddProject,
  onRenameProject,
  onRemoveProject,
  onRename,
  onArchive,
  onDelete,
  onFork,
  onViewChange,
}: SidebarProps) {
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() => readStoredIds(PINNED_STORAGE_KEY));
  const [unreadIds, setUnreadIds] = useState<Set<string>>(() => readStoredIds(UNREAD_STORAGE_KEY));
  const [collapsedProjectIds, setCollapsedProjectIds] = useState<Set<string>>(() => readStoredIds(COLLAPSED_PROJECTS_STORAGE_KEY));
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [renamingProjectId, setRenamingProjectId] = useState("");
  const [projectRenameValue, setProjectRenameValue] = useState("");
  const [copyExpanded, setCopyExpanded] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [busyThreadId, setBusyThreadId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const renameRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const renameCommittingRef = useRef(false);

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
        return project.name.toLowerCase().includes(needle) || project.root.toLowerCase().includes(needle) || rows.length > 0;
      });
  }, [filtered, pinnedIds, projects, query]);

  const normalThreads = useMemo(() => (
    sortThreads(filtered.filter((thread) => !threadBelongsToProject(thread, projectIds)), pinnedIds)
  ), [filtered, pinnedIds, projectIds]);

  const legacyWorkspaceGroups = useMemo(() => {
    if (projectsSupported) return [];
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
  }, [filtered, pinnedIds, projectsSupported]);

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
    const activeProjectId = (activeThread?.projectId || "").trim();
    if (!activeProjectId || !collapsedProjectIds.has(activeProjectId)) return;
    setCollapsedProjectIds((current) => {
      const next = new Set(current);
      next.delete(activeProjectId);
      persistIds(COLLAPSED_PROJECTS_STORAGE_KEY, next);
      return next;
    });
  }, [activeThread?.projectId, collapsedProjectIds]);

  const markRead = (threadId: string) => {
    if (unreadIds.has(threadId)) updateStoredSet(UNREAD_STORAGE_KEY, setUnreadIds, threadId, false);
  };

  const openThread = async (thread: ThreadRecord) => {
    markRead(thread.id);
    setContextMenu(null);
    await onOpen(thread.id);
  };

  const beginRename = (thread: ThreadRecord) => {
    setContextMenu(null);
    setDeleteArmed(false);
    setRenamingId(thread.id);
    setRenameValue(thread.title || "New conversation");
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
      setNotice({ kind: "success", text: "会话已重命名" });
    } catch (cause) {
      setNotice({ kind: "error", text: `重命名失败：${errorText(cause)}` });
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
    setContextMenu(null);
    setCopyExpanded(false);
    setDeleteArmed(false);
    setBusyThreadId(thread.id);
    try {
      await action();
      setNotice({ kind: "success", text: successText });
    } catch (cause) {
      setNotice({ kind: "error", text: errorText(cause) });
    } finally {
      setBusyThreadId(null);
    }
  };

  const archiveThread = (thread: ThreadRecord) => {
    const nextArchived = !Boolean(thread.archived);
    return runRemoteAction(
      thread,
      nextArchived ? "会话已归档" : "会话已恢复",
      () => onArchive(thread.id, nextArchived),
    );
  };

  const forkThread = (thread: ThreadRecord) => runRemoteAction(
    thread,
    "已创建分叉会话",
    () => onFork(thread.id),
  );

  const deleteThread = (thread: ThreadRecord) => runRemoteAction(
    thread,
    "会话已删除",
    async () => {
      await onDelete(thread.id);
      updateStoredSet(PINNED_STORAGE_KEY, setPinnedIds, thread.id, false);
      updateStoredSet(UNREAD_STORAGE_KEY, setUnreadIds, thread.id, false);
    },
  );

  const copyThreadValue = async (thread: ThreadRecord, label: string, value: string) => {
    try {
      await writeClipboard(value);
      setNotice({ kind: "success", text: `${label}已复制` });
      setContextMenu(null);
      setCopyExpanded(false);
    } catch (cause) {
      setNotice({ kind: "error", text: `复制失败：${errorText(cause)}` });
    }
  };

  const addProject = async () => {
    const picked = await window.loom?.pickDirectory?.();
    if (!picked) return;
    try {
      await onAddProject(picked);
      setNotice({ kind: "success", text: "项目已添加" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "Could not add the project." });
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
      setNotice({ kind: "success", text: "项目已重命名" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "Could not rename the project." });
    }
  };

  const confirmRemoveProject = async (projectId: string) => {
    const project = projects.find((entry) => entry.id === projectId);
    if (!project) return;
    const count = project.threadCount;
    const lines = [
      `Remove ${project.name} from Loom's project list?`,
      "The folder and its files are not touched.",
    ];
    if (count) lines.push(`${count} conversation${count === 1 ? "" : "s"} stay in Loom and become unfiled.`);
    if (!window.confirm(lines.join("\n\n"))) return;
    try {
      await onRemoveProject(projectId);
      setNotice({ kind: "success", text: `${project.name} removed from the list.` });
      setCollapsedProjectIds((current) => {
        const next = new Set(current);
        next.delete(projectId);
        persistIds(COLLAPSED_PROJECTS_STORAGE_KEY, next);
        return next;
      });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "Could not remove the project." });
    }
  };

  const openContextMenu = (thread: ThreadRecord, x: number, y: number) => {
    const width = 264;
    const height = 352;
    setCopyExpanded(false);
    setDeleteArmed(false);
    setContextMenu({
      threadId: thread.id,
      x: Math.max(8, Math.min(x, window.innerWidth - width - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - height - 8)),
    });
  };

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2400);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!contextMenu) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      setContextMenu(null);
    };
    const close = () => setContextMenu(null);
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
        setContextMenu(null);
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
        onContextMenu={(event) => {
          event.preventDefault();
          openContextMenu(thread, event.clientX, event.clientY);
        }}
      >
        {renaming ? (
          <div className="compact-thread-main rename-main">
            <span className={`compact-thread-dot ${running ? "running" : unread ? "unread" : ""}`} aria-hidden="true" />
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
              aria-label="Rename conversation"
            />
          </div>
        ) : (
          <button
            className="compact-thread-main"
            onClick={() => void openThread(thread)}
            type="button"
            title={`${thread.title || "New conversation"}${time ? ` · ${time}` : ""}${pinned ? " · pinned" : ""}`}
            aria-current={active ? "page" : undefined}
          >
            <span className={`compact-thread-dot ${running ? "running" : unread ? "unread" : ""}`} aria-hidden="true" />
            <span className="compact-thread-title">{thread.title || "New conversation"}</span>
            {running ? <span className="sr-only">Running</span> : null}
            {unread ? <span className="sr-only">Unread</span> : null}
          </button>
        )}

        <div className="thread-quick-actions" aria-label="Conversation quick actions">
          <button
            type="button"
            className={pinned ? "is-active" : ""}
            onClick={() => togglePinned(thread.id)}
            title={pinned ? "Unpin" : "Pin"}
            aria-label={pinned ? "Unpin conversation" : "Pin conversation"}
            disabled={busy}
          >
            {pinned ? <PinOff size={13} strokeWidth={1.8} /> : <Pin size={13} strokeWidth={1.8} />}
          </button>
          <button
            type="button"
            onClick={() => void archiveThread(thread)}
            title={thread.archived ? "Restore" : "Archive"}
            aria-label={thread.archived ? "Restore conversation" : "Archive conversation"}
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
            title="More actions"
            aria-label="More conversation actions"
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
      <section className="sidebar-section project-section" aria-label="Projects">
        <div className="sidebar-section-title">
          <span>项目</span>
          <button type="button" onClick={() => void addProject()} title="Add project" aria-label="Add project">
            <Plus size={13} strokeWidth={1.8} />
          </button>
        </div>

        <div className="project-list">
          {projectSections.map(({ project, threads: projectThreads }) => {
            const collapsed = collapsedProjectIds.has(project.id);
            const visibleCount = projectThreads.length || project.threadCount || 0;
            return (
              <section className={`project-group ${collapsed ? "is-collapsed" : ""}`} key={project.id}>
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
                    <button
                      type="button"
                      className="project-group-main"
                      onClick={() => toggleProjectCollapsed(project.id)}
                      title={`${collapsed ? "展开" : "折叠"} ${project.name} · ${project.root}`}
                      aria-expanded={!collapsed}
                    >
                      <ChevronRight className="project-disclosure-chevron" size={14} strokeWidth={1.9} aria-hidden="true" />
                      <Folder size={15} strokeWidth={1.75} aria-hidden="true" />
                      <span>{project.name}</span>
                      {visibleCount ? <small>{visibleCount}</small> : null}
                    </button>
                  )}

                  <div className="project-group-actions" aria-label="Project actions">
                    <button type="button" onClick={() => void onNew(project.root || undefined, project.id)} title={`New conversation in ${project.name}`} aria-label={`New conversation in ${project.name}`}>
                      <Plus size={14} strokeWidth={1.8} />
                    </button>
                    <button type="button" onClick={() => beginProjectRename(project.id)} title={`Rename ${project.name}`} aria-label={`Rename ${project.name}`}>
                      <Pencil size={13.5} strokeWidth={1.8} />
                    </button>
                    <button type="button" className="danger" onClick={() => void confirmRemoveProject(project.id)} title={`Remove ${project.name}`} aria-label={`Remove ${project.name}`}>
                      <Trash2 size={13.5} strokeWidth={1.8} />
                    </button>
                  </div>
                </div>

                {projectThreads.length && !collapsed ? (
                  <div className="project-thread-list">
                    {projectThreads.map(renderThreadRow)}
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
      <section className="sidebar-section recent-section" aria-label="Recent conversations">
        <div className="sidebar-section-title"><span>最近</span></div>
        <div className="workspace-thread-list recent-thread-list">
          {normalThreads.map(renderThreadRow)}
        </div>
      </section>
    );
  };

  return (
    <aside className="sidebar compact-sidebar codex-sidebar">
      <div className="codex-sidebar-topbar">
        <button type="button" className="codex-sidebar-brand" title="Loom" aria-label="Loom">
          <span>{threadView === "archived" ? "Archive" : "Loom"}</span>
          <ChevronDown size={14} strokeWidth={1.8} />
        </button>
        <div className="compact-sidebar-actions">
          <button
            type="button"
            onClick={searchOpen ? closeSearch : focusSearch}
            className={searchOpen ? "active" : ""}
            title="Search conversations"
            aria-label="Search conversations"
          >
            <Search size={16} strokeWidth={1.85} />
          </button>
        </div>
      </div>

      {threadView !== "archived" ? (
        <div className="codex-sidebar-primary">
          <button type="button" className="sidebar-new-conversation" onClick={() => void onNew()}>
            <Plus size={17} strokeWidth={1.9} />
            <span>新对话</span>
          </button>
          {projectsSupported ? (
            <button type="button" className="sidebar-secondary-action" onClick={() => void addProject()}>
              <FolderPlus size={16} strokeWidth={1.75} />
              <span>添加项目</span>
            </button>
          ) : null}
        </div>
      ) : null}

      <div className={`compact-search ${searchOpen ? "open" : ""}`} aria-hidden={!searchOpen}>
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
          placeholder={threadView === "archived" ? "Search archived" : "Search conversations"}
          aria-label="Search conversations"
          tabIndex={searchOpen ? 0 : -1}
        />
        {query ? (
          <button type="button" onClick={() => setQuery("")} aria-label="Clear search">
            <X size={13} strokeWidth={1.9} />
          </button>
        ) : null}
      </div>

      <div className="compact-thread-scroll" aria-label={threadView === "archived" ? "Archived conversations" : "Conversations"}>
        {renderRecent()}
        {renderProjects()}

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
                      title={`New thread in ${group.displayLabel}`}
                      aria-label={`New thread in ${group.displayLabel}`}
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
            <span>{query ? "No matching conversations" : "No conversations yet"}</span>
            <button type="button" onClick={query ? () => setQuery("") : () => void onNew()}>
              {query ? "Clear search" : "New conversation"}
            </button>
          </div>
        ) : null}

        {(!projectsSupported || threadView === "archived") && !legacyWorkspaceGroups.length ? (
          <div className="compact-sidebar-empty">
            <span>{query ? "No matching conversations" : threadView === "archived" ? "Archive is empty" : "No conversations yet"}</span>
            <button
              type="button"
              onClick={query ? () => setQuery("") : threadView === "archived" ? () => void onViewChange("active") : () => void onNew()}
            >
              {query ? "Clear search" : threadView === "archived" ? "Back to conversations" : "New conversation"}
            </button>
          </div>
        ) : null}
      </div>

      {notice ? <div className={`sidebar-notice ${notice.kind}`}>{notice.text}</div> : null}

      <div className="compact-sidebar-footer">
        <button
          type="button"
          onClick={() => void onViewChange(threadView === "archived" ? "active" : "archived")}
          title={threadView === "archived" ? "Back to conversations" : "Open archive"}
        >
          {threadView === "archived" ? <ArrowLeft size={14} strokeWidth={1.7} /> : <Archive size={14} strokeWidth={1.7} />}
          <span>{threadView === "archived" ? "Conversations" : "Archive"}</span>
          {threadView === "active" && archivedCount > 0 ? <span className="archive-count">{archivedCount}</span> : null}
        </button>
      </div>

      {contextMenu && menuThread ? createPortal(
        <div
          ref={menuRef}
          className="thread-context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          role="menu"
          aria-label="Conversation actions"
        >
          <button type="button" role="menuitem" onClick={() => beginRename(menuThread)}>
            <Pencil size={16} strokeWidth={1.75} />
            <span>重命名</span>
            <kbd>Ctrl+Alt+R</kbd>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              togglePinned(menuThread.id);
              setContextMenu(null);
            }}
          >
            {pinnedIds.has(menuThread.id) ? <PinOff size={16} strokeWidth={1.75} /> : <Pin size={16} strokeWidth={1.75} />}
            <span>{pinnedIds.has(menuThread.id) ? "取消置顶" : "置顶"}</span>
            <kbd>Ctrl+Alt+P</kbd>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              toggleUnread(menuThread.id);
              setContextMenu(null);
            }}
          >
            {unreadIds.has(menuThread.id) ? <Eye size={16} strokeWidth={1.75} /> : <EyeOff size={16} strokeWidth={1.75} />}
            <span>{unreadIds.has(menuThread.id) ? "标记为已读" : "标记为未读"}</span>
            <kbd>Ctrl+Shift+U</kbd>
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={threadIsBusy(menuThread)}
            onClick={() => void archiveThread(menuThread)}
          >
            {menuThread.archived ? <ArchiveRestore size={16} strokeWidth={1.75} /> : <Archive size={16} strokeWidth={1.75} />}
            <span>{menuThread.archived ? "恢复" : "归档"}</span>
            <kbd>Ctrl+Shift+A</kbd>
          </button>

          <div className="thread-menu-separator" />

          <button
            type="button"
            role="menuitem"
            className={copyExpanded ? "submenu-open" : ""}
            onClick={() => setCopyExpanded((current) => !current)}
          >
            <Copy size={16} strokeWidth={1.75} />
            <span>复制</span>
            <ChevronRight className="menu-chevron" size={15} strokeWidth={1.75} />
          </button>
          {copyExpanded ? (
            <div className="thread-copy-submenu" role="group" aria-label="Copy conversation data">
              <button type="button" onClick={() => void copyThreadValue(menuThread, "标题", menuThread.title || "New conversation")}>复制标题</button>
              <button type="button" onClick={() => void copyThreadValue(menuThread, "会话 ID", menuThread.id)}>复制会话 ID</button>
              <button type="button" onClick={() => void copyThreadValue(menuThread, "工作区路径", menuThread.workspace || "")}>复制工作区路径</button>
            </div>
          ) : null}

          <button
            type="button"
            role="menuitem"
            disabled={threadIsBusy(menuThread)}
            onClick={() => void forkThread(menuThread)}
          >
            <GitFork size={16} strokeWidth={1.75} />
            <span>分叉</span>
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
            <span>{deleteArmed ? "再次点击确认删除" : "删除会话"}</span>
          </button>
        </div>,
        document.body,
      ) : null}
    </aside>
  );
}
