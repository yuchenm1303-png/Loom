import {
  Activity,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  FileDiff,
  Files,
  PanelRightClose,
  Terminal,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { TranscriptItem } from "../types/loom";
import "./Inspector.css";

interface InspectorProps {
  items: TranscriptItem[];
  onClose(): void;
}

type Tab = "activity" | "changes" | "terminal";
type IconComponent = typeof Activity;

const tabs: Array<{ id: Tab; label: string; icon: IconComponent }> = [
  { id: "activity", label: "Activity", icon: Activity },
  { id: "changes", label: "Changes", icon: Files },
  { id: "terminal", label: "Shell", icon: Terminal },
];

const emptyCopy: Record<Tab, { title: string; body: string; chips: string[]; icon: IconComponent }> = {
  activity: {
    title: "Runtime is ready",
    body: "Tool calls, approvals and execution events will appear here as Loom works.",
    chips: ["Tools", "Approvals", "Errors"],
    icon: Activity,
  },
  changes: {
    title: "No workspace changes",
    body: "File edits and diffs will collect here without interrupting the conversation.",
    chips: ["Files", "Diffs", "Paths"],
    icon: FileDiff,
  },
  terminal: {
    title: "Shell is quiet",
    body: "Commands and process output will appear here when Loom uses the terminal.",
    chips: ["Commands", "stdout", "stderr"],
    icon: Terminal,
  },
};

function statusOf(item: TranscriptItem): string {
  if (item.type === "error") return "failed";
  if (item.type === "approval" && !item.status) return "waiting";
  return String(item.status || "completed").toLowerCase();
}

function statusLabel(status: string): string {
  if (status === "started") return "Running";
  if (status === "waiting_approval" || status === "waiting") return "Waiting";
  return status.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isRunningStatus(status: string): boolean {
  return ["running", "started", "waiting", "waiting_approval", "pending"].includes(status);
}

function timeLabel(item: TranscriptItem): string {
  const value = item.updatedAt || item.createdAt;
  if (typeof value !== "string") return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function commandOf(item: TranscriptItem): string {
  if (Array.isArray(item.argv)) return item.argv.map(String).join(" ");
  return typeof item.command === "string" ? item.command : "";
}

function titleOf(item: TranscriptItem): string {
  if (item.toolName) return item.toolName;
  if (item.type === "file_edit") {
    const paths = item.paths ?? [];
    if (paths.length === 1) return paths[0];
    if (paths.length > 1) return `${paths.length} files changed`;
    return "Workspace change";
  }
  if (item.type === "process") return commandOf(item) || "Shell command";
  if (item.type === "approval") return "Permission request";
  if (item.type === "error") return "Runtime error";
  return item.type.replaceAll("_", " ");
}

function detailOf(item: TranscriptItem): string {
  if (item.type === "file_edit") return item.diff || (item.paths ?? []).join("\n");
  if (item.type === "process") {
    const command = commandOf(item);
    const output = `${item.stdout ?? ""}${item.stderr ? `${item.stdout ? "\n" : ""}${item.stderr}` : ""}`;
    return [command ? `$ ${command}` : "", output].filter(Boolean).join("\n\n");
  }
  if (item.type === "approval") return item.reason || "Loom is waiting for permission to continue.";
  if (item.type === "error") return item.error || "The runtime reported an error.";
  if (item.content) return item.content;
  if (item.stdout || item.stderr) return `${item.stdout ?? ""}${item.stderr ? `${item.stdout ? "\n" : ""}${item.stderr}` : ""}`;
  if (item.arguments !== undefined) {
    try {
      return JSON.stringify(item.arguments, null, 2);
    } catch {
      return String(item.arguments);
    }
  }
  return "";
}

function iconOf(item: TranscriptItem): IconComponent {
  if (item.type === "file_edit") return FileDiff;
  if (item.type === "process") return Terminal;
  if (item.type === "approval" || item.type === "error") return CircleAlert;
  return Wrench;
}

function EmptyState({ tab }: { tab: Tab }) {
  const copy = emptyCopy[tab];
  const EmptyIcon = copy.icon;
  return (
    <div className="runtime-empty">
      <div className="runtime-empty-visual" aria-hidden="true">
        <span className="runtime-orbit runtime-orbit-one" />
        <span className="runtime-orbit runtime-orbit-two" />
        <span className="runtime-orbit-dot" />
        <span className="runtime-empty-icon"><EmptyIcon size={20} strokeWidth={1.75} /></span>
      </div>
      <strong>{copy.title}</strong>
      <p>{copy.body}</p>
      <div className="runtime-empty-chips">
        {copy.chips.map((chip) => <span key={chip}>{chip}</span>)}
      </div>
    </div>
  );
}

function RuntimeEvent({ item, expanded, onToggle }: { item: TranscriptItem; expanded: boolean; onToggle(): void }) {
  const Icon = iconOf(item);
  const status = statusOf(item);
  const detail = detailOf(item);
  const timestamp = timeLabel(item);
  const running = isRunningStatus(status);
  const success = ["completed", "success", "succeeded"].includes(status);
  const failed = ["failed", "error", "denied"].includes(status);

  return (
    <div className={`runtime-event ${running ? "is-running" : ""} ${failed ? "is-failed" : ""}`}>
      <button className="runtime-event-main" onClick={onToggle} aria-expanded={expanded} disabled={!detail}>
        <span className="runtime-event-icon" aria-hidden="true">
          {success ? <CheckCircle2 size={15} strokeWidth={1.8} /> : <Icon size={15} strokeWidth={1.8} />}
        </span>
        <span className="runtime-event-copy">
          <span className="runtime-event-title" title={titleOf(item)}>{titleOf(item)}</span>
          <span className="runtime-event-subtitle">
            <span className={`runtime-status-dot status-${status}`} />
            <span>{statusLabel(status)}</span>
            {timestamp ? <><span className="runtime-meta-separator" /> <span>{timestamp}</span></> : null}
          </span>
        </span>
        {detail ? <ChevronDown size={14} className={`runtime-event-chevron ${expanded ? "open" : ""}`} /> : null}
      </button>
      {detail ? (
        <div className={`runtime-event-detail ${expanded ? "open" : ""}`}>
          <div className="runtime-event-detail-inner">
            <pre>{detail}</pre>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function Inspector({ items, onClose }: InspectorProps) {
  const [tab, setTab] = useState<Tab>("activity");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toolItems = useMemo(
    () => items.filter((item) => ["tool_call", "process", "approval", "error"].includes(item.type)),
    [items],
  );
  const changes = useMemo(() => items.filter((item) => item.type === "file_edit"), [items]);
  const processes = useMemo(() => items.filter((item) => item.type === "process"), [items]);
  const visible = tab === "changes" ? changes : tab === "terminal" ? processes : toolItems;
  const counts: Record<Tab, number> = { activity: toolItems.length, changes: changes.length, terminal: processes.length };
  const tabIndex = tabs.findIndex((entry) => entry.id === tab);
  const busy = items.some((item) => isRunningStatus(statusOf(item)));
  const failed = items.some((item) => ["failed", "error"].includes(statusOf(item)));

  return (
    <aside className="inspector runtime-inspector">
      <header className="runtime-header">
        <div className="runtime-heading">
          <div className="runtime-title-row">
            <strong>Runtime</strong>
            <span className={`runtime-health ${busy ? "live" : failed ? "warning" : "idle"}`}>
              <span className="runtime-health-dot" />
              {busy ? "Live" : failed ? "Attention" : "Idle"}
            </span>
          </div>
          <span className="runtime-caption">Execution inspector</span>
        </div>
        <button className="runtime-close" onClick={onClose} title="Close runtime inspector" aria-label="Close runtime inspector">
          <PanelRightClose size={16} strokeWidth={1.8} />
        </button>
      </header>

      <div className="runtime-tabs" role="tablist" aria-label="Runtime views">
        <span className="runtime-tab-glider" style={{ transform: `translateX(${tabIndex * 100}%)` }} aria-hidden="true" />
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={`runtime-tab ${tab === id ? "active" : ""}`}
            onClick={() => {
              setTab(id);
              setExpandedId(null);
            }}
          >
            <Icon size={14} strokeWidth={1.75} />
            <span>{label}</span>
            {counts[id] > 0 ? <span className="runtime-tab-count">{counts[id]}</span> : null}
          </button>
        ))}
      </div>

      <div className="runtime-body">
        <div className="runtime-section-bar">
          <span>{tab === "activity" ? "Execution stream" : tab === "changes" ? "Workspace edits" : "Process output"}</span>
          <span>{visible.length ? `${visible.length} ${visible.length === 1 ? "event" : "events"}` : "Waiting"}</span>
        </div>

        <div className="runtime-scroll">
          <div className="runtime-pane" key={tab}>
            {!visible.length ? (
              <EmptyState tab={tab} />
            ) : (
              <div className="runtime-timeline">
                {visible.map((item) => (
                  <RuntimeEvent
                    item={item}
                    key={item.id}
                    expanded={expandedId === item.id}
                    onToggle={() => setExpandedId((current) => current === item.id ? null : item.id)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="runtime-footer">
          <span className={`runtime-stream-mark ${busy ? "live" : ""}`} aria-hidden="true"><i /><i /><i /></span>
          <span>{busy ? "Receiving runtime events" : "Runtime event stream ready"}</span>
        </div>
      </div>
    </aside>
  );
}
