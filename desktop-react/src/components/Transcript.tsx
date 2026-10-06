import {
  ArrowUpRight,
  Bot,
  Bug,
  Check,
  ChevronRight,
  CircleAlert,
  Code2,
  Copy,
  FileDiff,
  History,
  Pencil,
  Reply,
  Search,
  Terminal,
  ThumbsDown,
  ThumbsUp,
  Wrench,
  Zap,
} from "lucide-react";
import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { artifactName, artifactRenderer, canInlineRenderArtifact } from "../artifactRenderers";
import { useI18n } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { TURN_SETTLE_HOLD_MS } from "../presentationTiming";
import type { TranscriptItem } from "../types/loom";
import { latestTaskPlan, liveTaskProgress } from "./liveTaskProgress";
import { TaskProgressPanel } from "./TaskProgressPanel";
import { ArtifactRenderSurface } from "./ArtifactRenderSurface";
import { HomeTokenActivity } from "./HomeTokenActivity";
import { MarkdownMessage } from "./MarkdownMessage";
import { DecisionPromptCard, DecisionPromptRecoveryCard, parseDecisionMessage } from "./DecisionPromptCard";
import { StreamingPresentation, usePendingPresentations } from "./StreamingPresentation";
import { deferredActivityIndices } from "./presentationOrdering";
import { isSubAgentToolItem } from "./SubAgentWorkspace";
import { ThinkingGlyph } from "./ThinkingGlyph";
import { TurnArtifactsPreview } from "./TurnArtifactsPreview";
import { UserMessageContent, parseUserMessageContent } from "./UserMessageContent";
import { dispatchQuoteReply } from "./quoteReply";
import { ActivityGlyph as ToolIdentityGlyph, ActivityGroupGlyph, activityGroupIdentity, activityIdentity, activityToolLabel } from "./ToolIdentity";
import "./activity-flow.css";
import "./assistant-artifact-preview.css";
import "./message-actions.css";
import "./task-flow-folding.css";
import "./turn-flow.css";
import "./conversation-motion.css";
import "../taskCapsuleLens";

interface TranscriptProps {
  items: TranscriptItem[];
  running?: boolean;
  currentTurnId?: string | null;
  workspace?: string;
  promptDisabled?: boolean;
  onPrompt?(prompt: string): Promise<void> | void;
  onApproval(item: TranscriptItem, approved: boolean): void;
  /** Opens the usage insights page from the home activity card. */
  onOpenInsights?(): void;
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

interface TurnProcessBreakdown {
  commands: number;
  filesEdited: number;
  filesRead: number;
  imagesViewed: number;
  tools: number;
  added: number;
  removed: number;
}

// [English, Simplified Chinese]. The prompt follows the interface language so
// the agent answers in it too.
const starterPrompts = [
  {
    icon: Search,
    title: ["Inspect this project", "了解这个项目"],
    copy: ["Map the architecture and tell me what matters first.", "梳理架构，告诉我最先该关注什么。"],
    prompt: [
      "Inspect this project, map the architecture, and tell me what I should understand first.",
      "检查这个项目，梳理它的架构，并告诉我最先应该了解什么。",
    ],
  },
  {
    icon: Code2,
    title: ["Implement a feature", "实现一个功能"],
    copy: ["Turn a product idea into a focused code change.", "把产品想法变成一次聚焦的代码改动。"],
    prompt: [
      "Help me implement a feature in this project. Start by identifying the smallest clean approach.",
      "帮我在这个项目中实现一个功能。先找出最小、最干净的实现方式。",
    ],
  },
  {
    icon: Bug,
    title: ["Trace a problem", "排查一个问题"],
    copy: ["Follow the failure to its root cause before changing code.", "先追到根本原因，再动手改代码。"],
    prompt: [
      "Investigate the current project for the problem I am seeing and trace it to the root cause before making changes.",
      "排查当前项目中我遇到的问题，在修改代码之前先追查到根本原因。",
    ],
  },
  {
    icon: Zap,
    title: ["Run a workflow", "运行一个工作流"],
    copy: ["Use tools and the workspace to complete a multi-step task.", "借助工具和工作区完成一个多步骤任务。"],
    prompt: [
      "Use the available tools and workspace to complete a useful multi-step task for this project.",
      "使用可用的工具和工作区，为这个项目完成一个有用的多步骤任务。",
    ],
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

interface PendingThinkingHandle {
  element: HTMLDivElement | null;
  mountedAt: number;
}

/** The turn's pending "thinking" capsule, registered while it is mounted. */
const PendingThinkingContext = createContext<PendingThinkingHandle | null>(null);

// Its birth waits a beat, so a capsule younger than this has not been seen.
const PENDING_THINKING_SEEN_MS = 240;

/**
 * Called while rendering the live reasoning capsule, before the commit that
 * removes the pending one, so the old capsule can still be measured. Only a
 * capsule the user has actually seen counts: not one mounted for the single
 * frame between an empty streaming item and its first delta, and not one
 * collapsed behind a running task group.
 */
function pendingThinkingOnScreen(pending: PendingThinkingHandle | null): boolean {
  const element = pending?.element;
  if (!element?.isConnected) return false;
  if (performance.now() - pending!.mountedAt < PENDING_THINKING_SEEN_MS) return false;
  return element.getBoundingClientRect().height >= 16 && Number(getComputedStyle(element).opacity) > 0.5;
}

function LiveReasoning({ reasoning, workspace, streaming, messageKey, interrupted }: { reasoning: string; workspace?: string; streaming: boolean; messageKey: string; interrupted: boolean }) {
  const [open, setOpen] = useState(false);
  const pendingThinking = useContext(PendingThinkingContext);
  // Replacing a capsule that is on screen continues it instead of being born
  // a second time in the same spot, which read as a blink.
  const [handoff] = useState(() => pendingThinkingOnScreen(pendingThinking));
  const hasReasoning = Boolean(reasoning.trim());

  return (
    <div className={`live-reasoning ${open ? "open" : ""} ${handoff ? "is-handoff" : ""}`}>
      <button
        type="button"
        className="live-reasoning-trigger"
        onClick={() => hasReasoning && setOpen((value) => !value)}
        aria-expanded={hasReasoning ? open : undefined}
        disabled={!hasReasoning}
      >
        <ThinkingGlyph />
        <span className="thinking-shimmer">正在思考…</span>
        {hasReasoning ? <ChevronRight size={13} className="live-reasoning-chevron" aria-hidden="true" /> : null}
      </button>

      {hasReasoning ? (
        <div className="live-reasoning-grid">
          <div className="live-reasoning-inner">
            <div className="live-reasoning-copy">
              <MarkdownMessage content={reasoning} compact workspace={workspace} streaming={streaming && open} messageKey={messageKey} interrupted={interrupted || !open} />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PendingThinking() {
  const pending = useContext(PendingThinkingContext);
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!pending) return;
    const element = ref.current;
    pending.element = element;
    pending.mountedAt = performance.now();
    return () => {
      if (pending.element === element) pending.element = null;
    };
  }, [pending]);

  return (
    <div ref={ref} className="inline-thinking" role="status" aria-live="polite">
      <ThinkingGlyph />
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

function isSteeringUserMessage(item: TranscriptItem): boolean {
  return item.type === "user_message"
    && String(item.source ?? "").trim().toLowerCase() === "steering";
}

function itemCreatedAtMs(item: TranscriptItem): number | null {
  const value = item.submittedAt ?? item.createdAt;
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function placeSteeringAtSubmissionTime(items: TranscriptItem[]): TranscriptItem[] {
  const steering = items.filter(isSteeringUserMessage);
  if (!steering.length) return items;

  const ordered = items.filter((item) => !isSteeringUserMessage(item));
  for (const item of steering) {
    const submittedAt = itemCreatedAtMs(item);
    if (submittedAt === null) {
      ordered.push(item);
      continue;
    }

    let insertAt = ordered.length;
    for (let index = 0; index < ordered.length; index += 1) {
      const candidateAt = itemCreatedAtMs(ordered[index]);
      if (candidateAt !== null && candidateAt > submittedAt) {
        insertAt = index;
        break;
      }
    }
    ordered.splice(insertAt, 0, item);
  }
  return ordered;
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
  if (status === "started" || status === "running" || status === "streaming" || status === "streaming_arguments") return "Running";
  if (status === "waiting" || status === "waiting_approval" || status === "pending") return "Waiting";
  if (status === "completed") return "Completed";
  if (status === "changed") return "Changed";
  if (status === "failed") return "Failed";
  if (status === "denied") return "Denied";
  if (status === "cancelled") return "Cancelled";
  if (status === "interrupted") return "Interrupted";
  return status;
}

function isActiveActivityStatus(status: string): boolean {
  return ["started", "running", "streaming", "streaming_arguments", "waiting", "waiting_approval", "pending"].includes(status);
}

function isExecutingActivityStatus(status: string): boolean {
  return ["started", "running", "streaming", "streaming_arguments"].includes(status);
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

function isFileReadTool(item: TranscriptItem): boolean {
  if (item.type !== "tool_call") return false;
  const name = String(item.toolName ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (!name) return false;
  const hasReadVerb = /(^|_)(read|open|fetch|get|cat)(_|$)/.test(name);
  const hasFileObject = /(^|_)(file|files|workspace|text|document|blob)(_|$)/.test(name);
  return hasReadVerb && hasFileObject;
}

function collectReadPaths(value: unknown, paths: Set<string>, keyHint = "") {
  if (typeof value === "string") {
    if (/(^|_)(path|paths|file|files|filename|filepath|file_path)$/.test(keyHint) && value.trim()) paths.add(value.trim());
    return;
  }
  if (Array.isArray(value)) {
    for (const entry of value) collectReadPaths(entry, paths, keyHint);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    collectReadPaths(entry, paths, key.trim().toLowerCase());
  }
}

function isBrowserScreenshotActivity(item: TranscriptItem): boolean {
  return item.type === "tool_call"
    && String(item.toolName ?? "").trim().toLowerCase() === "browser_screenshot";
}

function browserScreenshotPaths(item: TranscriptItem): string[] {
  if (!isBrowserScreenshotActivity(item)) return [];
  const result = item.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return [];
  const path = String((result as Record<string, unknown>).path ?? "").trim();
  if (!path || !/\.png$/i.test(path)) return [];
  return [path];
}

function turnProcessBreakdown(items: TranscriptItem[]): TurnProcessBreakdown {
  const editedPaths = new Set<string>();
  const readPaths = new Set<string>();
  let anonymousEdits = 0;
  let anonymousReads = 0;
  let commands = 0;
  let imagesViewed = 0;
  let added = 0;
  let removed = 0;

  for (const item of items) {
    if (item.type === "process") commands += 1;
    if (item.type === "file_edit") {
      const paths = (item.paths ?? []).map(String).map((path) => path.trim()).filter(Boolean);
      if (paths.length) paths.forEach((path) => editedPaths.add(path));
      else anonymousEdits += 1;
      const stats = diffStats(item.diff);
      added += stats.added;
      removed += stats.removed;
    }
    if (isFileReadTool(item)) {
      const before = readPaths.size;
      collectReadPaths(item.arguments, readPaths);
      if (readPaths.size === before) anonymousReads += 1;
    }
    imagesViewed += browserScreenshotPaths(item).length;
  }

  const compactItems = compactActivityItems(items.filter(isActivityItem));
  const tools = compactItems.reduce((count, item) => (
    item.type === "tool_call" && !isFileReadTool(item) && !browserScreenshotPaths(item).length ? count + 1 : count
  ), 0);

  return {
    commands,
    filesEdited: editedPaths.size + anonymousEdits,
    filesRead: readPaths.size + anonymousReads,
    imagesViewed,
    tools,
    added,
    removed,
  };
}

function turnProcessSummaryLabel(breakdown: TurnProcessBreakdown, fallbackCount: number): string {
  const parts: string[] = [];
  if (breakdown.commands) parts.push(`运行 ${breakdown.commands} 条命令`);
  if (breakdown.filesEdited) parts.push(`编辑 ${breakdown.filesEdited} 个文件`);
  if (breakdown.filesRead) parts.push(`读取 ${breakdown.filesRead} 个文件`);
  if (breakdown.imagesViewed) parts.push(`查看 ${breakdown.imagesViewed} 张图片`);
  if (breakdown.tools) parts.push(`使用 ${breakdown.tools} 个工具`);
  return parts.length ? parts.join(" · ") : `${fallbackCount} 个过程项`;
}

function fileLabel(item: TranscriptItem): string {
  const paths = item.paths ?? [];
  if (!paths.length) return "workspace";
  if (paths.length === 1) return paths[0];
  if (paths.length === 2) return `${paths[0]}, ${paths[1]}`;
  return `${paths[0]}, ${paths[1]} +${paths.length - 2}`;
}

function hasActivityDetail(item: TranscriptItem): boolean {
  if (browserScreenshotPaths(item).length) return true;
  if (item.type === "process") return Boolean(String(item.stdout ?? "").trim() || String(item.stderr ?? "").trim());
  if (item.type === "file_edit") return Boolean(String(item.diff ?? "").trim());
  if (String(item.content ?? "").trim()) return true;
  if (String(item.stdout ?? "").trim() || String(item.stderr ?? "").trim()) return true;
  return item.arguments !== undefined;
}

function activityDetail(item: TranscriptItem): string {
  if (browserScreenshotPaths(item).length) return "";
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

function BrowserScreenshotDetail({ paths, workspace }: { paths: string[]; workspace?: string }) {
  const pathKey = paths.join("\n");
  const [sources, setSources] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    let cancelled = false;
    setSources({});
    setFailed(new Set());
    const workspaceRoot = String(workspace ?? "").trim();
    if (!workspaceRoot) return () => { cancelled = true; };

    for (const path of paths) {
      void window.loom.readLocalImage(path, workspaceRoot)
        .then((result) => {
          if (cancelled) return;
          setSources((current) => ({ ...current, [path]: result.dataUrl }));
        })
        .catch(() => {
          if (cancelled) return;
          setFailed((current) => new Set(current).add(path));
        });
    }

    return () => { cancelled = true; };
  }, [pathKey, workspace]);

  return (
    <div className="task-flow-image-grid" aria-label={`已查看 ${paths.length} 张图片`}>
      {paths.map((path) => {
        const source = sources[path];
        const unavailable = failed.has(path) || !String(workspace ?? "").trim();
        const name = path.replaceAll("\\", "/").split("/").pop() || "browser-screenshot.png";
        return (
          <figure className="task-flow-image-card" key={path} title={path}>
            {source ? (
              <img src={source} alt={`浏览器截图 ${name}`} loading="lazy" decoding="async" />
            ) : (
              <div className={`task-flow-image-loading ${unavailable ? "is-unavailable" : ""}`}>
                {unavailable ? "图片预览不可用" : "正在载入预览…"}
              </div>
            )}
            <figcaption>{name}</figcaption>
          </figure>
        );
      })}
    </div>
  );
}

function liveActivityHint(item: TranscriptItem, status: string): string {
  if (status === "waiting_approval") return "正在等待权限确认…";
  if (status === "waiting" || status === "pending") return "任务已就绪，等待继续…";
  if (item.type === "process") return "命令正在执行，等待输出…";
  if (item.type === "file_edit") return "正在生成文件修改…";
  return "工具正在执行，等待结果…";
}

function ActivityGlyph({ item, size = 13 }: { item: TranscriptItem; size?: number }) {
  const identity = activityIdentity(item);
  if (identity.family === "terminal") return <Terminal size={size} />;
  if (identity.family === "file") return <FileDiff size={size} />;
  if (identity.family === "generic") return <Wrench size={size} />;
  return <ToolIdentityGlyph item={item} size={size} />;
}

interface ActivityRowProps {
  item: TranscriptItem;
  open: boolean;
  workspace?: string;
  onToggle(id: string): void;
}

function sameActivityRowProps(previous: ActivityRowProps, next: ActivityRowProps): boolean {
  if (previous.open !== next.open) return false;
  if (previous.workspace !== next.workspace) return false;
  if (previous.item.id !== next.item.id || previous.item.type !== next.item.type) return false;

  const previousStatus = itemStatus(previous.item);
  const nextStatus = itemStatus(next.item);
  if (previousStatus !== nextStatus) return false;

  if (previous.item.toolName !== next.item.toolName) return false;
  if (browserScreenshotPaths(previous.item).join("\n") !== browserScreenshotPaths(next.item).join("\n")) return false;
  if (previous.item.type === "process" && processCommand(previous.item) !== processCommand(next.item)) return false;
  if (previous.item.type === "file_edit" && fileLabel(previous.item) !== fileLabel(next.item)) return false;

  // Collapsed rows intentionally ignore stdout/stderr/content/argument deltas.
  // Those can arrive every presentation frame and used to make the task pill
  // reconcile while its entrance animation was still running. When expanded,
  // the detail panel remains fully live.
  if (next.open) return activityDetail(previous.item) === activityDetail(next.item);

  const active = isActiveActivityStatus(nextStatus);
  if (!active && hasActivityDetail(previous.item) !== hasActivityDetail(next.item)) return false;
  if (!active && next.item.type === "file_edit" && previous.item.diff !== next.item.diff) return false;
  return true;
}

const ActivityRow = memo(function ActivityRow({ item, open, workspace, onToggle }: ActivityRowProps) {
  const status = itemStatus(item);
  const active = isActiveActivityStatus(status);
  const executing = isExecutingActivityStatus(status);
  const expandable = active || hasActivityDetail(item);
  const detailPresence = useMotionPresence(open, 260);
  const cachedDetailRef = useRef("");
  const liveDetail = open ? activityDetail(item) : "";
  if (open) cachedDetailRef.current = liveDetail;
  const visibleDetail = open ? liveDetail : cachedDetailRef.current;
  const stats = item.type === "file_edit" && (!active || open) ? diffStats(item.diff) : null;
  const screenshotPaths = browserScreenshotPaths(item);
  const screenshotActivity = isBrowserScreenshotActivity(item);
  const verbKey = active ? "active" : "rested";
  const identity = activityIdentity(item);

  return (
    <div
      className={`task-flow-row-wrap ${open ? "is-open" : ""}`}
      data-kind={item.type}
    >
      <button
        type="button"
        className={`task-flow-row task-flow-kind-${item.type} ${active ? "is-active" : "is-resting"} ${executing ? "is-executing" : ""} ${expandable ? "is-expandable" : "no-detail"}`.trim()}
        data-tool-family={identity.family}
        onClick={() => expandable && onToggle(item.id)}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        title={expandable ? (open ? "Collapse details" : "Expand details") : undefined}
      >
        <span className="task-flow-sheen" aria-hidden="true"><i /></span>
        <span className="task-flow-row-icon" title={identity.label}><ActivityGlyph item={item} /></span>
        <span className="task-flow-row-main">
          {/* Verbs are keyed on the live/rested state so the tense change
              remounts the span and cross-fades (conversation-motion.css). */}
          {item.type === "process" ? (
            <>
              <span className="task-flow-verb" key={verbKey}>{active ? "正在运行" : "已运行"}</span>
              <span className="task-flow-primary code">{processCommand(item)}</span>
            </>
          ) : item.type === "file_edit" ? (
            <>
              <span className="task-flow-verb" key={verbKey}>{active ? "正在编辑" : "已编辑"}</span>
              <span className="task-flow-primary task-flow-path">{fileLabel(item)}</span>
              {stats && (stats.added > 0 || stats.removed > 0) ? (
                <span className="task-flow-diffstat">
                  <span className="task-flow-plus">+{stats.added}</span>
                  <span className="task-flow-minus">-{stats.removed}</span>
                </span>
              ) : null}
            </>
          ) : screenshotActivity && screenshotPaths.length ? (
            <>
              <span className="task-flow-verb" key={verbKey}>{active ? "正在查看" : "已查看"}</span>
              <span className="task-flow-primary">{screenshotPaths.length} 张图片</span>
            </>
          ) : (
            <>
              <span className="task-flow-verb" key={verbKey}>{active ? "正在使用" : "已使用"}</span>
              <span className="task-flow-primary">{activityToolLabel(item)}</span>
            </>
          )}
        </span>
        <ActivityStatus status={status} />
      </button>

      {expandable ? (
        <div
          className={`task-flow-inline-detail-grid ${open ? "open" : ""}`}
          data-motion-phase={detailPresence.phase}
        >
          <div className="task-flow-inline-detail-inner">
            {detailPresence.mounted ? (
              <div className="task-flow-inline-detail">
                {screenshotPaths.length ? (
                  <BrowserScreenshotDetail paths={screenshotPaths} workspace={workspace} />
                ) : visibleDetail ? (
                  <pre>{visibleDetail}</pre>
                ) : active ? (
                  <div className="task-flow-live-detail" role="status">
                    <span className="task-flow-live-detail-glow" aria-hidden="true" />
                    <span>{liveActivityHint(item, status)}</span>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}, sameActivityRowProps);

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

function activityGroupTitle(items: TranscriptItem[], running = false): string {
  let hasProcess = false;
  let hasEdit = false;
  let hasTool = false;
  for (const item of items) {
    if (item.type === "process") hasProcess = true;
    else if (item.type === "file_edit") hasEdit = true;
    else if (item.type === "tool_call") hasTool = true;
  }

  if (hasEdit && hasProcess && hasTool) return running ? "正在编辑文件、运行命令并使用工具" : "编辑了文件、运行了命令并使用了工具";
  if (hasEdit && hasProcess) return running ? "正在编辑文件并运行命令" : "编辑了文件并运行了命令";
  if (hasProcess && hasTool) return running ? "正在运行命令并使用工具" : "运行了命令并使用了工具";
  if (hasEdit && hasTool) return running ? "正在编辑文件并使用工具" : "编辑了文件并使用了工具";
  if (hasProcess) return running ? "正在运行命令" : "运行了命令";
  if (hasEdit) return running ? "正在编辑文件" : "编辑了文件";
  return running ? "正在使用工具" : "使用了工具";
}

function ActivityGroupIcon({ items }: { items: TranscriptItem[] }) {
  const identity = activityGroupIdentity(items);
  if (identity.family === "terminal") return <Terminal size={14} />;
  if (identity.family === "file") return <FileDiff size={14} />;
  if (identity.family === "generic") return <Wrench size={14} />;
  return <ActivityGroupGlyph items={items} size={14} />;
}

function ActivityFlow({
  items,
  keepOpen = false,
  continuing = false,
  workspace,
}: {
  items: TranscriptItem[];
  keepOpen?: boolean;
  continuing?: boolean;
  workspace?: string;
}) {
  const compactItems = useMemo(() => compactActivityItems(items), [items]);
  const running = keepOpen;
  const hasActiveRows = compactItems.some((item) => isActiveActivityStatus(itemStatus(item)));
  const betweenSteps = Boolean(running && continuing && !hasActiveRows);
  const [open, setOpen] = useState(true);
  // Opening a group pops its rows out once (.is-unfolding in conversation-motion.css).
  // It is only ever set by the user's click, never on mount, so remounted
  // history stays still.
  const [unfolding, setUnfolding] = useState(false);
  const [openRows, setOpenRows] = useState<Set<string>>(() => new Set());
  const wasRunningRef = useRef(false);

  const toggleRow = useCallback((id: string) => {
    setOpenRows((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  useEffect(() => {
    if (running && !wasRunningRef.current) setOpen(true);
    wasRunningRef.current = running;
  }, [running]);

  useEffect(() => {
    if (!unfolding) return;
    // Long enough for the last staggered row (230ms lead + 460ms glow).
    const timer = window.setTimeout(() => setUnfolding(false), 900);
    return () => window.clearTimeout(timer);
  }, [unfolding]);

  const title = activityGroupTitle(compactItems, running && !betweenSteps);
  const groupIdentity = activityGroupIdentity(compactItems);

  return (
    <section
      className={`task-flow task-flow-group ${open ? "is-open" : ""} ${running ? "is-running" : ""} ${betweenSteps ? "is-between-steps" : ""} ${unfolding ? "is-unfolding" : ""}`}
      data-tool-family={groupIdentity.family}
      aria-label="Task activity"
    >
      <button
        type="button"
        className="task-flow-group-header"
        onClick={() => {
          setUnfolding(!open);
          setOpen(!open);
        }}
        aria-expanded={open}
      >
        {betweenSteps ? <span className="task-flow-between-sheen" aria-hidden="true"><i /></span> : null}
        <span className="task-flow-group-icon" aria-hidden="true" title={groupIdentity.label}><ActivityGroupIcon items={compactItems} /></span>
        <span className="task-flow-group-title" key={title}>{title}</span>
        {betweenSteps ? (
          <span className="task-flow-between-label" role="status" aria-live="polite">
            <i aria-hidden="true" />
            <span>继续处理中</span>
          </span>
        ) : null}
        <ChevronRight size={13} className="task-flow-group-chevron" aria-hidden="true" />
      </button>

      <div className="task-flow-group-grid">
        <div className="task-flow-group-inner">
          <div className="task-flow-list">
            {compactItems.map((item) => (
              <ActivityRow
                key={item.id}
                item={item}
                open={openRows.has(item.id)}
                workspace={workspace}
                onToggle={toggleRow}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function SubAgentActivityNotice({ items }: { items: TranscriptItem[] }) {
  const spawned = items.filter((item) => String(item.toolName || "") === "spawn_agent");
  const count = spawned.length || items.length;
  const running = items.some((item) => isActiveActivityStatus(itemStatus(item)));

  return (
    <button
      type="button"
      className={`sub-agent-inline-notice ${running ? "is-running" : ""}`}
      onClick={() => window.dispatchEvent(new Event("loom:sub-agents-open"))}
      title="在右侧打开子代理工作区"
    >
      <span className="sub-agent-inline-icon" aria-hidden="true"><Bot size={13} /></span>
      <span className="sub-agent-inline-copy">
        {running ? "子代理正在并行工作" : "本轮使用了子代理"}
      </span>
      <span className="sub-agent-inline-count">{count}</span>
      <span className="sub-agent-inline-action">查看工作区</span>
      <ChevronRight size={12} aria-hidden="true" />
    </button>
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

  const quote = () => {
    if (disabled || !canCopy) return;
    dispatchQuoteReply({
      text,
      source: kind,
      messageId: item.id,
    });
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

        <button
          type="button"
          className="message-action-button message-quote-action"
          onClick={quote}
          disabled={disabled || !canCopy}
          title={kind === "assistant" ? "引用回答" : "引用消息"}
          aria-label={kind === "assistant" ? "引用这段回答" : "引用这条消息"}
        >
          <Reply size={14} strokeWidth={1.75} />
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
  streaming = false,
  onApproval,
  onPrompt,
  decisionInteractive = false,
  promptDisabled,
  workspace,
}: {
  item: TranscriptItem;
  streaming?: boolean;
  onApproval(item: TranscriptItem, approved: boolean): void;
  onPrompt?(prompt: string): Promise<void> | void;
  decisionInteractive?: boolean;
  promptDisabled?: boolean;
  workspace?: string;
}) {
  if (item.type === "user_message") {
    const rawText = String(item.text ?? "");
    const parsed = parseUserMessageContent(rawText);
    return (
      <div className="message-shell user-message-shell" data-message-id={item.id} data-loom-message-kind="user" data-loom-message-text={parsed.text}>
        <div className={`user-message ${parsed.attachments.length ? "has-attachments" : ""}`}><UserMessageContent parsed={parsed} workspace={workspace} /></div>
        <MessageToolbar kind="user" item={item} text={parsed.text} editable disabled={promptDisabled} />
      </div>
    );
  }

  if (item.type === "assistant_message") {
    const parsed = splitReasoning(item.text ?? "");
    const providerReasoning = String(item.reasoning ?? "").trim();
    // New providers stream reasoning on its own field. Legacy tag parsing remains
    // only as a compatibility fallback for models that embed thinking in text.
    const reasoning = providerReasoning || parsed.reasoning;
    const interrupted = ["interrupted", "cancelled", "failed"].includes(item.status || "");
    const live = streaming && !interrupted && (item.status === "streaming" || isActiveActivityStatus(item.status || "running"));
    const decisionMessage = parseDecisionMessage(parsed.answer, live);
    const answer = decisionMessage.text.trim();

    if (live && !answer && (reasoning || parsed.state === "streaming")) {
      return <div className="assistant-message"><LiveReasoning reasoning={reasoning} workspace={workspace} streaming={live} messageKey={`${item.id}:reasoning`} interrupted={interrupted} /></div>;
    }

    if (!reasoning && !answer && !decisionMessage.decisions.length) return null;

    return (
      <div className="message-shell assistant-message-shell" data-message-id={item.id} data-loom-message-kind="assistant" data-loom-message-text={answer || reasoning}>
        <div className="assistant-message">
          {reasoning ? (
            <Disclosure label="Thought process">
              <div className="reasoning-copy">
                <MarkdownMessage content={reasoning} compact workspace={workspace} messageKey={`${item.id}:reasoning`} interrupted={interrupted} />
              </div>
            </Disclosure>
          ) : null}
          {answer ? <MarkdownMessage content={answer} workspace={workspace} streaming={live} messageKey={`${item.id}:answer`} interrupted={interrupted} /> : null}
          {decisionMessage.decisions.map((decision, index) => (
            <DecisionPromptCard
              key={decision.id || `${item.id}:decision:${index}`}
              spec={decision}
              disabled={!decisionInteractive || Boolean(promptDisabled)}
              onSubmit={decisionInteractive ? onPrompt : undefined}
            />
          ))}
          {decisionMessage.incomplete && !live ? (
            <DecisionPromptRecoveryCard
              disabled={!decisionInteractive || Boolean(promptDisabled)}
              onRetry={decisionInteractive && onPrompt
                ? () => onPrompt("刚才的选项没有生成完整。请只重新给出完整的选项卡，不要重复前面的分析。")
                : undefined}
            />
          ) : null}
        </div>
        {decisionMessage.decisions.length || decisionMessage.incomplete ? null : (
          <MessageToolbar kind="assistant" item={item} text={answer || reasoning} disabled={promptDisabled} />
        )}
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
  onPrompt,
  keepActivityOpen = false,
  active = false,
  promptDisabled,
  workspace,
}: {
  items: TranscriptItem[];
  onApproval(item: TranscriptItem, approved: boolean): void;
  onPrompt?(prompt: string): Promise<void> | void;
  keepActivityOpen?: boolean;
  active?: boolean;
  promptDisabled?: boolean;
  workspace?: string;
}) {
  const subAgentItems = useMemo(() => items.filter(isSubAgentToolItem), [items]);
  const visibleItems = useMemo(
    () => subAgentItems.length ? items.filter((item) => !isSubAgentToolItem(item)) : items,
    [items, subAgentItems.length],
  );
  const blocks = useMemo(() => groupTranscript(visibleItems), [visibleItems]);
  const pendingPresentations = usePendingPresentations();
  const revealedActivityIds = useRef(new Set<string>());
  const deferredActivityBlocks = useMemo(
    () => deferredActivityIndices(blocks, pendingPresentations, revealedActivityIds.current),
    [blocks, pendingPresentations],
  );
  useLayoutEffect(() => {
    blocks.forEach((block, index) => {
      if (block.kind === "activity" && !deferredActivityBlocks.has(index)) {
        block.items.forEach((item) => revealedActivityIds.current.add(item.id));
      }
    });
  }, [blocks, deferredActivityBlocks]);
  // Only genuinely active tool rows own the "running" semantics. When the
  // turn is still alive but the previous tool batch has completed, keep the
  // latest activity group visually alive in a separate between-steps state
  // instead of pretending the completed command is still executing.
  const activeActivityBlocks = useMemo(() => {
    const live = new Set<number>();
    if (!keepActivityOpen) return live;
    blocks.forEach((block, index) => {
      if (block.kind !== "activity") return;
      if (block.items.some((item) => isActiveActivityStatus(itemStatus(item)))) live.add(index);
    });
    return live;
  }, [blocks, keepActivityOpen]);

  const latestActivityBlockIndex = useMemo(() => {
    for (let index = blocks.length - 1; index >= 0; index -= 1) {
      if (blocks[index].kind === "activity") return index;
    }
    return -1;
  }, [blocks]);

  const assistantBusy = useMemo(() => {
    if (!active) return false;
    for (let index = visibleItems.length - 1; index >= 0; index -= 1) {
      const item = visibleItems[index];
      if (item.type !== "assistant_message") continue;
      const status = itemStatus(item);
      if (isActiveActivityStatus(status)) return true;
      const split = splitReasoning(item.text ?? "");
      return split.state === "streaming";
    }
    return false;
  }, [active, visibleItems]);

  const continuingActivityBlock = useMemo(() => {
    if (!keepActivityOpen || latestActivityBlockIndex < 0 || assistantBusy || activeActivityBlocks.size) return -1;
    return latestActivityBlockIndex;
  }, [activeActivityBlocks, assistantBusy, keepActivityOpen, latestActivityBlockIndex]);

  const liveAssistantId = useMemo(() => {
    if (!active) return "";
    for (let index = visibleItems.length - 1; index >= 0; index -= 1) {
      const item = visibleItems[index];
      if (item.type === "assistant_message") return item.id;
    }
    return "";
  }, [active, visibleItems]);

  return (
    <>
      {subAgentItems.length ? (
        <div className="transcript-entry entry-sub-agent-workspace">
          <SubAgentActivityNotice items={subAgentItems} />
        </div>
      ) : null}

      {blocks.map((block, index) => (
        block.kind === "activity" ? (deferredActivityBlocks.has(index) ? null : (
          <div className="transcript-entry entry-activity" key={`activity-${block.items[0]?.id ?? index}`}>
            <ActivityFlow
              items={block.items}
              keepOpen={activeActivityBlocks.has(index) || continuingActivityBlock === index}
              continuing={continuingActivityBlock === index}
              workspace={workspace}
            />
          </div>
        )) : (
          <div
            className={`transcript-entry entry-${block.item.type} ${isSteeringUserMessage(block.item) ? "entry-steering-user" : ""}`.trim()}
            key={block.item.id}
          >
            <ItemView
              item={block.item}
              streaming={Boolean(active && block.item.type === "assistant_message" && block.item.id === liveAssistantId)}
              onApproval={onApproval}
              onPrompt={onPrompt}
              promptDisabled={promptDisabled}
              workspace={workspace}
            />
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

function latestInlineArtifact(items: TranscriptItem[]): { path: string; revision: number } | null {
  let revision = 0;
  for (const item of items) {
    if (item.type !== "file_edit") continue;
    revision += 1;
  }

  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.type !== "file_edit") continue;
    const paths = item.paths ?? [];
    for (let pathIndex = paths.length - 1; pathIndex >= 0; pathIndex -= 1) {
      const target = String(paths[pathIndex] || "").trim();
      if (target && canInlineRenderArtifact(target)) return { path: target, revision };
    }
  }
  return null;
}

function AssistantArtifactPreview({
  path,
  workspace,
  revision,
}: {
  path: string;
  workspace: string;
  revision: number;
}) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const descriptor = artifactRenderer(path);
  const name = artifactName(path);

  return (
    <div className="assistant-artifact-preview-card" data-renderer-kind={descriptor.kind}>
      {previewOpen ? <ArtifactRenderSurface
        path={path}
        workspace={workspace}
        compact
        revision={revision}
        onOpenSide={() => {
          window.dispatchEvent(new CustomEvent("loom:artifact-preview-open", {
            detail: { path, workspace },
          }));
        }}
      /> : null}
      <div className="assistant-artifact-preview-footer">
        <div className="assistant-artifact-preview-copy">
          <strong title={path}>{name}</strong>
          <span>{descriptor.label} · 可交互预览</span>
        </div>
        <button type="button" aria-expanded={previewOpen} onClick={() => setPreviewOpen(!previewOpen)}>
          {previewOpen ? "收起预览" : "预览"}
        </button>
        <button
          type="button"
          onClick={() => {
            window.dispatchEvent(new CustomEvent("loom:artifact-preview-open", {
              detail: { path, workspace },
            }));
          }}
        >
          打开
        </button>
      </div>
    </div>
  );
}

function TurnProcess({
  items,
  allItems,
  guidanceItems,
  active,
  open,
  onOpenChange,
  onApproval,
  onPrompt,
  promptDisabled,
  workspace,
}: {
  items: TranscriptItem[];
  allItems: TranscriptItem[];
  guidanceItems: TranscriptItem[];
  active: boolean;
  open: boolean;
  onOpenChange(open: boolean): void;
  onApproval(item: TranscriptItem, approved: boolean): void;
  onPrompt?(prompt: string): Promise<void> | void;
  promptDisabled?: boolean;
  workspace?: string;
}) {
  const summary = useMemo(() => activitySummary(items), [items]);
  const breakdown = useMemo(() => turnProcessBreakdown(items), [items]);
  const intermediateMessages = useMemo(
    () => items.reduce((count, item) => count + (item.type === "assistant_message" ? 1 : 0), 0),
    [items],
  );
  const operationCount = summary.steps + intermediateMessages;
  const summaryLabel = useMemo(
    () => turnProcessSummaryLabel(breakdown, operationCount),
    [breakdown, operationCount],
  );
  const [processVisited, setProcessVisited] = useState(active || open);
  const [earlierOpen, setEarlierOpen] = useState(false);
  const earlierHistoryId = useId();
  const progress = useMemo(() => liveTaskProgress(items, new Set(items
    .filter((item) => {
      if (item.type !== "assistant_message") return false;
      const parsed = parseDecisionMessage(item.text ?? "");
      return parsed.decisions.length > 0 || parsed.incomplete;
    })
    .map((item) => item.id))), [items]);

  useEffect(() => {
    if (active || open) setProcessVisited(true);
  }, [active, open]);

  // Historical turns arrive already folded. Avoid constructing their entire
  // tool/process subtree until the user actually expands that turn; after the
  // first expansion keep it mounted so the existing fold animation stays smooth.
  const renderProcessContent = active || open || processVisited;

  return (
    <section className={`turn-process ${active ? "is-live" : "is-settled"} ${open ? "is-open" : ""} ${guidanceItems.length ? "has-guidance" : ""}`.trim()}>
      {!active ? (
        <button
          type="button"
          className="turn-process-header"
          onClick={() => onOpenChange(!open)}
          aria-expanded={open}
          title={open ? "折叠任务过程" : "展开完整任务过程"}
        >
          <span className="turn-process-primary">
            <span className="turn-process-summary">{summaryLabel}</span>
            {(breakdown.added > 0 || breakdown.removed > 0) ? (
              <span className="turn-process-diffstat" aria-label={`新增 ${breakdown.added} 行，删除 ${breakdown.removed} 行`}>
                <span className="turn-process-plus">+{breakdown.added}</span>
                <span className="turn-process-minus">-{breakdown.removed}</span>
              </span>
            ) : null}
            <span className="turn-process-time">{elapsedLabel(allItems)}</span>
          </span>
          <ChevronRight size={14} className="turn-process-chevron" aria-hidden="true" />
        </button>
      ) : null}

      {!active && !open && guidanceItems.length ? (
        <div className="turn-guidance-recap" aria-label="Guidance added during this turn">
          {guidanceItems.map((item) => (
            <div className="transcript-entry entry-user_message entry-steering-user" key={`guidance-${item.id}`}>
              <ItemView item={item} onApproval={onApproval} onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} />
            </div>
          ))}
        </div>
      ) : null}

      {renderProcessContent ? (
        <div className="turn-process-grid">
          <div className="turn-process-inner">
            <div className="turn-process-content">
              {active && progress.earlier.length ? (
                <div className={`earlier-task-process ${earlierOpen ? "is-open" : ""}`.trim()}>
                  <button type="button" className="earlier-process-toggle" aria-expanded={earlierOpen}
                    aria-controls={earlierHistoryId}
                    aria-label={`${earlierOpen ? "收起" : "展开"}较早过程，${progress.earlier.length} 项`}
                    title={earlierOpen ? "收起较早的进度与工具记录" : "展开较早的进度与工具记录"}
                    onClick={() => setEarlierOpen(!earlierOpen)}>
                    <span className="earlier-process-icon" aria-hidden="true"><History size={14} strokeWidth={1.8} /></span>
                    <span className="earlier-process-label">较早过程</span>
                    <span className="earlier-process-count" aria-hidden="true">{progress.earlier.length} 项</span>
                    <ChevronRight size={13} className="earlier-process-chevron" aria-hidden="true" />
                  </button>
                  <div id={earlierHistoryId} className="earlier-process-history" hidden={!earlierOpen}>
                    {earlierOpen ? <Sequence items={progress.earlier} active={false} onApproval={onApproval}
                      onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} /> : null}
                  </div>
                </div>
              ) : null}
              <Sequence items={active ? progress.current : items} active={active} onApproval={onApproval} onPrompt={onPrompt} keepActivityOpen={active} promptDisabled={promptDisabled} workspace={workspace} />
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function TurnArtifacts({ items, workspace }: { items: TranscriptItem[]; workspace?: string }) {
  const edit = latestFileEdit(items);
  const paths = changedPaths(items);
  const diff = String(edit?.diff ?? "").trim();
  if (!paths.length && !diff) return null;
  return <TurnArtifactsPreview items={items} workspace={workspace} />;
}

interface TurnViewProps {
  turnId: string;
  items: TranscriptItem[];
  active: boolean;
  onApproval(item: TranscriptItem, approved: boolean): void;
  onPrompt?(prompt: string): Promise<void> | void;
  decisionInteractiveItemId?: string;
  promptDisabled?: boolean;
  workspace?: string;
}

const TurnView = memo(function TurnView({
  turnId,
  items,
  active,
  onApproval,
  onPrompt,
  decisionInteractiveItemId = "",
  promptDisabled,
  workspace,
}: TurnViewProps) {
  const derived = useMemo(() => {
    const orderedItems = placeSteeringAtSubmissionTime(items);
    const userItems = orderedItems.filter((item) => item.type === "user_message");
    const initialUser = userItems.find((item) => !isSteeringUserMessage(item)) ?? userItems[0] ?? null;
    const guidanceItems = userItems.filter((item) => item.id !== initialUser?.id);
    const errorItems: TranscriptItem[] = [];
    let latestAssistant: TranscriptItem | null = null;

    for (const item of orderedItems) {
      if (!active && item.type === "error") errorItems.push(item);
      if (item.type === "assistant_message") latestAssistant = item;
    }

    const finalAssistant = active ? null : finalAssistantForTurn(orderedItems);
    const errorIds = new Set(errorItems.map((item) => item.id));
    const processItems = orderedItems.filter((item) => (
      item.id !== initialUser?.id
      && item.id !== finalAssistant?.id
      && !errorIds.has(item.id)
    ));
    const hasProcess = processItems.some((item) => (
      isActivityItem(item)
      || item.type === "assistant_message"
      || item.type === "approval"
      || item.type === "user_message"
    ));

    return {
      orderedItems,
      initialUser,
      guidanceItems,
      errorItems,
      latestAssistant,
      finalAssistant,
      processItems,
      hasProcess,
    };
  }, [active, items]);

  const [processOpen, setProcessOpen] = useState(active);
  const [settling, setSettling] = useState(false);
  const wasActiveRef = useRef(active);

  useEffect(() => {
    if (active) {
      setProcessOpen(true);
      setSettling(false);
    } else if (wasActiveRef.current) {
      // Only a live -> complete transition owns completion motion. Historical
      // turns mount already settled and therefore never replay the handoff.
      setSettling(true);
      const foldTimer = window.setTimeout(() => setProcessOpen(false), TURN_SETTLE_HOLD_MS);
      const settleTimer = window.setTimeout(() => setSettling(false), TURN_SETTLE_HOLD_MS + 360);
      wasActiveRef.current = active;
      return () => {
        window.clearTimeout(foldTimer);
        window.clearTimeout(settleTimer);
      };
    } else {
      setSettling(false);
    }
    wasActiveRef.current = active;
  }, [active]);

  const inlineArtifact = useMemo(() => latestInlineArtifact(derived.orderedItems), [derived.orderedItems]);
  const latestAssistantState = derived.latestAssistant ? splitReasoning(derived.latestAssistant.text ?? "") : null;
  const latestProviderReasoning = String(derived.latestAssistant?.reasoning ?? "").trim();
  const showPendingThinking = Boolean(
    active
    && !latestProviderReasoning
    && latestAssistantState?.state !== "streaming"
    && !latestAssistantState?.answer.trim(),
  );
  const pendingThinking = useRef<PendingThinkingHandle>({ element: null, mountedAt: 0 }).current;

  return (
    <StreamingPresentation>
    <PendingThinkingContext.Provider value={pendingThinking}>
    <section className={`turn-block ${active ? "is-active" : "is-complete"} ${settling ? "is-settling" : ""}`.trim()} data-turn-id={turnId}>
      {derived.initialUser ? (
        <div className="transcript-entry entry-user_message" key={derived.initialUser.id}>
          <ItemView item={derived.initialUser} onApproval={onApproval} onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} />
        </div>
      ) : null}

      {derived.hasProcess ? (
        <TurnProcess
          items={derived.processItems}
          allItems={derived.orderedItems}
          guidanceItems={derived.guidanceItems}
          active={active}
          open={processOpen}
          onOpenChange={setProcessOpen}
          onApproval={onApproval}
          onPrompt={onPrompt}
          promptDisabled={promptDisabled}
          workspace={workspace}
        />
      ) : null}

      {!active && derived.finalAssistant ? (
        <div className="transcript-entry entry-assistant_message turn-final-answer" key={derived.finalAssistant.id}>
          <ItemView
            item={derived.finalAssistant}
            onApproval={onApproval}
            onPrompt={onPrompt}
            decisionInteractive={derived.finalAssistant.id === decisionInteractiveItemId}
            promptDisabled={promptDisabled}
            workspace={workspace}
          />
        </div>
      ) : null}

      {inlineArtifact && workspace ? (
        <div className="transcript-entry entry-artifact-preview" key={`artifact-preview:${inlineArtifact.path}`}>
          <AssistantArtifactPreview
            path={inlineArtifact.path}
            workspace={workspace}
            revision={inlineArtifact.revision}
          />
        </div>
      ) : null}

      {!active ? derived.errorItems.map((item) => (
        <div className="transcript-entry entry-error" key={item.id}>
          <ItemView item={item} onApproval={onApproval} onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} />
        </div>
      )) : null}

      {!active ? <TurnArtifacts items={items} workspace={workspace} /> : null}
      {showPendingThinking ? <PendingThinking /> : null}
    </section>
    </PendingThinkingContext.Provider>
    </StreamingPresentation>
  );
}, (previous, next) => (
  previous.turnId === next.turnId
  && previous.items === next.items
  && previous.active === next.active
  && previous.promptDisabled === next.promptDisabled
  && previous.workspace === next.workspace
  && previous.decisionInteractiveItemId === next.decisionInteractiveItemId
));

function EmptyState({ disabled, onPrompt, onOpenInsights }: {
  disabled?: boolean;
  onPrompt?(prompt: string): Promise<void> | void;
  onOpenInsights?(): void;
}) {
  const { language } = useI18n();
  const lang = language === "zh-CN" ? 1 : 0;
  return (
    <section className="empty-state">
      <div className="empty-intro">
        <div className="empty-hero" aria-hidden="true">
          <span className="empty-hero-aura" />
          <span className="empty-hero-field" />
          <span className="empty-loom-sweep" />
          <span className="empty-loom-orbit empty-loom-orbit-a" />
          <span className="empty-loom-orbit empty-loom-orbit-b" />
          <span className="empty-loom-orbit empty-loom-orbit-c" />
          <span className="empty-loom-core" />
          <span className="empty-loom-pulse empty-loom-pulse-a" />
          <span className="empty-loom-pulse empty-loom-pulse-b" />
          <span className="empty-spark spark-one" />
          <span className="empty-spark spark-two" />
          <span className="empty-spark spark-three" />
        </div>
        <h1>{lang ? "今天我们做点什么？" : "What are we working on?"}</h1>
        <p>
          {lang
            ? "浏览代码库、修改代码、排查故障，或把一个多步骤任务交给 Loom。"
            : "Inspect a codebase, make a change, debug a failure, or hand Loom a multi-step task."}
        </p>
      </div>

      <HomeTokenActivity onOpenInsights={onOpenInsights} />

      <div className="starter-grid">
        {starterPrompts.map(({ icon: Icon, title, copy, prompt }) => (
          <button
            className="starter-card"
            type="button"
            key={title[0]}
            disabled={disabled}
            onClick={() => onPrompt?.(prompt[lang])}
          >
            <span className="starter-icon"><Icon size={17} strokeWidth={1.85} /></span>
            <span className="starter-content">
              <strong>{title[lang]}</strong>
              <span>{copy[lang]}</span>
            </span>
            <ArrowUpRight className="starter-arrow" size={15} strokeWidth={1.8} aria-hidden="true" />
          </button>
        ))}
      </div>
    </section>
  );
}

export function Transcript({ items, running, currentTurnId, workspace, promptDisabled, onPrompt, onApproval, onOpenInsights }: TranscriptProps) {
  const turnBlocks = useStableTurnBlocks(items);
  const activeTurnId = running && currentTurnId ? String(currentTurnId) : "";
  const milestones = useMemo(() => {
    const block = turnBlocks.find((block) => block.kind === "turn" && block.id === activeTurnId);
    return block?.kind === "turn" ? latestTaskPlan(block.items) : [];
  }, [turnBlocks, activeTurnId]);
  const decisionInteractiveItemId = useMemo(() => {
    if (running || promptDisabled || !onPrompt) return "";
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index];
      if (item.type === "user_message") return "";
      if (item.type !== "assistant_message") continue;
      const parsed = splitReasoning(item.text ?? "");
      const decision = parseDecisionMessage(parsed.answer, false);
      return decision.decisions.length || decision.incomplete ? item.id : "";
    }
    return "";
  }, [items, onPrompt, promptDisabled, running]);

  return (
    <>
    <div className={`transcript-scroll ${!items.length ? "is-empty" : ""}`}>
      <div className="chat-ambient" aria-hidden="true">
        <span className="ambient-glow glow-one" />
        <span className="ambient-glow glow-two" />
        <span className="ambient-grid" />
      </div>
      <main className={`transcript ${!items.length ? "is-empty" : ""}`} aria-live="polite">
        {!items.length ? (
          <EmptyState disabled={promptDisabled} onPrompt={onPrompt} onOpenInsights={onOpenInsights} />
        ) : turnBlocks.map((block, index) => (
          block.kind === "turn" ? (
            <TurnView
              key={block.id}
              turnId={block.id}
              items={block.items}
              active={Boolean(running && block.id === activeTurnId)}
              onApproval={onApproval}
              onPrompt={onPrompt}
              decisionInteractiveItemId={decisionInteractiveItemId}
              promptDisabled={promptDisabled}
              workspace={workspace}
            />
          ) : (
            <div className={`transcript-entry entry-${block.item.type}`} key={block.item.id || `loose-${index}`}>
              <ItemView
                item={block.item}
                onApproval={onApproval}
                onPrompt={onPrompt}
                decisionInteractive={block.item.id === decisionInteractiveItemId}
                promptDisabled={promptDisabled}
                workspace={workspace}
              />
            </div>
          )
        ))}
      </main>
    </div>
      {milestones.length ? <TaskProgressPanel key={activeTurnId} steps={milestones} /> : null}
    </>
  );
}
