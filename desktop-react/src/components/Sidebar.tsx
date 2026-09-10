import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  ChevronRight,
  Copy,
  Ellipsis,
  Eye,
  FolderPlus,
  EyeOff,
  GitFork,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ProjectRecord, ThreadRecord } from "../types/loom";
import "./sidebar.css";

type ThreadView = "active" | "archived";
type Notice = { kind: "success" | "error"; text: string };
type ContextMenuState = { threadId: string; x: number; y: number };

type GroupMenuState = { projectId: string; x: number; y: number };

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
    // Sidebar state still works for this process when storage is unavailable.
  }
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
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [groupMenu, setGroupMenu] = useState<GroupMenuState | null>(null);
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

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return threads;
    return threads.filter((thread) => `${thread.title} ${thread.workspace}`.toLowerCase().includes(needle));
  }, [query, threads]);

  const groups = useMemo(() => {
    const sortThreads = (rows: ThreadRecord[]) =>
      [...rows].sort((left, right) => {
        const pinDelta = Number(pinnedIds.has(right.id)) - Number(pinnedIds.has(left.id));
        if (pinDelta) return pinDelta;
        return Date.parse(right.updatedAt || "") - Date.parse(left.updatedAt || "");
      });

    // Without server projects the only thing to group by is the workspace path,
    // which is what this sidebar did before projects were durable. Keeping that
    // path means an older App Server still gets a grouped list rather than an
    // empty one.
    if (!projectsSupported) {
      const byWorkspace = new Map<string, { key: string; label: string; workspace: string; threads: ThreadRecord[] }>();
      for (const thread of filtered) {
        const normalized = normalizeWorkspace(thread.workspace);
        const key = normalized || "__other__";
        const existing = byWorkspace.get(key);
        if (existing) existing.threads.push(thread);
        else
          byWorkspace.set(key, {
            key,
            label: workspaceLabel(thread.workspace),
            workspace: thread.workspace || "",
            threads: [thread],
          });
      }
      const derived = [...byWorkspace.values()];
      const labelCounts = new Map<string, number>();
      for (const group of derived) labelCounts.set(group.label, (labelCounts.get(group.label) ?? 0) + 1);
      return derived.map((group) => ({
        ...group,
        projectId: "",
        displayLabel:
          (labelCounts.get(group.label) ?? 0) > 1
            ? `${group.label} · ${workspaceParentLabel(group.workspace) || "workspace"}`
            : group.label,
        threads: sortThreads(group.threads),
      }));
    }

    // A project is an entity, so it gets a heading whether or not anything has
    // been said in it yet. That is the whole difference between a project and a
    // label derived from the rows underneath it -- an empty project is somewhere
    // you can start.
    const known = new Set(projects.map((project) => project.id));
    const byProject = new Map<string, ThreadRecord[]>();
    const unfiled: ThreadRecord[] = [];
    for (const thread of filtered) {
      const projectId = (thread.projectId || "").trim();
      if (projectId && known.has(projectId)) {
        const rows = byProject.get(projectId);
        if (rows) rows.push(thread);
        else byProject.set(projectId, [thread]);
      } else {
        unfiled.push(thread);
      }
    }

    const result = projects.map((project) => ({
      key: project.id,
      projectId: project.id,
      label: project.name,
      displayLabel: project.name,
      workspace: project.root,
      threads: sortThreads(byProject.get(project.id) ?? []),
    }));

    if (unfiled.length) {
      // Not a project: no id, so it offers no project controls. There is
      // nothing here to rename and nothing to remove.
      result.push({
        key: "__unfiled__",
        projectId: "",
        label: "No project",
        displayLabel: "No project",
        workspace: "",
        threads: sortThreads(unfiled),
      });
    }
    return result;
  }, [filtered, pinnedIds, projects, projectsSupported]);

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

  const updateStoredId = (
    key: string,
    setter: React.Dispatch<React.SetStateAction<Set<string>>>,
    threadId: string,
    enabled: boolean,
  ) => {
    setter((current) => {
      const next = new Set(current);
      if (enabled) next.add(threadId);
      else next.delete(threadId);
      persistIds(key, next);
      return next;
    });
  };

  const togglePinned = (threadId: string) => {
    updateStoredId(PINNED_STORAGE_KEY, setPinnedIds, threadId, !pinnedIds.has(threadId));
  };

  const toggleUnread = (threadId: string) => {
    updateStoredId(UNREAD_STORAGE_KEY, setUnreadIds, threadId, !unreadIds.has(threadId));
  };

  const markRead = (threadId: string) => {
    if (unreadIds.has(threadId)) updateStoredId(UNREAD_STORAGE_KEY, setUnreadIds, threadId, false);
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
      updateStoredId(PINNED_STORAGE_KEY, setPinnedIds, thread.id, false);
      updateStoredId(UNREAD_STORAGE_KEY, setUnreadIds, thread.id, false);
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
      setNotice({ kind: "success", text: "Project added." });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "Could not add the project." });
    }
  };

  const beginProjectRename = (projectId: string) => {
    const project = projects.find((entry) => entry.id === projectId);
    if (!project) return;
    setGroupMenu(null);
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
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "Could not rename the project." });
    }
  };

  const confirmRemoveProject = async (projectId: string) => {
    const project = projects.find((entry) => entry.id === projectId);
    if (!project) return;
    setGroupMenu(null);
    // "Remove" next to a folder full of work has to be unambiguous about which
    // of the two it means, so the prompt names what survives.
    const count = project.threadCount;
    const lines = [
      `Remove ${project.name} from Loom's project list?`,
      "The folder and its files are not touched.",
    ];
    if (count) {
      lines.push(`${count} conversation${count === 1 ? "" : "s"} stay in Loom and become unfiled.`);
    }
    if (!window.confirm(lines.join("\n\n"))) return;
    try {
      await onRemoveProject(projectId);
      setNotice({ kind: "success", text: `${project.name} removed from the list.` });
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

  return (
    <aside className="sidebar compact-sidebar">
      <div className="compact-sidebar-header">
        <strong>{threadView === "archived" ? "Archived" : "Loom"}</strong>
        <div className="compact-sidebar-actions">
          <button type="button" onClick={() => void onNew()} title="New thread" aria-label="New thread">
            <Plus size={17} strokeWidth={1.8} />
          </button>
          {/* Adding a project is a different act from starting a conversation:
              it puts a folder in the list without opening anything in it. */}
          {projectsSupported && threadView !== "archived" ? (
            <button type="button" onClick={() => void addProject()} title="Add project folder" aria-label="Add project folder">
              <FolderPlus size={16} strokeWidth={1.8} />
            </button>
          ) : null}
          <button
            type="button"
            onClick={searchOpen ? closeSearch : focusSearch}
            className={searchOpen ? "active" : ""}
            title="Search conversations"
            aria-label="Search conversations"
          >
            <Search size={15} strokeWidth={1.8} />
          </button>
        </div>
      </div>

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
        {groups.map((group) => (
          <section className="workspace-group" key={group.key}>
            <div className="workspace-group-header">
              {renamingProjectId === group.projectId && group.projectId ? (
                <input
                  className="workspace-group-rename"
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
                <span className="workspace-group-label" title={group.workspace || group.displayLabel}>{group.displayLabel}</span>
              )}
              {group.threads.length ? (
                <span className="workspace-group-count">{group.threads.length}</span>
              ) : null}
              <button
                type="button"
                onClick={() => void onNew(group.workspace || undefined, group.projectId || undefined)}
                title={`New thread in ${group.displayLabel}`}
                aria-label={`New thread in ${group.displayLabel}`}
              >
                <Plus size={15} strokeWidth={1.7} />
              </button>
              {/* Unfiled is not a project: nothing to rename, nothing to remove. */}
              {group.projectId ? (
                <button
                  type="button"
                  className="workspace-group-menu-button"
                  onClick={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    setGroupMenu({ projectId: group.projectId, x: rect.left, y: rect.bottom + 4 });
                  }}
                  title={`Rename or remove ${group.displayLabel}`}
                  aria-label={`Project actions for ${group.displayLabel}`}
                >
                  <Ellipsis size={15} strokeWidth={1.7} />
                </button>
              ) : null}
            </div>

            {group.projectId && !group.threads.length ? (
              <p className="workspace-group-empty">No conversations yet</p>
            ) : null}

            <div className="workspace-thread-list">
              {group.threads.map((thread) => {
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
              })}
            </div>
          </section>
        ))}

        {!groups.length ? (
          <div className="compact-sidebar-empty">
            <span>{query ? "No matching conversations" : threadView === "archived" ? "Archive is empty" : "No conversations yet"}</span>
            <button
              type="button"
              onClick={query ? () => setQuery("") : threadView === "archived" ? () => void onViewChange("active") : () => void onNew()}
            >
              {query ? "Clear search" : threadView === "archived" ? "Back to conversations" : "New thread"}
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

      {groupMenu ? createPortal(
        <>
          <div className="workspace-group-menu-scrim" onClick={() => setGroupMenu(null)} />
          <div className="workspace-group-menu" style={{ left: groupMenu.x, top: groupMenu.y }} role="menu">
            <button type="button" onClick={() => void onNew(undefined, groupMenu.projectId)}>
              <Plus size={14} strokeWidth={1.7} />
              <span>New conversation</span>
            </button>
            <button type="button" onClick={() => beginProjectRename(groupMenu.projectId)}>
              <Pencil size={14} strokeWidth={1.7} />
              <span>Rename project</span>
            </button>
            <button type="button" className="danger" onClick={() => void confirmRemoveProject(groupMenu.projectId)}>
              <Trash2 size={14} strokeWidth={1.7} />
              <span>Remove from Loom</span>
            </button>
          </div>
        </>,
        document.body,
      ) : null}

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
