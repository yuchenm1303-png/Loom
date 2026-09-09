import {
  ArrowUpRight,
  BrainCircuit,
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
import { splitInlineStickerText } from "../chatStickers";
import type { TranscriptItem } from "../types/loom";
import "./activity-flow.css";
import "./stickers.css";

interface TranscriptProps {
  items: TranscriptItem[];
  running?: boolean;
  promptDisabled?: boolean;
  onPrompt?(prompt: string): void;
  onApproval(item: TranscriptItem, approved: boolean): void;
}

type TranscriptBlock =
  | { kind: "item"; item: TranscriptItem }
  | { kind: "activity"; items: TranscriptItem[] };

type ReasoningState = "none" | "streaming" | "closed";

interface ReasoningSplit {
  reasoning: string;
  answer: string;
  state: ReasoningState;
}

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

function splitReasoning(text: string): ReasoningSplit {
  const raw = String(text ?? "");
  const lower = raw.toLowerCase();
  const openTag = "<think>";
  const closeTag = "</think>";
  const openIndex = lower.indexOf(openTag);

  if (openIndex >= 0) {
    const reasoningStart = openIndex + openTag.length;
    const closeIndex = lower.indexOf(closeTag, reasoningStart);
    const prefix = raw.slice(0, openIndex).trim();

    if (closeIndex < 0) {
      return {
        reasoning: raw.slice(reasoningStart).trimStart(),
        answer: "",
        state: "streaming",
      };
    }

    const suffix = raw.slice(closeIndex + closeTag.length).trimStart();
    return {
      reasoning: raw.slice(reasoningStart, closeIndex).trim(),
      answer: [prefix, suffix].filter(Boolean).join(prefix && suffix ? "\n" : ""),
      state: "closed",
    };
  }

  const trimmedStart = raw.trimStart().toLowerCase();
  if (trimmedStart && openTag.startsWith(trimmedStart) && trimmedStart.startsWith("<")) {
    return { reasoning: "", answer: "", state: "streaming" };
  }

  return { reasoning: "", answer: raw, state: "none" };
}

function AssistantCopy({ text }: { text: string }) {
  const segments = splitInlineStickerText(text);
  const hasStickers = segments.some((segment) => segment.kind === "sticker");
  return (
    <div className={`assistant-copy ${hasStickers ? "has-inline-stickers" : ""}`}>
      {segments.map((segment, index) => segment.kind === "text" ? (
        <span key={`text-${index}`}>{segment.text}</span>
      ) : (
        <img
          key={`sticker-${segment.asset.key}-${index}`}
          className="assistant-inline-sticker"
          src={segment.asset.url}
          alt={segment.asset.alt}
          title={segment.asset.alt}
          width={96}
          height={96}
          draggable={false}
        />
      ))}
    </div>
  );
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

function LiveReasoning({ reasoning }: { reasoning: string }) {
  const [open, setOpen] = useState(false);
  const hasReasoning = Boolean(reasoning.trim());

  return (
    <div className={`live-reasoning ${open ? "open" : ""}`}>
      <button
        type="button"
        className="live-reasoning-trigger"
        onClick={() => hasReasoning && setOpen((value) => !value)}
        aria-expanded={hasReasoning ? open : undefined}
        disabled={!hasReasoning}
      >
        <span className="thinking-symbol" aria-hidden="true"><BrainCircuit size={15} /></span>
        <span className="thinking-shimmer">正在思考…</span>
        {hasReasoning ? <ChevronRight size={13} className="live-reasoning-chevron" aria-hidden="true" /> : null}
      </button>

      {hasReasoning ? (
        <div className="live-reasoning-grid">
          <div className="live-reasoning-inner">
            <div className="live-reasoning-copy">{reasoning}</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PendingThinking() {
  return (
    <div className="inline-thinking" role="status" aria-live="polite">
      <span className="thinking-symbol" aria-hidden="true"><BrainCircuit size={15} /></span>
      <span className="thinking-shimmer">正在思考…</span>
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
  const [open, setOpen] = useState(false);
  const status = itemStatus(item);
  const stats = item.type === "file_edit" ? diffStats(item.diff) : null;
  const detail = activityDetail(item);
  const expandable = Boolean(detail);

  return (
    <div className={`task-flow-row-wrap ${open ? "is-open" : ""}`}>
      <button
        type="button"
        className={`task-flow-row ${expandable ? "is-expandable" : "no-detail"}`}
        onClick={() => expandable && setOpen((value) => !value)}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        title={expandable ? (open ? "Collapse details" : "Expand details") : undefined}
      >
        <span className="task-flow-chevron" aria-hidden="true"><ChevronRight size={12} /></span>
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
      </button>

      {expandable ? (
        <div className={`task-flow-inline-detail-grid ${open ? "open" : ""}`}>
          <div className="task-flow-inline-detail-inner">
            <div className="task-flow-inline-detail">
              <pre>{detail}</pre>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ActivityFlow({ items }: { items: TranscriptItem[] }) {
  return (
    <section className="task-flow" aria-label="Task activity">
      <div className="task-flow-list">
        {items.map((item) => <ActivityRow key={item.id} item={item} />)}
      </div>
    </section>
  );
}

function ItemView({ item, onApproval }: { item: TranscriptItem; onApproval(item: TranscriptItem, approved: boolean): void }) {
  if (item.type === "user_message") return <div className="user-message">{item.text}</div>;
  if (item.type === "assistant_message") {
    const parsed = splitReasoning(item.text ?? "");

    if (parsed.state === "streaming") {
      return <div className="assistant-message"><LiveReasoning reasoning={parsed.reasoning} /></div>;
    }

    if (!parsed.reasoning && !parsed.answer.trim()) return null;

    return (
      <div className="assistant-message">
        {parsed.reasoning ? <Disclosure label="Thought process"><div className="reasoning-copy">{parsed.reasoning}</div></Disclosure> : null}
        {parsed.answer.trim() ? <AssistantCopy text={parsed.answer} /> : null}
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

export function Transcript({ items, running, promptDisabled, onPrompt, onApproval }: TranscriptProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const previousCount = useRef(0);
  const blocks = groupTranscript(items);
  let lastUserIndex = -1;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].type === "user_message") {
      lastUserIndex = index;
      break;
    }
  }
  const currentTurnItems = lastUserIndex >= 0 ? items.slice(lastUserIndex + 1) : items;
  const latestAssistant = [...currentTurnItems].reverse().find((item) => item.type === "assistant_message");
  const latestAssistantState = latestAssistant ? splitReasoning(latestAssistant.text ?? "") : null;
  const showPendingThinking = Boolean(
    running &&
    latestAssistantState?.state !== "streaming" &&
    !latestAssistantState?.answer.trim(),
  );

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
        {showPendingThinking ? <PendingThinking /> : null}
      </main>
    </div>
  );
}
