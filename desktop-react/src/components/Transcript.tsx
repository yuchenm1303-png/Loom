import {
  ArrowUpRight,
  Bug,
  Check,
  ChevronRight,
  CircleAlert,
  Code2,
  FileDiff,
  Search,
  Sparkles,
  Terminal,
  Wrench,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { TranscriptItem } from "../types/loom";
import "./activity-flow.css";

interface TranscriptProps {
  items: TranscriptItem[];
  promptDisabled?: boolean;
  onPrompt?(prompt: string): void;
  onApproval(item: TranscriptItem, approved: boolean): void;
}

type TranscriptBlock =
  | { kind: "item"; item: TranscriptItem }
  | { kind: "activity"; items: TranscriptItem[] };

const starterPrompts = [
  {
    icon: Search,
    eyebrow: "Understand",
    title: "Inspect this project",
    copy: "Map the architecture and tell me what matters first.",
    prompt: "Inspect this project, map the architecture, and tell me what I should understand first.",
  },
  {
    icon: Code2,
    eyebrow: "Build",
    title: "Implement a feature",
    copy: "Turn a product idea into a focused code change.",
    prompt: "Help me implement a feature in this project. Start by identifying the smallest clean approach.",
  },
  {
    icon: Bug,
    eyebrow: "Debug",
    title: "Trace a problem",
    copy: "Follow the failure to its root cause before changing code.",
    prompt: "Investigate the current project for the problem I am seeing and trace it to the root cause before making changes.",
  },
  {
    icon: Zap,
    eyebrow: "Automate",
    title: "Run a workflow",
    copy: "Use tools and the workspace to complete a multi-step task.",
    prompt: "Use the available tools and workspace to complete a useful multi-step task for this project.",
  },
] as const;

function splitReasoning(text: string): { reasoning: string; answer: string } {
  const match = text.match(/<think>([\s\S]*?)<\/think>([\s\S]*)/i);
  if (!match) return { reasoning: "", answer: text };
  return { reasoning: match[1].trim(), answer: match[2].trimStart() };
}

function Disclosure({ label, children, openByDefault = false }: { label: string; children: ReactNode; openByDefault?: boolean }) {
  const [open, setOpen] = useState(openByDefault);
  return (
    <div className={`disclosure ${open ? "open" : ""}`}>
      <button className="disclosure-trigger" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <ChevronRight size={14} className="disclosure-chevron" />
        <span>{label}</span>
      </button>
      <div className="disclosure-grid">
        <div className="disclosure-inner">{children}</div>
      </div>
    </div>
  );
}

function isActivityItem(item: TranscriptItem): boolean {
  return item.type === "tool_call" || item.type === "process" || item.type === "file_edit";
}

function groupTranscript(items: TranscriptItem[]): TranscriptBlock[] {
  const blocks: TranscriptBlock[] = [];
  let activity: TranscriptItem[] = [];
  let activityTurn = "";

  const flush = () => {
    if (!activity.length) return;
    blocks.push({ kind: "activity", items: activity });
    activity = [];
    activityTurn = "";
  };

  for (const item of items) {
    if (!isActivityItem(item)) {
      flush();
      blocks.push({ kind: "item", item });
      continue;
    }

    const nextTurn = String(item.turnId ?? "");
    if (activity.length && activityTurn && nextTurn && nextTurn !== activityTurn) flush();
    if (!activity.length) activityTurn = nextTurn;
    activity.push(item);
  }

  flush();
  return blocks;
}

function processCommand(item: TranscriptItem): string {
  if (Array.isArray(item.argv)) return item.argv.map(String).join(" ");
  return String(item.command ?? item.toolName ?? "Process");
}

function itemStatus(item: TranscriptItem): string {
  if (item.type === "file_edit") return item.status || "changed";
  return item.status || "completed";
}

function statusLabel(status: string): string {
  if (status === "started") return "Running";
  if (status === "completed") return "Completed";
  if (status === "changed") return "Changed";
  if (status === "failed") return "Failed";
  if (status === "denied") return "Denied";
  if (status === "cancelled") return "Cancelled";
  if (status === "interrupted") return "Interrupted";
  return status;
}

function ActivityStatus({ status }: { status: string }) {
  const quiet = status === "completed" || status === "changed";
  const label = statusLabel(status);
  return (
    <span className={`task-flow-status ${status}`} title={label} aria-label={label}>
      <span className="task-flow-status-dot" />
      {!quiet ? <span>{label}</span> : null}
    </span>
  );
}

function diffStats(diff?: string): { added: number; removed: number } {
  if (!diff) return { added: 0, removed: 0 };
  let added = 0;
  let removed = 0;
  for (const line of diff.split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) added += 1;
    else if (line.startsWith("-") && !line.startsWith("---")) removed += 1;
  }
  return { added, removed };
}

function fileLabel(item: TranscriptItem): string {
  const paths = item.paths ?? [];
  if (!paths.length) return "workspace";
  if (paths.length === 1) return paths[0];
  if (paths.length === 2) return `${paths[0]}, ${paths[1]}`;
  return `${paths[0]}, ${paths[1]} +${paths.length - 2}`;
}

function activitySummary(items: TranscriptItem[]): string {
  const processes = items.filter((item) => item.type === "process").length;
  const edits = items.filter((item) => item.type === "file_edit").length;
  const tools = items.filter((item) => item.type === "tool_call").length;
  const running = items.some((item) => ["running", "started"].includes(itemStatus(item)));

  if (running) return "Working through the task";
  if (edits && processes && !tools) return "Edited files and ran commands";
  if (edits && tools && !processes) return "Edited files and used tools";
  if (processes && tools && !edits) return "Ran commands and used tools";
  if (edits && processes && tools) return "Worked through the task";
  if (processes) return processes === 1 ? "Ran a command" : `Ran ${processes} commands`;
  if (edits) return edits === 1 ? "Edited a file" : `Edited ${edits} files`;
  if (tools) return tools === 1 ? "Used a tool" : `Used ${tools} tools`;
  return "Task activity";
}

function activityDetail(item: TranscriptItem): string {
  if (item.type === "process") {
    const stdout = String(item.stdout ?? "");
    const stderr = String(item.stderr ?? "");
    return `${stdout}${stderr ? `${stdout ? "\n" : ""}${stderr}` : ""}`.trim();
  }
  if (item.type === "file_edit") return String(item.diff ?? "").trim();
  if (item.content) return String(item.content);
  if (item.stdout || item.stderr) return `${String(item.stdout ?? "")}${item.stderr ? `\n${String(item.stderr)}` : ""}`.trim();
  if (item.arguments !== undefined) {
    try {
      return JSON.stringify(item.arguments, null, 2);
    } catch {
      return String(item.arguments);
    }
  }
  return "";
}

function ActivityGlyph({ item, size = 13 }: { item: TranscriptItem; size?: number }) {
  if (item.type === "process") return <Terminal size={size} />;
  if (item.type === "file_edit") return <FileDiff size={size} />;
  return <Wrench size={size} />;
}

function ActivityRow({ item }: { item: TranscriptItem }) {
  const status = itemStatus(item);
  const stats = item.type === "file_edit" ? diffStats(item.diff) : null;

  return (
    <div className="task-flow-row">
      <span className="task-flow-row-icon"><ActivityGlyph item={item} /></span>
      <span className="task-flow-row-main">
        {item.type === "process" ? (
          <>
            <span className="task-flow-verb">Ran</span>
            <span className="task-flow-primary code">{processCommand(item)}</span>
          </>
        ) : item.type === "file_edit" ? (
          <>
            <span className="task-flow-verb">Edited</span>
            <span className="task-flow-primary task-flow-path">{fileLabel(item)}</span>
            {stats && (stats.added > 0 || stats.removed > 0) ? (
              <span className="task-flow-diffstat">
                <span className="task-flow-plus">+{stats.added}</span>
                <span className="task-flow-minus">-{stats.removed}</span>
              </span>
            ) : null}
          </>
        ) : (
          <>
            <span className="task-flow-verb">Used</span>
            <span className="task-flow-primary">{item.toolName || "Tool"}</span>
          </>
        )}
      </span>
      <ActivityStatus status={status} />
    </div>
  );
}

function ActivityDetail({ item }: { item: TranscriptItem }) {
  const detail = activityDetail(item);
  const title = item.type === "process" ? processCommand(item) : item.type === "file_edit" ? fileLabel(item) : item.toolName || "Tool";
  const kind = item.type === "process" ? "Command" : item.type === "file_edit" ? "Change" : "Tool";
  return (
    <div className="task-flow-detail">
      <div className="task-flow-detail-head">
        <ActivityGlyph item={item} size={12} />
        <span className="task-flow-detail-title">{title}</span>
        <span className="task-flow-detail-kind">{kind}</span>
      </div>
      {detail ? <pre>{detail}</pre> : <div className="task-flow-detail-empty">No additional output.</div>}
    </div>
  );
}

function ActivityFlow({ items }: { items: TranscriptItem[] }) {
  const hasDetails = items.some((item) => Boolean(activityDetail(item)));
  const compact = items.length === 1;
  return (
    <section className={`task-flow ${compact ? "task-flow-single" : ""}`} aria-label="Task activity">
      {!compact ? (
        <div className="task-flow-summary">
          <span className="task-flow-summary-icon"><Wrench size={13} /></span>
          <span className="task-flow-summary-copy">
            <strong>{activitySummary(items)}</strong>
            <span>{items.length} actions</span>
          </span>
        </div>
      ) : null}

      <div className="task-flow-list">
        {items.map((item) => <ActivityRow key={item.id} item={item} />)}
      </div>

      {hasDetails ? (
        <div className="task-flow-disclosure">
          <Disclosure label="Details" openByDefault={items.some((item) => ["running", "started"].includes(itemStatus(item)))}>
            <div className="task-flow-details">
              {items.map((item) => <ActivityDetail key={item.id} item={item} />)}
            </div>
          </Disclosure>
        </div>
      ) : null}
    </section>
  );
}

function StreamingIndicator() {
  return (
    <div className="streaming-placeholder" role="status">
      <span className="streaming-spark"><Sparkles size={13} /></span>
      <span>Loom is working</span>
      <span className="streaming-dots" aria-hidden="true"><i /><i /><i /></span>
    </div>
  );
}

function ItemView({ item, onApproval }: { item: TranscriptItem; onApproval(item: TranscriptItem, approved: boolean): void }) {
  if (item.type === "user_message") return <div className="user-message">{item.text}</div>;
  if (item.type === "assistant_message") {
    const { reasoning, answer } = splitReasoning(item.text ?? "");
    return (
      <div className="assistant-message">
        {reasoning ? <Disclosure label="Thought process"><div className="reasoning-copy">{reasoning}</div></Disclosure> : null}
        {answer ? <div className="assistant-copy">{answer}</div> : <StreamingIndicator />}
      </div>
    );
  }
  if (item.type === "approval") return (
    <div className="approval-card">
      <div className="approval-icon"><CircleAlert size={16} /></div>
      <div className="approval-main">
        <div className="approval-title">Permission required</div>
        <div className="approval-copy">{item.toolName || "Tool"}{item.reason ? ` · ${item.reason}` : ""}</div>
      </div>
      <div className="approval-actions">
        <button className="button secondary" onClick={() => onApproval(item, false)}>Deny</button>
        <button className="button primary" onClick={() => onApproval(item, true)}><Check size={14} /> Allow</button>
      </div>
    </div>
  );
  if (item.type === "error") return <div className="error-row"><span className="error-icon"><CircleAlert size={14} /></span><span>{item.error || "Turn failed"}</span></div>;
  return null;
}

function EmptyState({ disabled, onPrompt }: { disabled?: boolean; onPrompt?(prompt: string): void }) {
  return (
    <section className="empty-state">
      <div className="empty-hero" aria-hidden="true">
        <div className="empty-orbit orbit-one" />
        <div className="empty-orbit orbit-two" />
        <div className="empty-mark"><span>L</span></div>
        <span className="empty-spark spark-one" />
        <span className="empty-spark spark-two" />
      </div>
      <div className="empty-kicker"><Sparkles size={12} /> Loom workspace</div>
      <h1>What are we working on?</h1>
      <p>Inspect a codebase, make a change, debug a failure, or hand Loom a multi-step task.</p>

      <div className="starter-grid">
        {starterPrompts.map(({ icon: Icon, eyebrow, title, copy, prompt }) => (
          <button
            className="starter-card"
            type="button"
            key={title}
            disabled={disabled}
            onClick={() => onPrompt?.(prompt)}
          >
            <span className="starter-icon"><Icon size={15} /></span>
            <span className="starter-content">
              <span className="starter-eyebrow">{eyebrow}</span>
              <strong>{title}</strong>
              <span>{copy}</span>
            </span>
            <ArrowUpRight className="starter-arrow" size={14} />
          </button>
        ))}
      </div>
    </section>
  );
}

export function Transcript({ items, promptDisabled, onPrompt, onApproval }: TranscriptProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const previousCount = useRef(0);
  const blocks = groupTranscript(items);

  useEffect(() => {
    if (items.length > previousCount.current) {
      const scroller = scrollRef.current;
      if (scroller) {
        requestAnimationFrame(() => {
          scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
        });
      }
    }
    previousCount.current = items.length;
  }, [items.length]);

  return (
    <div className="transcript-scroll" ref={scrollRef}>
      <div className="chat-ambient" aria-hidden="true">
        <span className="ambient-glow glow-one" />
        <span className="ambient-glow glow-two" />
        <span className="ambient-grid" />
      </div>
      <main className="transcript" aria-live="polite">
        {!items.length ? (
          <EmptyState disabled={promptDisabled} onPrompt={onPrompt} />
        ) : blocks.map((block, index) => (
          block.kind === "activity" ? (
            <div className="transcript-entry entry-activity" key={`activity-${block.items[0]?.id ?? index}`}>
              <ActivityFlow items={block.items} />
            </div>
          ) : (
            <div className={`transcript-entry entry-${block.item.type}`} key={block.item.id}>
              <ItemView item={block.item} onApproval={onApproval} />
            </div>
          )
        ))}
      </main>
    </div>
  );
}
