import {
  Activity,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Download,
  FileDiff,
  Files,
  MousePointer2,
  PanelRightClose,
  Terminal,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { TranscriptItem } from "../types/loom";
import { ReviewWorkspace } from "./ReviewWorkspace";
import "./Inspector.css";
import "./InspectorMark.css";
import "./ComputerLogExport.css";

interface InspectorProps {
  items: TranscriptItem[];
  onClose(): void;
}

type Tab = "activity" | "computer" | "changes" | "terminal";
type IconComponent = typeof Activity;

const tabs: Array<{ id: Tab; label: string; icon: IconComponent }> = [
  { id: "activity", label: "Activity", icon: Activity },
  { id: "computer", label: "Computer", icon: MousePointer2 },
  { id: "changes", label: "Changes", icon: Files },
  { id: "terminal", label: "Shell", icon: Terminal },
];

const emptyCopy: Record<Tab, { title: string; body: string; chips: string[] }> = {
  activity: {
    title: "Runtime is ready",
    body: "Tool calls, approvals and execution events will appear here as Loom works.",
    chips: ["Tools", "Approvals", "Errors"],
  },
  computer: {
    title: "No Computer Use trace yet",
    body: "Desktop observations, actions, visual grounding steps, resolved coordinates and trace paths will appear here.",
    chips: ["Observe", "Action", "Verify"],
  },
  changes: {
    title: "No workspace changes",
    body: "File edits and diffs will collect here without interrupting the conversation.",
    chips: ["Files", "Diffs", "Paths"],
  },
  terminal: {
    title: "Shell is quiet",
    body: "Commands and process output will appear here when Loom uses the terminal.",
    chips: ["Commands", "stdout", "stderr"],
  },
};

const emptyIcons: Array<{ id: Tab; icon: IconComponent }> = [
  { id: "activity", icon: Activity },
  { id: "computer", icon: MousePointer2 },
  { id: "changes", icon: FileDiff },
  { id: "terminal", icon: Terminal },
];

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

function isComputerItem(item: TranscriptItem): boolean {
  return String(item.toolName || "").startsWith("computer_");
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function recordValue(value: unknown, key: string): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return (value as Record<string, unknown>)[key];
}

function humanBytes(value: unknown): string {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function computerDetailOf(item: TranscriptItem): string {
  const result = item.result;
  const trace = recordValue(result, "trace");
  const tracePath = recordValue(trace, "trace_path");
  const geometry = recordValue(result, "geometry");
  const verification = recordValue(result, "verification");
  const parts = [
    item.content ? `Result: ${item.content}` : "",
    tracePath ? `Trace file: ${String(tracePath)}` : "",
    geometry ? `Geometry:\n${safeJson(geometry)}` : "",
    verification ? `Verification:\n${safeJson(verification)}` : "",
    result !== undefined ? `Raw result:\n${safeJson(result)}` : "",
    item.arguments !== undefined ? `Arguments:\n${safeJson(item.arguments)}` : "",
  ];
  return parts.filter(Boolean).join("\n\n");
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

function hasDetail(item: TranscriptItem): boolean {
  if (isComputerItem(item)) {
    return Boolean(item.content || item.result !== undefined || item.arguments !== undefined);
  }
  if (item.type === "file_edit") return Boolean(item.diff || (item.paths ?? []).length);
  if (item.type === "process") return Boolean(commandOf(item) || item.stdout || item.stderr);
  if (item.type === "approval" || item.type === "error") return true;
  return Boolean(
    item.content
    || item.result !== undefined
    || item.stdout
    || item.stderr
    || item.arguments !== undefined,
  );
}

function detailOf(item: TranscriptItem): string {
  if (isComputerItem(item)) return computerDetailOf(item);
  if (item.type === "file_edit") return item.diff || (item.paths ?? []).join("\n");
  if (item.type === "process") {
    const command = commandOf(item);
    const output = `${item.stdout ?? ""}${item.stderr ? `${item.stdout ? "\n" : ""}${item.stderr}` : ""}`;
    return [command ? `$ ${command}` : "", output].filter(Boolean).join("\n\n");
  }
  if (item.type === "approval") return item.reason || "Loom is waiting for permission to continue.";
  if (item.type === "error") return item.error || "The runtime reported an error.";
  if (item.content && item.result !== undefined) return `${item.content}\n\n${safeJson(item.result)}`;
  if (item.content) return item.content;
  if (item.result !== undefined) return safeJson(item.result);
  if (item.stdout || item.stderr) return `${item.stdout ?? ""}${item.stderr ? `${item.stdout ? "\n" : ""}${item.stderr}` : ""}`;
  if (item.arguments !== undefined) return safeJson(item.arguments);
  return "";
}

function iconOf(item: TranscriptItem): IconComponent {
  if (isComputerItem(item)) return MousePointer2;
  if (item.type === "file_edit") return FileDiff;
  if (item.type === "process") return Terminal;
  if (item.type === "approval" || item.type === "error") return CircleAlert;
  return Wrench;
}

function EmptyState({ tab }: { tab: Tab }) {
  const copy = emptyCopy[tab];
  return (
    <div className="runtime-empty">
      <div className="runtime-empty-visual" aria-hidden="true">
        <span className="runtime-orbit runtime-orbit-one" />
        <span className="runtime-orbit runtime-orbit-two" />
        <span className="runtime-orbit-dot" />
        <span className="runtime-empty-icon">
          {emptyIcons.map(({ id, icon: Icon }) => (
            <Icon
              key={id}
              className={`runtime-empty-glyph ${tab === id ? "active" : ""}`}
              size={20}
              strokeWidth={1.75}
            />
          ))}
        </span>
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
  const expandable = hasDetail(item);
  const detail = expanded && expandable ? detailOf(item) : "";
  const timestamp = timeLabel(item);
  const running = isRunningStatus(status);
  const success = ["completed", "success", "succeeded"].includes(status);
  const failed = ["failed", "error", "denied"].includes(status);

  return (
    <div className={`runtime-event ${running ? "is-running" : ""} ${failed ? "is-failed" : ""}`}>
      <button className="runtime-event-main" onClick={onToggle} aria-expanded={expanded} disabled={!expandable}>
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
        {expandable ? <ChevronDown size={14} className={`runtime-event-chevron ${expanded ? "open" : ""}`} /> : null}
      </button>
      {expandable ? (
        <div className={`runtime-event-detail ${expanded ? "open" : ""}`}>
          <div className="runtime-event-detail-inner">
            {expanded ? <pre>{detail}</pre> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function sectionTitle(tab: Tab): string {
  if (tab === "activity") return "Execution stream";
  if (tab === "computer") return "Computer Use trace";
  if (tab === "changes") return "Workspace edits";
  return "Process output";
}

export function Inspector({ items, onClose }: InspectorProps) {
  const [tab, setTab] = useState<Tab>("activity");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [exportingLogs, setExportingLogs] = useState(false);
  const [exportResult, setExportResult] = useState<Window["loom"] extends { exportComputerLogs(): Promise<infer T> } ? T | null : unknown>(null);
  const [exportError, setExportError] = useState("");

  const toolItems = useMemo(
    () => items.filter((item) => ["tool_call", "process", "approval", "error"].includes(item.type)),
    [items],
  );
  const computerItems = useMemo(() => items.filter(isComputerItem), [items]);
  const changes = useMemo(() => items.filter((item) => item.type === "file_edit"), [items]);
  const processes = useMemo(() => items.filter((item) => item.type === "process"), [items]);
  const visible = tab === "computer" ? computerItems : tab === "changes" ? changes : tab === "terminal" ? processes : toolItems;
  const counts: Record<Tab, number> = { activity: toolItems.length, computer: computerItems.length, changes: changes.length, terminal: processes.length };
  const tabIndex = tabs.findIndex((entry) => entry.id === tab);
  const busy = items.some((item) => isRunningStatus(statusOf(item)));
  const failed = items.some((item) => ["failed", "error"].includes(statusOf(item)));

  async function handleExportComputerLogs() {
    setExportingLogs(true);
    setExportError("");
    try {
      const result = await window.loom.exportComputerLogs();
      if (result.cancelled) return;
      setExportResult(result);
      if (result.archivePath) {
        await window.loom.revealPath(result.archivePath);
      }
    } catch (error) {
      setExportError(error instanceof Error ? error.message : String(error));
    } finally {
      setExportingLogs(false);
    }
  }

  const exportArchivePath = exportResult && typeof exportResult === "object" && "archivePath" in exportResult
    ? String(exportResult.archivePath || "")
    : "";
  const exportFileCount = exportResult && typeof exportResult === "object" && "fileCount" in exportResult
    ? Number(exportResult.fileCount || 0)
    : 0;
  const exportSize = exportResult && typeof exportResult === "object" && "sizeBytes" in exportResult
    ? humanBytes(exportResult.sizeBytes)
    : "";

  return (
    <>
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

        <div className="runtime-tabs" role="tablist" aria-label="Runtime views" style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}>
          <span
            className="runtime-tab-glider"
            style={{
              width: `calc((100% - 16px) / ${tabs.length})`,
              transform: `translateX(${tabIndex * 100}%)`,
            }}
            aria-hidden="true"
          />
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
            <span>{sectionTitle(tab)}</span>
            <span className="runtime-section-actions">
              {tab === "computer" ? (
                <button className="computer-log-export-button" onClick={handleExportComputerLogs} disabled={exportingLogs}>
                  <Download size={12} strokeWidth={1.8} />
                  <span>{exportingLogs ? "Exporting" : "Export logs"}</span>
                </button>
              ) : null}
              {tab === "changes" && changes.length ? (
                <button className="runtime-review-button" type="button" onClick={() => setReviewOpen(true)}>
                  <FileDiff size={12} strokeWidth={1.8} />
                  <span>Review</span>
                </button>
              ) : null}
              <span>{visible.length ? `${visible.length} ${visible.length === 1 ? "event" : "events"}` : "Waiting"}</span>
            </span>
          </div>

          <div className="runtime-scroll">
            <div className="runtime-pane">
              {tab === "computer" && (exportArchivePath || exportError) ? (
                <div className={`computer-log-export-note ${exportError ? "error" : "success"}`}>
                  <strong>{exportError ? "Export failed" : "Logs exported"}</strong>
                  <span>
                    {exportError || `${exportArchivePath}${exportFileCount ? ` · ${exportFileCount} files` : ""}${exportSize ? ` · ${exportSize}` : ""}`}
                  </span>
                </div>
              ) : null}
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
      <ReviewWorkspace items={changes} open={reviewOpen} onClose={() => setReviewOpen(false)} />
    </>
  );
}
