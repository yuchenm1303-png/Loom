import { Archive, MessageSquarePlus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { ThreadRecord } from "../types/loom";

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
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return threads;
    return threads.filter((thread) => `${thread.title} ${thread.workspace}`.toLowerCase().includes(needle));
  }, [query, threads]);

  return (
    <aside className="sidebar">
      <div className="brand-row">
        <div className="brand-mark">L</div>
        <div>
          <strong>Loom</strong>
          <span>Agent workspace</span>
        </div>
      </div>

      <button className="new-thread" onClick={onNew}>
        <MessageSquarePlus size={16} />
        New thread
      </button>

      <label className="search-box">
        <Search size={14} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search conversations" />
      </label>

      <div className="sidebar-section-label">Conversations</div>
      <div className="thread-list">
        {filtered.map((thread) => (
          <button
            key={thread.id}
            className={`thread-row ${thread.id === activeId ? "active" : ""}`}
            onClick={() => onOpen(thread.id)}
          >
            <span className="thread-title">{thread.title || "New conversation"}</span>
            <span className="thread-meta">
              {thread.status === "running" ? <span className="status-dot live" /> : null}
              <span>{relativeTime(thread.updatedAt)}</span>
            </span>
          </button>
        ))}
        {!filtered.length ? <div className="sidebar-empty">No conversations</div> : null}
      </div>

      <button className="sidebar-footer-button" disabled>
        <Archive size={15} />
        Archive
      </button>
    </aside>
  );
}
