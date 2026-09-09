import { Archive, Plus, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ThreadRecord } from "../types/loom";
import "./sidebar.css";

interface SidebarProps {
  threads: ThreadRecord[];
  activeId?: string;
  onOpen(threadId: string): void;
  onNew(): void;
}

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

function workspaceLabel(workspace?: string): string {
  const value = (workspace || "").trim();
  if (!value) return "Other";
  const normalized = value.replaceAll("\\", "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || "Other";
}

export function Sidebar({ threads, activeId, onOpen, onNew }: SidebarProps) {
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return threads;
    return threads.filter((thread) => `${thread.title} ${thread.workspace}`.toLowerCase().includes(needle));
  }, [query, threads]);

  const groups = useMemo(() => {
    const result: Array<{ label: string; threads: ThreadRecord[] }> = [];
    const index = new Map<string, number>();

    for (const thread of filtered) {
      const label = workspaceLabel(thread.workspace);
      const existing = index.get(label);
      if (existing !== undefined) {
        result[existing].threads.push(thread);
        continue;
      }
      index.set(label, result.length);
      result.push({ label, threads: [thread] });
    }

    return result;
  }, [filtered]);

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

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();

      if (key === "k") {
        event.preventDefault();
        focusSearch();
        return;
      }

      if (key === "n") {
        event.preventDefault();
        onNew();
      }
    };

    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [onNew]);

  return (
    <aside className="sidebar compact-sidebar">
      <div className="compact-sidebar-header">
        <strong>Loom</strong>
        <div className="compact-sidebar-actions">
          <button type="button" onClick={onNew} title="New thread" aria-label="New thread">
            <Plus size={17} strokeWidth={1.8} />
          </button>
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
          placeholder="Search conversations"
          aria-label="Search conversations"
          tabIndex={searchOpen ? 0 : -1}
        />
        {query ? (
          <button type="button" onClick={() => setQuery("")} aria-label="Clear search">
            <X size={13} strokeWidth={1.9} />
          </button>
        ) : null}
      </div>

      <div className="compact-thread-scroll" aria-label="Conversations">
        {groups.map((group) => (
          <section className="workspace-group" key={group.label}>
            <div className="workspace-group-header">
              <span title={group.label}>{group.label}</span>
              <button type="button" onClick={onNew} title={`New thread in ${group.label}`} aria-label={`New thread in ${group.label}`}>
                <Plus size={15} strokeWidth={1.7} />
              </button>
            </div>

            <div className="workspace-thread-list">
              {group.threads.map((thread) => {
                const active = thread.id === activeId;
                const running = thread.status === "running";
                const time = relativeTime(thread.updatedAt);
                return (
                  <button
                    key={thread.id}
                    className={`compact-thread-row ${active ? "active" : ""}`}
                    onClick={() => onOpen(thread.id)}
                    type="button"
                    title={`${thread.title || "New conversation"}${time ? ` · ${time}` : ""}`}
                    aria-current={active ? "page" : undefined}
                  >
                    <span className={`compact-thread-dot ${running ? "running" : ""}`} aria-hidden="true" />
                    <span className="compact-thread-title">{thread.title || "New conversation"}</span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}

        {!groups.length ? (
          <div className="compact-sidebar-empty">
            <span>{query ? "No matching conversations" : "No conversations yet"}</span>
            <button type="button" onClick={query ? () => setQuery("") : onNew}>
              {query ? "Clear search" : "New thread"}
            </button>
          </div>
        ) : null}
      </div>

      <div className="compact-sidebar-footer">
        <button type="button" disabled title="Archive view is coming soon">
          <Archive size={14} strokeWidth={1.7} />
          <span>Archive</span>
        </button>
      </div>
    </aside>
  );
}
