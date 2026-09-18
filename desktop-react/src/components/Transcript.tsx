import {
  ArrowUpRight,
  BrainCircuit,
  Bug,
  Check,
  ChevronRight,
  CircleAlert,
  Code2,
  Copy,
  FileDiff,
  Pencil,
  Search,
  Sparkles,
  Terminal,
  ThumbsDown,
  ThumbsUp,
  Wrench,
  Zap,
} from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { TranscriptItem } from "../types/loom";
import { MarkdownMessage } from "./MarkdownMessage";
import { TurnArtifactsPreview } from "./TurnArtifactsPreview";
import { UserMessageContent, parseUserMessageContent } from "./UserMessageContent";
import "./activity-flow.css";
import "./message-actions.css";
import "./task-flow-folding.css";
import "./turn-flow.css";

interface TranscriptProps {
  items: TranscriptItem[];
  running?: boolean;
  currentTurnId?: string | null;
  workspace?: string;
  promptDisabled?: boolean;
  onPrompt?(prompt: string): void;
  onApproval(item: TranscriptItem, approved: boolean): void;
}

type TranscriptBlock =
  | { kind: "item"; item: TranscriptItem }
  | { kind: "activity"; items: TranscriptItem[] };

type TurnBlock =
  | { kind: "turn"; id: string; items: TranscriptItem[] }
  | { kind: "loose"; item: TranscriptItem };

type ReasoningState = "none" | "streaming" | "closed";
type MessageFeedback = "up" | "down" | null;

interface ReasoningSplit {
  reasoning: string;
  answer: string;
  state: ReasoningState;
}

interface ActivitySummaryData {
  steps: number;
  failed: boolean;
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

function LiveReasoning({ reasoning, workspace }: { reasoning: string; workspace?: string }) {
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
            <div className="live-reasoning-copy">
              <MarkdownMessage content={reasoning} compact workspace={workspace} />
            </div>
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

function groupTurns(items: TranscriptItem[]): TurnBlock[] {
  const blocks: TurnBlock[] = [];

  for (const item of items) {
    const turnId = String(item.turnId ?? "");
    if (!turnId) {
      blocks.push({ kind: "loose", item });
      continue;
    }

    const last = blocks[blocks.length - 1];
    if (last?.kind === "turn" && last.id === turnId) {
      last.items.push(item);
    } else {
      blocks.push({ kind: "turn", id: turnId, items: [item] });
    }
  }

  return blocks;
}

function sameItemReferences(previous: TranscriptItem[] | undefined, next: TranscriptItem[]): boolean {
  if (!previous || previous.length !== next.length) return false;
  for (let index = 0; index < next.length; index += 1) {
    if (previous[index] !== next[index]) return false;
  }
  return true;
}

/**
 * groupTurns must inspect the canonical item stream, but completed turns should
 * not become new React subtrees just because the active turn received one more
 * tool event. Reuse the exact item-array reference for unchanged turns so the
 * memoized TurnView stays cold on long conversations.
 */
function useStableTurnBlocks(items: TranscriptItem[]): TurnBlock[] {
  const turnCacheRef = useRef<Map<string, TranscriptItem[]>>(new Map());

  return useMemo(() => {
    const grouped = groupTurns(items);
    const previous = turnCacheRef.current;
    const nextCache = new Map<string, TranscriptItem[]>();

    for (const block of grouped) {
      if (block.kind !== "turn") continue;
      const cached = previous.get(block.id);
      if (sameItemReferences(cached, block.items)) block.items = cached!;
      nextCache.set(block.id, block.items);
    }

    turnCacheRef.current = nextCache;
    return grouped;
  }, [items]);
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
  if (status === "started" || status === "running") return "Running";
  if (status === "completed") return "Completed";
  if (status === "changed") return "Changed";
  if (status === "failed") return "Failed";
  if (status === "denied") return "Denied";
  if (status === "cancelled") return "Cancelled";
  if (status === "interrupted") return "Interrupted";
  return status;
}

function isActiveActivityStatus(status: string): boolean {
  return status === "started" || status === "running" || status === "waiting" || status === "waiting_approval" || status === "pending";
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

function hasActivityDetail(item: TranscriptItem): boolean {
  if (item.type === "process") return Boolean(String(item.stdout ?? "").trim() || String(item.stderr ?? "").trim());
  if (item.type === "file_edit") return Boolean(String(item.diff ?? "").trim());
  if (String(item.content ?? "").trim()) return true;
  if (String(item.stdout ?? "").trim() || String(item.stderr ?? "").trim()) return true;
  return item.arguments !== undefined;
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

const ActivityRow = memo(function ActivityRow({ item }: { item: TranscriptItem }) {
  const [open, setOpen] = useState(false);
  const status = itemStatus(item);
  const stats = useMemo(() => item.type === "file_edit" ? diffStats(item.diff) : null, [item.diff, item.type]);
  const expandable = hasActivityDetail(item);
  const detail = useMemo(() => open ? activityDetail(item) : "", [item, open]);

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
              <span className="task-flow-verb">已运行</span>
              <span className="task-flow-primary code">{processCommand(item)}</span>
            </>
          ) : item.type === "file_edit" ? (
            <>
              <span className="task-flow-verb">已编辑</span>
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
              <span className="task-flow-verb">已使用</span>
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
});

function isFailureStatus(status: string): boolean {
  return status === "failed" || status === "denied" || status === "cancelled" || status === "interrupted";
}

function isRedundantToolWrapper(item: TranscriptItem, hasProcess: boolean, hasFileEdit: boolean): boolean {
  if (item.type !== "tool_call" || isFailureStatus(itemStatus(item))) return false;
  const name = String(item.toolName ?? "").trim().toLowerCase();

  if (hasProcess && /^(exec|execute|shell|run_command|run-command|command|powershell|bash|cmd)$/.test(name)) return true;
  if (
    hasFileEdit
    && /^(write_workspace_text|write_file|write-file|edit_file|edit-file|apply_patch|apply-patch|patch_file|patch-file|replace_text|replace-text)$/.test(name)
  ) return true;
  return false;
}

function compactActivityItems(items: TranscriptItem[]): TranscriptItem[] {
  let hasProcess = false;
  let hasFileEdit = false;
  for (const item of items) {
    if (item.type === "process") hasProcess = true;
    else if (item.type === "file_edit") hasFileEdit = true;
  }

  const compact = items.filter((item) => !isRedundantToolWrapper(item, hasProcess, hasFileEdit));
  return compact.length ? compact : items;
}

function activitySummary(items: TranscriptItem[]): ActivitySummaryData {
  const activityItems: TranscriptItem[] = [];
  let failed = false;

  for (const item of items) {
    if (isActivityItem(item)) activityItems.push(item);
    if (!failed && (item.type === "error" || isFailureStatus(itemStatus(item)))) failed = true;
  }

  return {
    steps: compactActivityItems(activityItems).length,
    failed,
  };
}

function activityGroupTitle(items: TranscriptItem[]): string {
  let hasProcess = false;
  let hasEdit = false;
  let hasTool = false;
  for (const item of items) {
    if (item.type === "process") hasProcess = true;
    else if (item.type === "file_edit") hasEdit = true;
    else if (item.type === "tool_call") hasTool = true;
  }

  if (hasEdit && hasProcess && hasTool) return "编辑了文件、运行了命令并使用了工具";
  if (hasEdit && hasProcess) return "编辑了文件并运行了命令";
  if (hasProcess && hasTool) return "运行了命令并使用了工具";
  if (hasEdit && hasTool) return "编辑了文件并使用了工具";
  if (hasProcess) return "运行了命令";
  if (hasEdit) return "编辑了文件";
  return "使用了工具";
}

function ActivityGroupIcon({ items }: { items: TranscriptItem[] }) {
  let hasEdit = false;
  for (const item of items) {
    if (item.type === "process") return <Terminal size={14} />;
    if (item.type === "file_edit") hasEdit = true;
  }
  if (hasEdit) return <FileDiff size={14} />;
  return <Wrench size={14} />;
}

function ActivityFlow({ items, keepOpen = false }: { items: TranscriptItem[]; keepOpen?: boolean }) {
  const compactItems = useMemo(() => compactActivityItems(items), [items]);
  const running = useMemo(
    () => compactItems.some((item) => isActiveActivityStatus(itemStatus(item))),
    [compactItems],
  );
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (keepOpen || running) setOpen(true);
  }, [keepOpen, running]);

  return (
    <section
      className={`task-flow task-flow-group ${open ? "is-open" : ""} ${running ? "is-running" : ""}`}
      aria-label="Task activity"
    >
      <button
        type="button"
        className="task-flow-group-header"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="task-flow-group-icon" aria-hidden="true"><ActivityGroupIcon items={compactItems} /></span>
        <span className="task-flow-group-title">{activityGroupTitle(compactItems)}</span>
        <ChevronRight size={13} className="task-flow-group-chevron" aria-hidden="true" />
      </button>

      <div className="task-flow-group-grid">
        <div className="task-flow-group-inner">
          <div className="task-flow-list">
            {compactItems.map((item) => <ActivityRow key={item.id} item={item} />)}
          </div>
        </div>
      </div>
    </section>
  );
}

function messageTimestamp(item: TranscriptItem): string {
  const value = item.createdAt || item.updatedAt;
  if (!value) return "";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(parsed));
}

async function copyMessageText(value: string): Promise<void> {
  if (!value) return;
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

function placeTextInComposer(value: string): boolean {
  const text = value.trim();
  if (!text) return false;
  const textarea = document.querySelector<HTMLTextAreaElement>(".composer textarea");
  if (!textarea || textarea.disabled) return false;

  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
  if (setter) setter.call(textarea, text);
  else textarea.value = text;
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.focus();
  requestAnimationFrame(() => {
    textarea.selectionStart = text.length;
    textarea.selectionEnd = text.length;
  });
  return true;
}

function MessageToolbar({
  kind,
  item,
  text,
  editable = false,
  disabled = false,
}: {
  kind: "user" | "assistant";
  item: TranscriptItem;
  text: string;
  editable?: boolean;
  disabled?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<MessageFeedback>(null);
  const time = messageTimestamp(item);
  const canCopy = Boolean(text.trim());

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    if (!canCopy) return;
    try {
      await copyMessageText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const edit = () => {
    if (!editable || disabled || !canCopy) return;
    if (!placeTextInComposer(text)) void copyMessageText(text);
  };

  return (
    <div className={`message-meta ${kind}-message-meta`}>
      {time ? <span className="message-time">{time}</span> : null}
      <span className="message-actions" aria-label={kind === "user" ? "User message actions" : "Assistant message actions"}>
        <button
          type="button"
          className={`message-action-button ${copied ? "is-copied" : ""}`}
          onClick={() => void copy()}
          disabled={!canCopy}
          title={copied ? "已复制" : "复制"}
          aria-label={copied ? "已复制" : "复制消息"}
        >
          {copied ? <Check size={14} strokeWidth={2.1} /> : <Copy size={14} strokeWidth={1.75} />}
        </button>

        {kind === "user" ? (
          <button
            type="button"
            className="message-action-button"
            onClick={edit}
            disabled={!editable || disabled || !canCopy}
            title="放到输入框里编辑"
            aria-label="编辑这条消息"
          >
            <Pencil size={14} strokeWidth={1.75} />
          </button>
        ) : (
          <>
            <button
              type="button"
              className={`message-action-button ${feedback === "up" ? "active" : ""}`}
              onClick={() => setFeedback((current) => (current === "up" ? null : "up"))}
              title="有帮助"
              aria-label="标记回复有帮助"
              aria-pressed={feedback === "up"}
            >
              <ThumbsUp size={14} strokeWidth={1.75} />
            </button>
            <button
              type="button"
              className={`message-action-button ${feedback === "down" ? "active" : ""}`}
              onClick={() => setFeedback((current) => (current === "down" ? null : "down"))}
              title="没帮助"
              aria-label="标记回复没帮助"
              aria-pressed={feedback === "down"}
            >
              <ThumbsDown size={14} strokeWidth={1.75} />
            </button>
          </>
        )}
      </span>
    </div>
  );
}

function ItemView({
  item,
  onApproval,
  promptDisabled,
  workspace,
}: {
  item: TranscriptItem;
  onApproval(item: TranscriptItem, approved: boolean): void;
  promptDisabled?: boolean;
  workspace?: string;
}) {
  if (item.type === "user_message") {
    const rawText = String(item.text ?? "");
    const parsed = parseUserMessageContent(rawText);
    return (
      <div className="message-shell user-message-shell" data-message-id={item.id}>
        <div className="user-message"><UserMessageContent parsed={parsed} /></div>
        <MessageToolbar kind="user" item={item} text={parsed.text} editable disabled={promptDisabled} />
      </div>
    );
  }

  if (item.type === "assistant_message") {
    const parsed = splitReasoning(item.text ?? "");

    if (parsed.state === "streaming") {
      return <div className="assistant-message"><LiveReasoning reasoning={parsed.reasoning} workspace={workspace} /></div>;
    }

    if (!parsed.reasoning && !parsed.answer.trim()) return null;

    const answer = parsed.answer.trim();
    return (
      <div className="message-shell assistant-message-shell" data-message-id={item.id}>
        <div className="assistant-message">
          {parsed.reasoning ? (
            <Disclosure label="Thought process">
              <div className="reasoning-copy">
                <MarkdownMessage content={parsed.reasoning} compact workspace={workspace} />
              </div>
            </Disclosure>
          ) : null}
          {answer ? <MarkdownMessage content={parsed.answer} workspace={workspace} /> : null}
        </div>
        <MessageToolbar kind="assistant" item={item} text={answer || parsed.reasoning} />
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

  if (item.type === "error") {
    return <div className="error-row"><span className="error-icon"><CircleAlert size={14} /></span><span>{item.error || "Turn failed"}</span></div>;
  }

  return null;
}

function Sequence({
  items,
  onApproval,
  keepActivityOpen = false,
  promptDisabled,
  workspace,
}: {
  items: TranscriptItem[];
  onApproval(item: TranscriptItem, approved: boolean): void;
  keepActivityOpen?: boolean;
  promptDisabled?: boolean;
  workspace?: string;
}) {
  const blocks = useMemo(() => groupTranscript(items), [items]);
  return (
    <>
      {blocks.map((block, index) => (
        block.kind === "activity" ? (
          <div className="transcript-entry entry-activity" key={`activity-${block.items[0]?.id ?? index}`}>
            <ActivityFlow items={block.items} keepOpen={keepActivityOpen} />
          </div>
        ) : (
          <div className={`transcript-entry entry-${block.item.type}`} key={block.item.id}>
            <ItemView item={block.item} onApproval={onApproval} promptDisabled={promptDisabled} workspace={workspace} />
          </div>
        )
      ))}
    </>
  );
}

function parseTimestamp(value: unknown): number | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function elapsedLabel(items: TranscriptItem[]): string {
  let earliest = Number.POSITIVE_INFINITY;
  let latest = Number.NEGATIVE_INFINITY;

  for (const item of items) {
    const start = parseTimestamp(item.createdAt);
    const end = parseTimestamp(item.updatedAt) ?? start;
    if (start !== null) earliest = Math.min(earliest, start);
    if (end !== null) latest = Math.max(latest, end);
  }

  if (!Number.isFinite(earliest) || !Number.isFinite(latest)) return "任务过程";
  const seconds = Math.max(1, Math.round((latest - earliest) / 1000));
  if (seconds < 60) return `用时 ${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) return `用时 ${minutes}m${remainingSeconds ? ` ${remainingSeconds}s` : ""}`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `用时 ${hours}h${remainingMinutes ? ` ${remainingMinutes}m` : ""}`;
}

function finalAssistantForTurn(items: TranscriptItem[]): TranscriptItem | null {
  const assistants = items.filter((item) => item.type === "assistant_message");
  if (!assistants.length) return null;

  for (let index = assistants.length - 1; index >= 0; index -= 1) {
    if (assistants[index].phase === "final_answer") return assistants[index];
  }

  // New app-server payloads explicitly classify every assistant item. Once that
  // semantic boundary exists, never promote commentary merely because it is the
  // last visible model message. The positional fallback is only for legacy
  // histories written before assistant phases were persisted.
  if (assistants.some((item) => Boolean(item.phase))) return null;

  let fallback: TranscriptItem | null = null;
  for (let index = assistants.length - 1; index >= 0; index -= 1) {
    const item = assistants[index];
    fallback ??= item;
    if (splitReasoning(item.text ?? "").answer.trim()) return item;
  }
  return fallback;
}

function latestFileEdit(items: TranscriptItem[]): TranscriptItem | null {
  let fallback: TranscriptItem | null = null;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.type !== "file_edit") continue;
    fallback ??= item;
    if (String(item.diff ?? "").trim()) return item;
  }
  return fallback;
}

function changedPaths(items: TranscriptItem[]): string[] {
  const paths = new Set<string>();
  for (const item of items) {
    if (item.type !== "file_edit") continue;
    for (const path of item.paths ?? []) paths.add(path);
  }
  return [...paths];
}

function TurnProcess({
  items,
  allItems,
  active,
  open,
  onOpenChange,
  onApproval,
  promptDisabled,
  workspace,
}: {
  items: TranscriptItem[];
  allItems: TranscriptItem[];
  active: boolean;
  open: boolean;
  onOpenChange(open: boolean): void;
  onApproval(item: TranscriptItem, approved: boolean): void;
  promptDisabled?: boolean;
  workspace?: string;
}) {
  const summary = useMemo(() => activitySummary(items), [items]);
  const intermediateMessages = useMemo(
    () => items.reduce((count, item) => count + (item.type === "assistant_message" ? 1 : 0), 0),
    [items],
  );
  const operationCount = summary.steps + intermediateMessages;

  return (
    <section className={`turn-process ${active ? "is-live" : "is-settled"} ${open ? "is-open" : ""}`}>
      {!active ? (
        <button
          type="button"
          className="turn-process-header"
          onClick={() => onOpenChange(!open)}
          aria-expanded={open}
          title={open ? "折叠任务过程" : "展开完整任务过程"}
        >
          <span className="turn-process-time">{elapsedLabel(allItems)}</span>
          {operationCount ? <span className="turn-process-meta">{operationCount} 个过程项</span> : null}
          {summary.failed ? <span className="turn-process-failed">部分失败</span> : null}
          <ChevronRight size={14} className="turn-process-chevron" aria-hidden="true" />
          <span className="turn-process-rule" aria-hidden="true" />
        </button>
      ) : null}

      <div className="turn-process-grid">
        <div className="turn-process-inner">
          <div className="turn-process-content">
            <Sequence items={items} onApproval={onApproval} keepActivityOpen promptDisabled={promptDisabled} workspace={workspace} />
          </div>
        </div>
      </div>
    </section>
  );
}

function TurnArtifacts({ items }: { items: TranscriptItem[] }) {
  const edit = latestFileEdit(items);
  const paths = changedPaths(items);
  const diff = String(edit?.diff ?? "").trim();
  if (!paths.length && !diff) return null;
  return <TurnArtifactsPreview items={items} />;
}

interface TurnViewProps {
  turnId: string;
  items: TranscriptItem[];
  active: boolean;
  onApproval(item: TranscriptItem, approved: boolean): void;
  promptDisabled?: boolean;
  workspace?: string;
}

const TurnView = memo(function TurnView({
  turnId,
  items,
  active,
  onApproval,
  promptDisabled,
  workspace,
}: TurnViewProps) {
  const derived = useMemo(() => {
    const userItems: TranscriptItem[] = [];
    const errorItems: TranscriptItem[] = [];
    let latestAssistant: TranscriptItem | null = null;

    for (const item of items) {
      if (item.type === "user_message") userItems.push(item);
      if (!active && item.type === "error") errorItems.push(item);
      if (item.type === "assistant_message") latestAssistant = item;
    }

    const finalAssistant = active ? null : finalAssistantForTurn(items);
    const errorIds = new Set(errorItems.map((item) => item.id));
    const processItems = items.filter((item) => (
      item.type !== "user_message"
      && item.id !== finalAssistant?.id
      && !errorIds.has(item.id)
    ));
    const hasProcess = processItems.some((item) => (
      isActivityItem(item) || item.type === "assistant_message" || item.type === "approval"
    ));

    return { userItems, errorItems, latestAssistant, finalAssistant, processItems, hasProcess };
  }, [active, items]);

  const [processOpen, setProcessOpen] = useState(active);
  const wasActiveRef = useRef(active);

  useEffect(() => {
    if (active) {
      setProcessOpen(true);
    } else if (wasActiveRef.current) {
      const timer = window.setTimeout(() => setProcessOpen(false), 90);
      wasActiveRef.current = active;
      return () => window.clearTimeout(timer);
    }
    wasActiveRef.current = active;
  }, [active]);

  const latestAssistantState = derived.latestAssistant ? splitReasoning(derived.latestAssistant.text ?? "") : null;
  const showPendingThinking = Boolean(
    active
    && latestAssistantState?.state !== "streaming"
    && !latestAssistantState?.answer.trim(),
  );

  return (
    <section className={`turn-block ${active ? "is-active" : "is-complete"}`} data-turn-id={turnId}>
      {derived.userItems.map((item) => (
        <div className="transcript-entry entry-user_message" key={item.id}>
          <ItemView item={item} onApproval={onApproval} promptDisabled={promptDisabled} workspace={workspace} />
        </div>
      ))}

      {derived.hasProcess ? (
        <TurnProcess
          items={derived.processItems}
          allItems={items}
          active={active}
          open={processOpen}
          onOpenChange={setProcessOpen}
          onApproval={onApproval}
          promptDisabled={promptDisabled}
          workspace={workspace}
        />
      ) : null}

      {!active && derived.finalAssistant ? (
        <div className="transcript-entry entry-assistant_message turn-final-answer" key={derived.finalAssistant.id}>
          <ItemView item={derived.finalAssistant} onApproval={onApproval} promptDisabled={promptDisabled} workspace={workspace} />
        </div>
      ) : null}

      {!active ? derived.errorItems.map((item) => (
        <div className="transcript-entry entry-error" key={item.id}>
          <ItemView item={item} onApproval={onApproval} promptDisabled={promptDisabled} workspace={workspace} />
        </div>
      )) : null}

      {!active ? <TurnArtifacts items={items} /> : null}
      {showPendingThinking ? <PendingThinking /> : null}
    </section>
  );
}, (previous, next) => (
  previous.turnId === next.turnId
  && previous.items === next.items
  && previous.active === next.active
  && previous.promptDisabled === next.promptDisabled
  && previous.workspace === next.workspace
));

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

export function Transcript({ items, running, currentTurnId, workspace, promptDisabled, onPrompt, onApproval }: TranscriptProps) {
  const turnBlocks = useStableTurnBlocks(items);
  const activeTurnId = running && currentTurnId ? String(currentTurnId) : "";

  return (
    <div className="transcript-scroll">
      <div className="chat-ambient" aria-hidden="true">
        <span className="ambient-glow glow-one" />
        <span className="ambient-glow glow-two" />
        <span className="ambient-grid" />
      </div>
      <main className="transcript" aria-live="polite">
        {!items.length ? (
          <EmptyState disabled={promptDisabled} onPrompt={onPrompt} />
        ) : turnBlocks.map((block, index) => (
          block.kind === "turn" ? (
            <TurnView
              key={block.id}
              turnId={block.id}
              items={block.items}
              active={Boolean(running && block.id === activeTurnId)}
              onApproval={onApproval}
              promptDisabled={promptDisabled}
              workspace={workspace}
            />
          ) : (
            <div className={`transcript-entry entry-${block.item.type}`} key={block.item.id || `loose-${index}`}>
              <ItemView item={block.item} onApproval={onApproval} promptDisabled={promptDisabled} workspace={workspace} />
            </div>
          )
        ))}
      </main>
    </div>
  );
}
