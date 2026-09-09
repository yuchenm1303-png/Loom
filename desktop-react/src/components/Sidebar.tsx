import { Archive, MessageSquarePlus, Search, X } from "lucide-react";
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

export function Sidebar({ threads, activeId, onOpen, onNew }: SidebarProps) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const isMac = useMemo(
    () => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform),
    [],
  );
  const shortcutMod = isMac ? "⌘" : "Ctrl";

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return threads;
    return threads.filter((thread) => `${thread.title} ${thread.workspace}`.toLowerCase().includes(needle));
  }, [query, threads]);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();

      if (key === "k") {
        event.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
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

  const clearSearch = () => {
    setQuery("");
    searchRef.current?.focus();
  };

  return (
    <aside className="sidebar">
      <div className="brand-row">
        <div className="brand-mark" aria-hidden="true">L</div>
        <div className="brand-copy">
          <strong>Loom</strong>
          <span>Agent workspace</span>
        </div>
      </div>

      <button className="new-thread" onClick={onNew} type="button">
        <span className="new-thread-icon" aria-hidden="true">
          <MessageSquarePlus size={16} strokeWidth={1.8} />
        </span>
        <span className="new-thread-copy">New thread</span>
        <kbd>{shortcutMod} N</kbd>
      </button>

      <div className={`search-box ${query ? "has-query" : ""}`}>
        <Search className="search-icon" size={14} strokeWidth={1.8} aria-hidden="true" />
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && query) {
              event.preventDefault();
              clearSearch();
            }
          }}
          placeholder="Search conversations"
          aria-label="Search conversations"
        />
        {query ? (
          <button className="search-clear" onClick={clearSearch} type="button" aria-label="Clear conversation search">
            <X size={13} strokeWidth={2} />
          </button>
        ) : (
          <kbd className="search-shortcut">{shortcutMod} K</kbd>
        )}
      </div>

      <div className="sidebar-section-label">
        <span>Conversations</span>
        <span className="section-count">{filtered.length}</span>
      </div>

      <div className="thread-list" aria-label="Conversations">
        {filtered.map((thread) => {
          const active = thread.id === activeId;
          const running = thread.status === "running";
          return (
            <button
              key={thread.id}
              className={`thread-row ${active ? "active" : ""}`}
              onClick={() => onOpen(thread.id)}
              type="button"
              title={thread.title || "New conversation"}
              aria-current={active ? "page" : undefined}
              data-running={running ? "true" : undefined}
            >
              <span className="thread-title">{thread.title || "New conversation"}</span>
              <span className="thread-meta">
                {running ? <span className="status-dot live" aria-label="Running" /> : null}
                <span>{relativeTime(thread.updatedAt)}</span>
              </span>
            </button>
          );
        })}

        {!filtered.length ? (
          <div className="sidebar-empty">
            <span className="sidebar-empty-icon" aria-hidden="true">
              <Search size={14} strokeWidth={1.7} />
            </span>
            <strong>{query ? "No matching conversations" : "No conversations yet"}</strong>
            <span>{query ? "Try a different title or workspace." : "Create a thread to start working with Loom."}</span>
            {query ? (
              <button type="button" onClick={clearSearch}>
                Clear search
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="sidebar-footer">
        <button className="sidebar-footer-button" disabled type="button" title="Archive view is coming soon">
          <span className="sidebar-footer-icon" aria-hidden="true">
            <Archive size={15} strokeWidth={1.7} />
          </span>
          <span>Archive</span>
          <span className="footer-badge">Soon</span>
        </button>
      </div>
    </aside>
  );
}
