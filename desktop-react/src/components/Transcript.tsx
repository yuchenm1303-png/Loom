import {
  ArrowUpRight,
  Bot,
  Bug,
  Check,
  ChevronRight,
  CircleAlert,
  CircleStop,
  Code2,
  Copy,
  Eye,
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
  Fragment,
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
import { artifactName, artifactRenderer } from "../artifactRenderers";
import { useI18n } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { useReducedMotion } from "../motion/useReducedMotion";
import { useEarlierProcessHandoff } from "./useEarlierProcessHandoff";
import { LIVE_STATUS_GRACE_MS, TURN_FOLD_MS, TURN_SETTLE_HOLD_MS } from "../presentationTiming";
import type { TranscriptItem } from "../types/loom";
import { latestTaskPlan, liveTaskProgress } from "./liveTaskProgress";
import { TaskProgressPanel } from "./TaskProgressPanel";

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
import {
  commandFromItem,
  describeActivity,
  isCommandTool,
  isEditTool,
  normalizedToolName,
  useRuntimeCopy,
  type ActivityCategory,
  type ActivityDescription,
  type ProcessSummaryParts,
  type RuntimeCopy,
} from "./runtimeCopy";
import "./activity-flow.css";
import "./message-actions.css";
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
/** Live -> complete hand-off: hold the live layout, then fold it into the summary. */
type SettlePhase = "hold" | "fold" | null;

interface ReasoningSplit {
  reasoning: string;
  answer: string;
  state: ReasoningState;
}

interface ActivitySummaryData {
  steps: number;
  failed: boolean;
}

interface TurnProcessBreakdown extends ProcessSummaryParts {
  added: number;
  removed: number;
}

/**
 * One visible task row. A tool call that only launches a process or writes a
 * file (`exec`, `write_workspace_text`, ...) and the process/diff it produces
 * are the same step, so they share one row keyed by the call that appeared
 * first. The row keeps its DOM, motion and disclosure state while the outcome
 * item arrives, instead of swapping "使用 exec" for "运行 …" a moment later.
 */
interface ActivityRowModel {
  key: string;
  item: TranscriptItem;
  wrapper: TranscriptItem | null;
}

/** Per-step view of the cumulative turn diff snapshots. */
interface FileEditDelta {
  paths: string[];
  diff: string;
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

/**
 * The visible "thinking" surface of a live turn, registered while mounted.
 * The standalone capsule and a message's reasoning header take turns at the
 * growth edge; a header that replaces a capsule the user has seen continues it
 * in place instead of being born again (which read as a blink).
 */
interface ThinkingHandle {
  element: HTMLElement | null;
  mountedAt: number;
}

const ThinkingHandleContext = createContext<ThinkingHandle | null>(null);
/** True inside the live sequence of an active turn (not its earlier history). */
const LiveSequenceContext = createContext(false);

/**
 * Activity rows and groups the renderer has already shown. One-shot motion is
 * bound to a row's first appearance in a live turn, never to a class that can
 * toggle later (a group regaining its running state used to replay every
 * row's birth), and never to history or a remount after switching threads.
 */
const seenActivity = new Set<string>();

function useBornLive(key: string): boolean {
  const live = useContext(LiveSequenceContext);
  const [born] = useState(() => live && !seenActivity.has(key));
  useLayoutEffect(() => {
    seenActivity.add(key);
  }, [key]);
  return born;
}
/** Turn-wide view of each diff snapshot, so a row shows only its own files. */
const FileEditDeltaContext = createContext<ReadonlyMap<string, FileEditDelta>>(new Map());

// A capsule younger than this has not been seen. Opacity and size are checked
// as well, so this only filters the single-frame mounts of runtime races.
const THINKING_SEEN_MS = 90;

/**
 * Evaluated while rendering the new surface, before the commit that removes
 * the old one, so the old capsule can still be measured. Only a capsule the
 * user has actually seen counts: not one mounted for a single frame, and not
 * one collapsed behind running task rows.
 */
function thinkingOnScreen(handle: ThinkingHandle | null): boolean {
  const element = handle?.element;
  if (!element?.isConnected) return false;
  if (performance.now() - handle!.mountedAt < THINKING_SEEN_MS) return false;
  const presence = element.closest(".pending-thinking-presence");
  if (presence && (presence.getBoundingClientRect().height < 16 || Number(getComputedStyle(presence).opacity) <= .5)) return false;
  return element.getBoundingClientRect().height >= 16 && Number(getComputedStyle(element).opacity) > 0.5;
}

function useThinkingRegistration(enabled: boolean) {
  const handle = useContext(ThinkingHandleContext);
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!enabled || !handle) return;
    const element = ref.current;
    handle.element = element;
    handle.mountedAt = performance.now();
    return () => {
      if (handle.element === element) handle.element = null;
    };
  }, [enabled, handle]);
  return ref;
}

/**
 * A message's reasoning header. While the model thinks it is the live capsule
 * ("正在思考…" with the pulsing bead); once the answer starts, the same element
 * settles into a quiet "思考过程" disclosure above the text. Keeping one element
 * through that change lets CSS morph it instead of swapping two surfaces.
 */
function ReasoningBlock({
  reasoning,
  thinking,
  live,
  workspace,
  messageKey,
  interrupted,
}: {
  reasoning: string;
  thinking: boolean;
  live: boolean;
  workspace?: string;
  messageKey: string;
  interrupted: boolean;
}) {
  const copy = useRuntimeCopy();
  const [open, setOpen] = useState(false);
  const handle = useContext(ThinkingHandleContext);
  const [handoff] = useState(() => thinking && thinkingOnScreen(handle));
  const ref = useThinkingRegistration(thinking);
  const hasReasoning = Boolean(reasoning.trim());

  return (
    <div
      ref={ref}
      className={`live-reasoning ${thinking ? "is-thinking" : "is-done"} ${open ? "open" : ""} ${handoff ? "is-handoff" : ""} ${hasReasoning ? "has-reasoning" : ""}`.replace(/\s+/g, " ").trim()}
      role={thinking ? "status" : undefined}
      aria-live={thinking ? "polite" : undefined}
    >
      <button
        type="button"
        className="live-reasoning-trigger"
        onClick={() => hasReasoning && setOpen((value) => !value)}
        aria-expanded={hasReasoning ? open : undefined}
        disabled={!hasReasoning}
        title={hasReasoning ? (open ? copy.hideReasoning : copy.showReasoning) : undefined}
      >
        <span className="live-reasoning-bead" aria-hidden="true"><ThinkingGlyph /></span>
        <span className="live-reasoning-labels">
          <span className="live-reasoning-label is-live thinking-shimmer" aria-hidden={!thinking}>{copy.thinking}</span>
          <span className="live-reasoning-label is-rest" aria-hidden={thinking}>{copy.thoughtProcess}</span>
        </span>
        {hasReasoning ? <ChevronRight size={13} className="live-reasoning-chevron" aria-hidden="true" /> : null}
      </button>

      {hasReasoning ? (
        <div className="live-reasoning-grid">
          <div className="live-reasoning-inner">
            <div className="live-reasoning-copy">
              <MarkdownMessage
                content={reasoning}
                compact
                workspace={workspace}
                streaming={live && thinking && open}
                messageKey={messageKey}
                interrupted={interrupted || !open}
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** The standalone live capsule for the gaps no message or tool row explains. */
function ThinkingCapsule() {
  const copy = useRuntimeCopy();
  const ref = useThinkingRegistration(true);
  return (
    <div ref={ref} className="inline-thinking" role="status" aria-live="polite">
      <ThinkingGlyph />
      <span className="thinking-shimmer">{copy.thinking}</span>
    </div>
  );
}

/** True once `value` has held for `delayMs`; drops immediately. */
function useSettledFlag(value: boolean, delayMs: number): boolean {
  const [settled, setSettled] = useState(value && delayMs <= 0);
  useEffect(() => {
    if (!value) {
      setSettled(false);
      return;
    }
    if (delayMs <= 0) {
      setSettled(true);
      return;
    }
    const timer = window.setTimeout(() => setSettled(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);
  return value && settled;
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

function processHandoffGroups(items: TranscriptItem[]): string[][] {
  const agents = items.filter(isSubAgentToolItem);
  return [...(agents.length ? [agents.map(item => item.id)] : []),
    ...groupTranscript(items.filter(item => !isSubAgentToolItem(item)))
      .map(block => block.kind === "activity" ? block.items.map(item => item.id) : [block.item.id])];
}

function groupTurns(items: TranscriptItem[]): TurnBlock[] {
  const blocks: TurnBlock[] = [];

  for (const item of items) {
    const turnId = String(item.turnId || (item.type === "user_message" && item.status === "sending" ? item.clientMessageId || item.id : ""));
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

function itemStatus(item: TranscriptItem): string {
  if (item.type === "tool_call") {
    const result = item.result as Record<string, unknown> | undefined;
    const evidence = result?.action_evidence as Record<string, unknown> | undefined;
    if (result?.execution_status === "not_executed" || evidence?.execution_status === "not_executed") return "not_executed";
    if (result?.effect === "uncertain") return "uncertain";
    if (item.status === "failed" && ["stale_observation", "stale_element"].includes(String(result?.error_code || ""))) return "refresh_required";
  }
  if (item.type === "file_edit") return item.status || "changed";
  return item.status || "completed";
}

function isActiveActivityStatus(status: string): boolean {
  return ["started", "running", "streaming", "streaming_arguments", "waiting", "waiting_approval", "pending"].includes(status);
}

function isExecutingActivityStatus(status: string): boolean {
  return ["started", "running", "streaming", "streaming_arguments"].includes(status);
}

function isFailureStatus(status: string): boolean {
  return status === "failed" || status === "denied" || status === "cancelled" || status === "interrupted";
}

/** The wrapper's verdict wins when it reports a problem the outcome item cannot. */
function rowStatus(row: ActivityRowModel): string {
  const status = itemStatus(row.item);
  if (!row.wrapper || row.wrapper === row.item) return status;
  const wrapper = itemStatus(row.wrapper);
  if (isFailureStatus(wrapper) || ["not_executed", "uncertain", "refresh_required", "waiting", "waiting_approval"].includes(wrapper)) return wrapper;
  return status;
}

function buildActivityRows(items: TranscriptItem[]): ActivityRowModel[] {
  const rows: ActivityRowModel[] = [];
  const commandWrappers: ActivityRowModel[] = [];
  const editWrappers: ActivityRowModel[] = [];

  for (const item of items) {
    if (item.type === "tool_call" && (isCommandTool(item) || isEditTool(item))) {
      const row = { key: item.id, item, wrapper: item };
      rows.push(row);
      (isCommandTool(item) ? commandWrappers : editWrappers).push(row);
      continue;
    }
    if (item.type === "process" && commandWrappers.length) {
      const command = commandFromItem(item);
      const exact = commandWrappers.findIndex((row) => commandFromItem(row.wrapper) === command);
      const [row] = commandWrappers.splice(exact >= 0 ? exact : 0, 1);
      row.item = item;
      continue;
    }
    if (item.type === "file_edit") {
      const index = editWrappers.findIndex((row) => !isFailureStatus(itemStatus(row.item)));
      if (index >= 0) {
        const [row] = editWrappers.splice(index, 1);
        row.item = item;
        continue;
      }
    }
    rows.push({ key: item.id, item, wrapper: null });
  }
  return rows;
}

const VISUAL_ARTIFACTS = new Set(["web", "image", "pdf", "video", "audio"]);

/** The last written file worth looking at rather than reading as a diff. */
function visualArtifactPath(paths: readonly unknown[]): string {
  for (let index = paths.length - 1; index >= 0; index -= 1) {
    const path = String(paths[index] ?? "").trim();
    if (path && VISUAL_ARTIFACTS.has(artifactRenderer(path).kind)) return path;
  }
  return "";
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

/** Split a unified diff into per-file chunks (path -> chunk text). */
function splitDiffByFile(diff: string): Map<string, string> {
  const files = new Map<string, string>();
  const lines = diff.split("\n");
  let path = "";
  let chunk: string[] = [];
  let sawHunk = false;
  const flush = () => {
    if (chunk.length && path) files.set(path, chunk.join("\n"));
    chunk = [];
    sawHunk = false;
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const git = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
    const pair = line.startsWith("--- ") && lines[index + 1]?.startsWith("+++ ");
    if (git || (pair && (sawHunk || !chunk.length || !path))) {
      if (!(pair && chunk.length && !sawHunk && path)) flush();
      if (git) path = git[2].trim();
    }
    if (pair) {
      const target = lines[index + 1].slice(4).trim().replace(/^b\//, "");
      const source = line.slice(4).trim().replace(/^a\//, "");
      path = target && target !== "/dev/null" ? target : source;
    }
    if (line.startsWith("@@")) sawHunk = true;
    chunk.push(line);
  }
  flush();
  return files;
}

function fileEditDeltas(items: TranscriptItem[]): Map<string, FileEditDelta> {
  const deltas = new Map<string, FileEditDelta>();
  const previousChunks = new Map<string, string>();
  const previousPaths = new Set<string>();
  for (const item of items) {
    if (item.type !== "file_edit") continue;
    const diff = String(item.diff ?? "");
    const paths = (item.paths ?? []).map(String).map((path) => path.trim()).filter(Boolean);
    const chunks = splitDiffByFile(diff);
    const changed = [...chunks].filter(([path, text]) => previousChunks.get(path) !== text);
    const changedPaths = new Set(changed.map(([path]) => path));
    for (const path of paths) {
      if (!previousPaths.has(path) && ![...chunks.keys()].some((known) => known.endsWith(path) || path.endsWith(known))) changedPaths.add(path);
    }
    chunks.forEach((text, path) => previousChunks.set(path, text));
    paths.forEach((path) => previousPaths.add(path));
    if (!changedPaths.size) {
      deltas.set(item.id, { paths, diff, ...diffStats(diff) });
      continue;
    }
    const deltaDiff = changed.length ? changed.map(([, text]) => text).join("\n") : diff;
    deltas.set(item.id, { paths: [...changedPaths], diff: deltaDiff, ...diffStats(deltaDiff) });
  }
  return deltas;
}

function isFileReadTool(item: TranscriptItem): boolean {
  if (item.type !== "tool_call") return false;
  const name = normalizedToolName(item);
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
  const rows = buildActivityRows(items.filter(isActivityItem));
  const editedPaths = new Set<string>();
  const readPaths = new Set<string>();
  let anonymousEdits = 0;
  let anonymousReads = 0;
  let commands = 0;
  let imagesViewed = 0;
  let tools = 0;
  let lastDiff = "";

  for (const row of rows) {
    const source = row.wrapper ?? row.item;
    if (row.item.type === "process" || isCommandTool(source)) {
      commands += 1;
      continue;
    }
    if (row.item.type === "file_edit" || isEditTool(source)) {
      const paths = (row.item.type === "file_edit" ? row.item.paths ?? [] : []).map(String).map((path) => path.trim()).filter(Boolean);
      if (paths.length) paths.forEach((path) => editedPaths.add(path));
      else if (row.item.type === "file_edit") anonymousEdits += 1;
      if (row.item.type === "file_edit" && String(row.item.diff ?? "").trim()) lastDiff = String(row.item.diff);
      continue;
    }
    if (isFileReadTool(row.item)) {
      const before = readPaths.size;
      collectReadPaths(row.item.arguments, readPaths);
      if (readPaths.size === before) anonymousReads += 1;
      continue;
    }
    const screenshots = browserScreenshotPaths(row.item).length;
    if (screenshots) {
      imagesViewed += screenshots;
      continue;
    }
    tools += 1;
  }

  // Diff items are cumulative turn snapshots: the latest one is the turn's diff.
  const { added, removed } = diffStats(lastDiff);
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

function rowHasDetail(row: ActivityRowModel): boolean {
  return hasActivityDetail(row.item) || Boolean(row.wrapper && row.wrapper !== row.item && hasActivityDetail(row.wrapper));
}

function rowDetail(row: ActivityRowModel, delta?: FileEditDelta): string {
  if (row.item.type === "file_edit" && delta?.diff.trim()) return delta.diff.trim();
  const primary = activityDetail(row.item);
  if (primary || !row.wrapper || row.wrapper === row.item) return primary;
  return activityDetail(row.wrapper);
}

function BrowserScreenshotDetail({ paths, workspace }: { paths: string[]; workspace?: string }) {
  const copy = useRuntimeCopy();
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
    <div className="task-flow-image-grid" aria-label={copy.imagesViewed(paths.length)}>
      {paths.map((path) => {
        const source = sources[path];
        const unavailable = failed.has(path) || !String(workspace ?? "").trim();
        const name = path.replaceAll("\\", "/").split("/").pop() || "browser-screenshot.png";
        return (
          <figure className="task-flow-image-card" key={path} title={path}>
            {source ? (
              <img src={source} alt={copy.imageAlt(name)} loading="lazy" decoding="async" />
            ) : (
              <div className={`task-flow-image-loading ${unavailable ? "is-unavailable" : ""}`}>
                {unavailable ? copy.imageUnavailable : copy.imageLoading}
              </div>
            )}
            <figcaption>{name}</figcaption>
          </figure>
        );
      })}
    </div>
  );
}

function liveHintKind(row: ActivityRowModel, status: string): "approval" | "waiting" | "command" | "edit" | "tool" {
  if (status === "waiting_approval") return "approval";
  if (status === "waiting" || status === "pending") return "waiting";
  const source = row.wrapper ?? row.item;
  if (row.item.type === "process" || isCommandTool(source)) return "command";
  if (row.item.type === "file_edit" || isEditTool(source)) return "edit";
  return "tool";
}

/** The item whose identity (icon, family) represents a row from its first frame. */
function identitySource(row: ActivityRowModel): TranscriptItem {
  const source = row.wrapper ?? row.item;
  if (row.item.type === "process" || isCommandTool(source)) return row.item.type === "process" ? row.item : { ...source, type: "process" };
  if (row.item.type === "file_edit" || isEditTool(source)) return row.item.type === "file_edit" ? row.item : { ...source, type: "file_edit" };
  return source;
}

function RowGlyph({ row, category, size = 13 }: { row: ActivityRowModel; category: ActivityCategory; size?: number }) {
  if (category === "command") return <Terminal size={size} />;
  if (category === "edit") return <FileDiff size={size} />;
  const source = identitySource(row);
  const identity = activityIdentity(source);
  if (identity.family === "terminal") return <Terminal size={size} />;
  if (identity.family === "file") return <FileDiff size={size} />;
  if (identity.family === "generic") return <Wrench size={size} />;
  return <ToolIdentityGlyph item={source} size={size} />;
}

function ActivityStatus({ status, copy }: { status: string; copy: RuntimeCopy }) {
  const quiet = status === "completed" || status === "changed";
  const label = copy.statusLabel(status);
  return (
    <span className={`task-flow-status ${status}`} title={label} aria-label={label}>
      <span className="task-flow-status-dot" />
      {!quiet ? <span>{label}</span> : null}
    </span>
  );
}

function describeRow(row: ActivityRowModel, copy: RuntimeCopy, delta?: FileEditDelta): ActivityDescription {
  const status = rowStatus(row);
  const screenshots = browserScreenshotPaths(row.item).length;
  const primary = row.item.type === "file_edit" && delta ? { ...row.item, paths: delta.paths } : row.item;
  return describeActivity(primary, row.wrapper, status, copy, {
    fallbackToolLabel: activityToolLabel(row.wrapper ?? row.item),
    screenshotCount: screenshots,
  });
}

interface ActivityRowProps {
  row: ActivityRowModel;
  open: boolean;
  workspace?: string;
  copy: RuntimeCopy;
  delta?: FileEditDelta;
  onToggle(id: string): void;
}

function sameActivityRowProps(previous: ActivityRowProps, next: ActivityRowProps): boolean {
  if (previous.open !== next.open) return false;
  if (previous.workspace !== next.workspace || previous.copy !== next.copy || previous.delta !== next.delta) return false;
  if (previous.row.key !== next.row.key) return false;
  if (previous.row.item.id !== next.row.item.id || previous.row.item.type !== next.row.item.type) return false;
  if ((previous.row.wrapper?.id ?? "") !== (next.row.wrapper?.id ?? "")) return false;

  const previousStatus = rowStatus(previous.row);
  const nextStatus = rowStatus(next.row);
  if (previousStatus !== nextStatus) return false;

  if (previous.row.item.toolName !== next.row.item.toolName) return false;
  if (browserScreenshotPaths(previous.row.item).join("\n") !== browserScreenshotPaths(next.row.item).join("\n")) return false;
  if (commandFromItem(previous.row.item) !== commandFromItem(next.row.item)) return false;
  if ((previous.row.item.paths ?? []).join("\n") !== (next.row.item.paths ?? []).join("\n")) return false;
  if (previous.row.wrapper && next.row.wrapper && previous.row.wrapper.arguments !== next.row.wrapper.arguments) return false;
  if (previous.row.item.arguments !== next.row.item.arguments && !isActiveActivityStatus(nextStatus)) return false;

  // Collapsed rows intentionally ignore stdout/stderr/content/argument deltas.
  // Those can arrive every presentation frame and used to make the task pill
  // reconcile while its entrance animation was still running. When expanded,
  // the detail panel remains fully live.
  if (next.open) return rowDetail(previous.row, previous.delta) === rowDetail(next.row, next.delta);

  const active = isActiveActivityStatus(nextStatus);
  if (!active && rowHasDetail(previous.row) !== rowHasDetail(next.row)) return false;
  if (!active && next.row.item.type === "file_edit" && previous.row.item.diff !== next.row.item.diff) return false;
  return true;
}

const ActivityRow = memo(function ActivityRow({ row, open, workspace, copy, delta, onToggle }: ActivityRowProps) {
  const status = rowStatus(row);
  const active = isActiveActivityStatus(status);
  const executing = isExecutingActivityStatus(status);
  const born = useBornLive(row.key);
  // A row that finishes while the user watches confirms once, in its outcome
  // colour. The marker lives on the row, so nothing else can replay it.
  const live = useContext(LiveSequenceContext);
  const previousStatusRef = useRef(status);
  const [settled, setSettled] = useState<"done" | "failed" | null>(null);
  useLayoutEffect(() => {
    const previous = previousStatusRef.current;
    previousStatusRef.current = status;
    if (!live || previous === status || !isActiveActivityStatus(previous) || isActiveActivityStatus(status)) return;
    setSettled(isFailureStatus(status) ? "failed" : "done");
  }, [live, status]);
  useEffect(() => {
    if (!settled) return;
    const timer = window.setTimeout(() => setSettled(null), 760);
    return () => window.clearTimeout(timer);
  }, [settled]);
  const expandable = active || rowHasDetail(row);
  const detailPresence = useMotionPresence(open, 260);
  const cachedDetailRef = useRef("");
  const liveDetail = open ? rowDetail(row, delta) : "";
  if (open) cachedDetailRef.current = liveDetail;
  const visibleDetail = open ? liveDetail : cachedDetailRef.current;
  const description = describeRow(row, copy, delta);
  const stats = row.item.type === "file_edit" && (!active || open)
    ? (delta ? { added: delta.added, removed: delta.removed } : diffStats(row.item.diff))
    : null;
  const screenshotPaths = browserScreenshotPaths(row.item);
  const hintPresence = useMotionPresence(open && active && !visibleDetail && !screenshotPaths.length, 200);
  const identity = activityIdentity(identitySource(row));
  const kind = description.category === "command" ? "process" : description.category === "edit" ? "file_edit" : row.item.type;
  // A page, image or document being written can be watched while it grows.
  const previewPath = description.category === "edit" && workspace && !active
    ? visualArtifactPath(row.item.type === "file_edit" ? (delta?.paths ?? row.item.paths ?? []) : [])
    : "";

  return (
    <div
      className={`task-flow-row-wrap ${open ? "is-open" : ""} ${previewPath ? "has-preview" : ""}`.replace(/\s+/g, " ").trim()}
      data-kind={kind}
      data-row-key={row.key}
      data-born={born ? "live" : undefined}
      data-settled={settled ?? undefined}
    >
      <button
        type="button"
        className={`task-flow-row task-flow-kind-${kind} ${active ? "is-active" : "is-resting"} ${executing ? "is-executing" : ""} ${expandable ? "is-expandable" : "no-detail"} ${isFailureStatus(status) ? "is-failed" : ""}`.replace(/\s+/g, " ").trim()}
        data-tool-family={description.category === "command" ? "terminal" : description.category === "edit" ? "file" : identity.family}
        onClick={() => expandable && onToggle(row.key)}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        title={expandable ? (open ? copy.collapseDetails : copy.expandDetails) : undefined}
      >
        <span className="task-flow-sheen" aria-hidden="true"><i /></span>
        <span className="task-flow-row-icon" title={identity.label}><RowGlyph row={row} category={description.category} /></span>
        <span className="task-flow-row-main">
          {/* Verbs are keyed on their text so a tense change (正在运行 -> 已运行)
              remounts the span and cross-fades (conversation-motion.css). */}
          <span className="task-flow-verb" key={description.verb}>{description.verb}</span>
          <span
            className={`task-flow-primary ${description.code ? "code" : ""} ${description.category === "edit" || description.category === "read" ? "task-flow-path" : ""}`.replace(/\s+/g, " ").trim()}
            title={description.title}
          >
            {description.target}
          </span>
          {stats && (stats.added > 0 || stats.removed > 0) ? (
            <span className="task-flow-diffstat">
              {stats.added ? <span className="task-flow-plus">+{stats.added}</span> : null}
              {stats.removed ? <span className="task-flow-minus">-{stats.removed}</span> : null}
            </span>
          ) : null}
        </span>
        <ActivityStatus status={status} copy={copy} />
      </button>

      {previewPath ? (
        <button
          type="button"
          className="task-flow-row-preview"
          onClick={() => window.dispatchEvent(new CustomEvent("loom:artifact-preview-open", { detail: { path: previewPath, workspace } }))}
          title={copy.previewArtifactTitle(previewPath)}
          aria-label={copy.previewArtifact(artifactName(previewPath))}
        >
          <Eye size={12} strokeWidth={1.9} aria-hidden="true" />
          <span>{copy.preview}</span>
        </button>
      ) : null}

      {expandable ? (
        <div
          className={`task-flow-inline-detail-grid ${open ? "open" : ""}`}
          data-motion-phase={detailPresence.phase}
          inert={!open}
        >
          <div className="task-flow-inline-detail-inner">
            {detailPresence.mounted ? (
              <div className="task-flow-inline-detail">
                {screenshotPaths.length ? (
                  <BrowserScreenshotDetail paths={screenshotPaths} workspace={workspace} />
                ) : visibleDetail ? (
                  <pre>{visibleDetail}</pre>
                ) : null}
                {hintPresence.mounted ? (
                  <div className="tool-hint-presence" data-motion-phase={hintPresence.phase} inert={hintPresence.phase === "exiting"}>
                  <div className="tool-hint-presence-inner"><div className="task-flow-live-detail" role="status">
                    <span className="task-flow-live-detail-glow" aria-hidden="true" />
                    <span>{copy.liveHint(liveHintKind(row, status))}</span>
                  </div></div></div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}, sameActivityRowProps);

function activitySummary(items: TranscriptItem[]): ActivitySummaryData {
  const activityItems: TranscriptItem[] = [];
  let failed = false;

  for (const item of items) {
    if (isActivityItem(item)) activityItems.push(item);
    if (!failed && (item.type === "error" || isFailureStatus(itemStatus(item)))) failed = true;
  }

  return {
    steps: buildActivityRows(activityItems).length,
    failed,
  };
}

function groupCategories(rows: ActivityRowModel[], copy: RuntimeCopy, deltas: ReadonlyMap<string, FileEditDelta>): ActivityCategory[] {
  const categories: ActivityCategory[] = [];
  for (const row of rows) {
    const category = describeRow(row, copy, deltas.get(row.item.id)).category;
    if (!categories.includes(category)) categories.push(category);
  }
  return categories;
}

function ActivityGroupIcon({ items }: { items: TranscriptItem[] }) {
  const identity = activityGroupIdentity(items);
  if (identity.family === "terminal") return <Terminal size={14} />;
  if (identity.family === "file") return <FileDiff size={14} />;
  if (identity.family === "generic") return <Wrench size={14} />;
  return <ActivityGroupGlyph items={items} size={14} />;
}

interface ActivityFlowProps {
  items: TranscriptItem[];
  keepOpen?: boolean;
  continuing?: boolean;
  workspace?: string;
}

/** Grouping rebuilds the array on every delta; the group only changes with its items. */
function sameActivityFlowProps(previous: ActivityFlowProps, next: ActivityFlowProps): boolean {
  return previous.keepOpen === next.keepOpen
    && previous.continuing === next.continuing
    && previous.workspace === next.workspace
    && sameItemReferences(previous.items, next.items);
}

const ActivityFlow = memo(function ActivityFlow({
  items,
  keepOpen = false,
  continuing = false,
  workspace,
}: ActivityFlowProps) {
  const copy = useRuntimeCopy();
  const deltas = useContext(FileEditDeltaContext);
  const rows = useMemo(() => buildActivityRows(items), [items]);
  const born = useBornLive(`group:${items[0]?.id ?? ""}`);
  const running = keepOpen;
  const hasActiveRows = rows.some((row) => isActiveActivityStatus(rowStatus(row)));
  // The latest group of a live turn stays the motion anchor between tool
  // batches (so rows appended later still animate), but it only *looks* busy
  // while a row is genuinely active. The quiet gap belongs to the thinking
  // capsule below the group, not to a second "continuing" label here.
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

  const categories = useMemo(() => groupCategories(rows, copy, deltas), [copy, deltas, rows]);
  const title = copy.groupTitle(categories, running && !betweenSteps);
  const groupItems = useMemo(() => rows.map(identitySource), [rows]);
  const groupIdentity = activityGroupIdentity(groupItems);

  return (
    <section
      className={`task-flow task-flow-group ${open ? "is-open" : ""} ${running ? "is-running" : ""} ${betweenSteps ? "is-between-steps" : ""} ${unfolding ? "is-unfolding" : ""}`.replace(/\s+/g, " ").trim()}
      data-tool-family={groupIdentity.family}
      data-born={born ? "live" : undefined}
      aria-label={copy.activityRegion}
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
        <span className="task-flow-group-icon" aria-hidden="true" title={groupIdentity.label}><ActivityGroupIcon items={groupItems} /></span>
        <span className="task-flow-group-title" key={title}>{title}</span>
        <ChevronRight size={13} className="task-flow-group-chevron" aria-hidden="true" />
      </button>

      <div className="task-flow-group-grid">
        <div className="task-flow-group-inner">
          <div className="task-flow-list">
            {rows.map((row) => (
              <ActivityRow
                key={row.key}
                row={row}
                open={openRows.has(row.key)}
                workspace={workspace}
                copy={copy}
                delta={row.item.type === "file_edit" ? deltas.get(row.item.id) : undefined}
                onToggle={toggleRow}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}, sameActivityFlowProps);

function SubAgentActivityNotice({ items }: { items: TranscriptItem[] }) {
  const copy = useRuntimeCopy();
  const spawned = items.filter((item) => String(item.toolName || "") === "spawn_agent");
  const count = spawned.length || items.length;
  const running = items.some((item) => isActiveActivityStatus(itemStatus(item)));

  return (
    <button
      type="button"
      className={`sub-agent-inline-notice ${running ? "is-running" : ""}`}
      onClick={() => window.dispatchEvent(new Event("loom:sub-agents-open"))}
      title={copy.subAgentsTitle}
    >
      <span className="sub-agent-inline-icon" aria-hidden="true"><Bot size={13} /></span>
      <span className="sub-agent-inline-copy">
        {running ? copy.subAgentsRunning : copy.subAgentsUsed}
      </span>
      <span className="sub-agent-inline-count">{count}</span>
      <span className="sub-agent-inline-action">{copy.subAgentsOpen}</span>
      <ChevronRight size={12} aria-hidden="true" />
    </button>
  );
}

// Constructing an Intl formatter is expensive; toolbars render on every delta.
const TIME_FORMAT = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });

function messageTimestamp(item: TranscriptItem): string {
  const value = item.createdAt || item.updatedAt;
  if (!value) return "";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return "";
  return TIME_FORMAT.format(new Date(parsed));
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
  const copy = useRuntimeCopy();
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<MessageFeedback>(null);
  const time = messageTimestamp(item);
  const canCopy = Boolean(text.trim());

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copyText = async () => {
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
      <span className="message-actions" aria-label={kind === "user" ? copy.userActions : copy.assistantActions}>
        <button
          type="button"
          className={`message-action-button ${copied ? "is-copied" : ""}`}
          onClick={() => void copyText()}
          disabled={!canCopy}
          title={copied ? copy.copied : copy.copy}
          aria-label={copied ? copy.copied : copy.copyMessage}
        >
          {copied ? <Check size={14} strokeWidth={2.1} /> : <Copy size={14} strokeWidth={1.75} />}
        </button>

        <button
          type="button"
          className="message-action-button message-quote-action"
          onClick={quote}
          disabled={disabled || !canCopy}
          title={kind === "assistant" ? copy.quoteAnswer : copy.quoteMessage}
          aria-label={kind === "assistant" ? copy.quoteAnswerLabel : copy.quoteMessageLabel}
        >
          <Reply size={14} strokeWidth={1.75} />
        </button>

        {kind === "user" ? (
          <button
            type="button"
            className="message-action-button"
            onClick={edit}
            disabled={!editable || disabled || !canCopy}
            title={copy.editMessage}
            aria-label={copy.editMessageLabel}
          >
            <Pencil size={14} strokeWidth={1.75} />
          </button>
        ) : (
          <>
            <button
              type="button"
              className={`message-action-button ${feedback === "up" ? "active" : ""}`}
              onClick={() => setFeedback((current) => (current === "up" ? null : "up"))}
              title={copy.helpful}
              aria-label={copy.helpfulLabel}
              aria-pressed={feedback === "up"}
            >
              <ThumbsUp size={14} strokeWidth={1.75} />
            </button>
            <button
              type="button"
              className={`message-action-button ${feedback === "down" ? "active" : ""}`}
              onClick={() => setFeedback((current) => (current === "down" ? null : "down"))}
              title={copy.notHelpful}
              aria-label={copy.notHelpfulLabel}
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

interface AssistantMessageProps {
  item: TranscriptItem;
  streaming: boolean;
  onPrompt?(prompt: string): Promise<void> | void;
  decisionInteractive: boolean;
  promptDisabled?: boolean;
  workspace?: string;
}

function AssistantMessage({ item, streaming, onPrompt, decisionInteractive, promptDisabled, workspace }: AssistantMessageProps) {
  const copy = useRuntimeCopy();
  const parsed = splitReasoning(item.text ?? "");
  const providerReasoning = String(item.reasoning ?? "").trim();
  // New providers stream reasoning on its own field. Legacy tag parsing remains
  // only as a compatibility fallback for models that embed thinking in text.
  const reasoning = providerReasoning || parsed.reasoning;
  const interrupted = ["interrupted", "cancelled", "failed"].includes(item.status || "");
  const live = streaming && !interrupted && (item.status === "streaming" || isActiveActivityStatus(item.status || "running"));
  const decisionMessage = parseDecisionMessage(parsed.answer, live);
  const answer = decisionMessage.text.trim();
  // The model is still thinking about this message: nothing to read yet.
  const thinking = live && !answer && !decisionMessage.decisions.length;
  const showReasoning = thinking || Boolean(reasoning);
  // Only a live message can retire its thinking header (answer arrived without
  // any reasoning to disclose); history never pays for presence bookkeeping.
  const retiring = useMotionPresence(live && showReasoning, 240);
  // A message whose first frame already has text, while the thinking capsule
  // is on screen, continues that capsule as its header and folds it away above
  // the first line, instead of letting the capsule vanish under the text.
  const handle = useContext(ThinkingHandleContext);
  const [inheritsCapsule, setInheritsCapsule] = useState(() => live && !showReasoning && thinkingOnScreen(handle));
  useEffect(() => {
    if (!inheritsCapsule) return;
    const timer = window.setTimeout(() => setInheritsCapsule(false), 260);
    return () => window.clearTimeout(timer);
  }, [inheritsCapsule]);
  const reasoningExiting = !showReasoning && (retiring.mounted || inheritsCapsule);

  if (!showReasoning && !reasoningExiting && !answer && !decisionMessage.decisions.length && !decisionMessage.incomplete) return null;

  return (
    <div
      className={`message-shell assistant-message-shell ${thinking ? "is-thinking" : ""}`.trim()}
      data-message-id={item.id}
      data-loom-message-kind="assistant"
      data-loom-message-text={answer || reasoning}
    >
      <div className="assistant-message">
        {showReasoning || reasoningExiting ? (
          <div
            className={`live-reasoning-presence ${inheritsCapsule && !showReasoning ? "is-inherited" : ""}`.trim()}
            data-motion-phase={reasoningExiting ? "exiting" : "entered"}
            inert={reasoningExiting}
          >
            <div className="live-reasoning-presence-inner">
              <ReasoningBlock
                reasoning={reasoning}
                thinking={thinking || (inheritsCapsule && !showReasoning)}
                live={live}
                workspace={workspace}
                messageKey={`${item.id}:reasoning`}
                interrupted={interrupted}
              />
            </div>
          </div>
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
              ? () => onPrompt(copy.decisionRetryPrompt)
              : undefined}
          />
        ) : null}
      </div>
      {thinking || decisionMessage.decisions.length || decisionMessage.incomplete || (!answer && !reasoning) ? null : (
        <MessageToolbar kind="assistant" item={item} text={answer || reasoning} disabled={promptDisabled} />
      )}
    </div>
  );
}

function ApprovalCard({ item, onApproval }: { item: TranscriptItem; onApproval(item: TranscriptItem, approved: boolean): void }) {
  const copy = useRuntimeCopy();
  return (
    <div className="approval-card">
      <div className="approval-icon"><CircleAlert size={16} /></div>
      <div className="approval-main">
        <div className="approval-title">{copy.approvalTitle}</div>
        <div className="approval-copy">{item.toolName || copy.approvalTool}{item.reason ? ` · ${item.reason}` : ""}</div>
      </div>
      <div className="approval-actions">
        <button className="button secondary" onClick={() => onApproval(item, false)}>{copy.deny}</button>
        <button className="button primary" onClick={() => onApproval(item, true)}><Check size={14} /> {copy.allow}</button>
      </div>
    </div>
  );
}

/** A turn's ending: a failure reads as an error, the user's own stop does not. */
function ErrorRow({ item }: { item: TranscriptItem }) {
  const copy = useRuntimeCopy();
  const ending = copy.turnEnding(String(item.error || ""));
  const stopped = ending.tone === "stopped";
  return (
    <div className={`error-row ${stopped ? "is-stopped" : ""}`.trim()} role={stopped ? "status" : "alert"}>
      <span className="error-icon">{stopped ? <CircleStop size={14} /> : <CircleAlert size={14} />}</span>
      <span>{ending.text}</span>
    </div>
  );
}

// Items are immutable snapshots: an unchanged item (same object) renders the
// same view, so a delta on the live message does not re-render its neighbours.
const ItemView = memo(function ItemView({
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
    return (
      <AssistantMessage
        item={item}
        streaming={streaming}
        onPrompt={onPrompt}
        decisionInteractive={decisionInteractive}
        promptDisabled={promptDisabled}
        workspace={workspace}
      />
    );
  }

  if (item.type === "approval") return <ApprovalCard item={item} onApproval={onApproval} />;

  if (item.type === "error") return <ErrorRow item={item} />;

  return null;
});

function Sequence({
  items,
  onApproval,
  onPrompt,
  keepActivityOpen = false,
  active = false,
  promptDisabled,
  workspace,
  handoff,
}: {
  items: TranscriptItem[];
  onApproval(item: TranscriptItem, approved: boolean): void;
  onPrompt?(prompt: string): Promise<void> | void;
  keepActivityOpen?: boolean;
  active?: boolean;
  promptDisabled?: boolean;
  workspace?: string;
  handoff?: { retained: ReadonlySet<string>; folding: ReadonlySet<string> };
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
  // latest activity group as the motion anchor in a quiet between-steps state
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

  const envelope = (key: string, ids: string[], content: ReactNode) => handoff ? (
    <div key={key} className="process-handoff-slot" data-process-items={ids.join(" ")}
      data-handoff-phase={ids.every(id => handoff.folding.has(id)) ? "folding"
        : ids.some(id => handoff.retained.has(id)) ? "holding" : "current"}
      inert={ids.every(id => handoff.folding.has(id))}>
      <div className="process-handoff-slot-inner">{content}</div>
    </div>
  ) : <Fragment key={key}>{content}</Fragment>;

  return (
    <LiveSequenceContext.Provider value={active}>
      {subAgentItems.length ? (
        envelope("sub-agent-workspace", subAgentItems.map(item => item.id),
        <div className="transcript-entry entry-sub-agent-workspace">
          <SubAgentActivityNotice items={subAgentItems} />
        </div>
        )
      ) : null}

      {blocks.map((block, index) => (
        block.kind === "activity" ? (deferredActivityBlocks.has(index) ? null : (
          envelope(`activity-${block.items[0]?.id ?? index}`, block.items.map(item => item.id),
          <div className={`transcript-entry entry-activity ${active ? "has-lifecycle-motion" : ""}`} key={`activity-${block.items[0]?.id ?? index}`}>
            <ActivityFlow
              items={block.items}
              keepOpen={activeActivityBlocks.has(index) || continuingActivityBlock === index}
              continuing={continuingActivityBlock === index}
              workspace={workspace}
            />
          </div>
          )
        )) : (
          envelope(block.item.id, [block.item.id],
          <div
            className={`transcript-entry entry-${block.item.type} ${active ? "has-lifecycle-motion" : ""} ${isSteeringUserMessage(block.item) ? "entry-steering-user" : ""}`.trim()}
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
        )
      ))}
    </LiveSequenceContext.Provider>
  );
}

function parseTimestamp(value: unknown): number | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function elapsedSeconds(items: TranscriptItem[]): number | null {
  let earliest = Number.POSITIVE_INFINITY;
  let latest = Number.NEGATIVE_INFINITY;

  for (const item of items) {
    const start = parseTimestamp(item.createdAt);
    const end = parseTimestamp(item.updatedAt) ?? start;
    if (start !== null) earliest = Math.min(earliest, start);
    if (end !== null) latest = Math.max(latest, end);
  }

  if (!Number.isFinite(earliest) || !Number.isFinite(latest)) return null;
  return Math.max(1, (latest - earliest) / 1000);
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


/**
 * What the growth edge of a live turn is doing right now. The thinking capsule
 * only speaks for the quiet gaps: while text streams, a tool row runs or an
 * approval waits, those surfaces already say what Loom is doing.
 */
function liveEdgeState(items: TranscriptItem[]): { quiet: boolean; started: boolean } {
  let started = false;
  for (const item of items) {
    if (item.type === "user_message") continue;
    started = true;
    if (isActivityItem(item) && isActiveActivityStatus(itemStatus(item))) return { quiet: false, started };
    if (item.type === "approval" && isActiveActivityStatus(itemStatus(item))) return { quiet: false, started };
  }
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.type !== "assistant_message") continue;
    const status = itemStatus(item);
    const live = item.status === "streaming" || isActiveActivityStatus(status) || splitReasoning(item.text ?? "").state === "streaming";
    if (live) return { quiet: false, started };
    break;
  }
  return { quiet: true, started };
}

function TurnProcess({
  items,
  allItems,
  guidanceItems,
  active,
  settle,
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
  settle: SettlePhase;
  open: boolean;
  onOpenChange(open: boolean): void;
  onApproval(item: TranscriptItem, approved: boolean): void;
  onPrompt?(prompt: string): Promise<void> | void;
  promptDisabled?: boolean;
  workspace?: string;
}) {
  const copy = useRuntimeCopy();
  // A just-completed turn keeps its live layout until the fold has finished,
  // so completion never re-expands the earlier history for a frame.
  const live = active || settle !== null;
  const summary = useMemo(() => activitySummary(items), [items]);
  const breakdown = useMemo(() => turnProcessBreakdown(items), [items]);
  const intermediateMessages = useMemo(
    () => items.reduce((count, item) => count + (item.type === "assistant_message" ? 1 : 0), 0),
    [items],
  );
  const operationCount = summary.steps + intermediateMessages;
  const summaryLabel = useMemo(
    () => copy.processSummary(breakdown, operationCount),
    [breakdown, copy, operationCount],
  );
  const processPresence = useMotionPresence(live || open, TURN_FOLD_MS);
  const [earlierOpen, setEarlierOpen] = useState(false);
  // Completed text has its own presentation snapshots while the fold is visible.
  const earlierPresence = useMotionPresence(earlierOpen, 280);
  const earlierHistoryId = useId();
  const progress = useMemo(() => liveTaskProgress(items, new Set(items
    .filter((item) => {
      if (item.type !== "assistant_message") return false;
      const parsed = parseDecisionMessage(item.text ?? "");
      return parsed.decisions.length > 0 || parsed.incomplete;
    })
    .map((item) => item.id))), [items]);
  const pendingPresentations = usePendingPresentations();
  // The finished live layout is held through the settle fold, including records
  // still retained in place, so nothing disappears at the moment of completion.
  const handoff = useEarlierProcessHandoff(items, progress, live, earlierOpen, pendingPresentations, processHandoffGroups);
  const earlierEntry = useMotionPresence(live && progress.earlier.length > 0, 200);

  // The standalone thinking capsule: shown at once while the turn has produced
  // nothing yet, and after a short grace during later quiet gaps.
  const edge = useMemo(() => liveEdgeState(items), [items]);
  const wantsCapsule = active && edge.quiet && pendingPresentations.size === 0;
  const capsuleVisible = useSettledFlag(wantsCapsule, edge.started ? LIVE_STATUS_GRACE_MS : 0);
  const capsulePresence = useMotionPresence(capsuleVisible, 240);
  // A message that starts thinking takes the capsule's place in the same
  // frame: the capsule leaves without an exit so nothing is drawn twice.
  const handedOff = active && !edge.quiet && items.some((item) => item.type === "assistant_message"
    && (item.status === "streaming" || isActiveActivityStatus(itemStatus(item))));
  const handedOffRef = useRef(false);
  if (handedOff) handedOffRef.current = true;
  if (capsuleVisible) handedOffRef.current = false;
  const renderCapsule = active && capsulePresence.mounted && !handedOff
    && !(capsulePresence.phase === "exiting" && handedOffRef.current);

  // Retain pixels through exit/reversal, then release hidden React trees. A
  // visited long conversation must return to its original folded DOM budget.
  const renderProcessContent = live || open || processPresence.mounted;

  return (
    <section
      className={`turn-process ${live ? "is-live" : "is-settled"} ${settle ? `is-settling is-settle-${settle}` : ""} ${open ? "is-open" : ""} ${guidanceItems.length ? "has-guidance" : ""}`.replace(/\s+/g, " ").trim()}
    >
      {!active ? (
        <div className="turn-process-header-shell">
          <div className="turn-process-header-inner">
            <button
              type="button"
              className="turn-process-header"
              onClick={() => onOpenChange(!open)}
              aria-expanded={open}
              title={copy.processToggleTitle(open)}
              tabIndex={settle === "hold" ? -1 : undefined}
            >
              <span className="turn-process-primary">
                <span className="turn-process-summary">{summaryLabel}</span>
                {(breakdown.added > 0 || breakdown.removed > 0) ? (
                  <span className="turn-process-diffstat" aria-label={copy.diffLabel(breakdown.added, breakdown.removed)}>
                    {breakdown.added ? <span className="turn-process-plus">+{breakdown.added}</span> : null}
                    {breakdown.removed ? <span className="turn-process-minus">-{breakdown.removed}</span> : null}
                  </span>
                ) : null}
                <span className="turn-process-time">{copy.elapsed(elapsedSeconds(allItems))}</span>
              </span>
              <ChevronRight size={14} className="turn-process-chevron" aria-hidden="true" />
            </button>
          </div>
        </div>
      ) : null}

      {!live && !open && guidanceItems.length ? (
        <div className="turn-guidance-recap" aria-label={copy.guidanceRecap}>
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
              {live && earlierEntry.mounted ? (
                <div className="earlier-process-entry" data-motion-phase={earlierEntry.phase} inert={!progress.earlier.length}>
                <div className={`earlier-task-process ${earlierOpen ? "is-open" : ""} ${handoff.folding.size ? "is-receiving" : ""}`.trim()}>
                  <button type="button" className="earlier-process-toggle" aria-expanded={earlierOpen}
                    aria-controls={earlierHistoryId}
                    aria-label={copy.earlierToggleLabel(earlierOpen, progress.earlier.length)}
                    title={copy.earlierToggleTitle(earlierOpen)}
                    onClick={() => {
                      setEarlierOpen(!earlierOpen);
                    }}>
                    <span className="earlier-process-icon" aria-hidden="true"><History size={14} strokeWidth={1.8} /></span>
                    <span className="earlier-process-label">{copy.earlier}</span>
                    <span className="earlier-process-count" key={progress.earlier.length} aria-hidden="true">{copy.earlierCount(progress.earlier.length)}</span>
                    <ChevronRight size={13} className="earlier-process-chevron" aria-hidden="true" />
                  </button>
                  <div id={earlierHistoryId} className="earlier-process-history" data-motion-phase={earlierPresence.phase} inert={!earlierOpen}>
                    <div className="earlier-process-history-inner">
                    {earlierPresence.mounted ? <StreamingPresentation>
                      <Sequence items={handoff.earlier} active={false} onApproval={onApproval}
                        onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} />
                    </StreamingPresentation> : null}
                    </div>
                  </div>
                </div>
                </div>
              ) : null}
              <Sequence items={live ? handoff.current : items} handoff={live ? handoff : undefined} active={active} onApproval={onApproval} onPrompt={onPrompt} keepActivityOpen={active} promptDisabled={promptDisabled} workspace={workspace} />
              {renderCapsule ? (
                <div className="pending-thinking-presence" data-motion-phase={capsulePresence.phase} inert={!capsuleVisible}>
                  <div className="pending-thinking-presence-inner"><ThinkingCapsule /></div>
                </div>
              ) : null}
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
  // The slot owns the unfold at completion; the card keeps its own surface.
  return (
    <div className="turn-artifacts-slot">
      <div className="turn-artifacts-slot-inner"><TurnArtifactsPreview items={items} workspace={workspace} /></div>
    </div>
  );
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

    for (const item of orderedItems) {
      if (!active && item.type === "error") errorItems.push(item);
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
      finalAssistant,
      processItems,
      hasProcess,
    };
  }, [active, items]);

  const [processOpen, setProcessOpen] = useState(active);
  // Sending owns its full animation even if the runtime completes immediately.
  // History mounts inactive, so opening a past turn does not launch its bubble.
  const reduce = useReducedMotion();
  const [sending, setSending] = useState(active && !reduce);
  const [settle, setSettle] = useState<SettlePhase>(null);
  const wasActiveRef = useRef(active);

  useLayoutEffect(() => {
    if (reduce) setSending(false);
    if (active) {
      setProcessOpen(true);
      setSettle(null);
    } else if (wasActiveRef.current && !reduce && !document.hidden) {
      // Only a live -> complete transition owns completion motion. Historical
      // turns mount already settled and therefore never replay the hand-off:
      // hold the finished live layout for a beat, fold it into its summary,
      // then swap the (now hidden) content for the full history.
      wasActiveRef.current = active;
      setSettle("hold");
      const foldTimer = window.setTimeout(() => {
        setSettle("fold");
        setProcessOpen(false);
      }, TURN_SETTLE_HOLD_MS);
      const settleTimer = window.setTimeout(() => setSettle(null), TURN_SETTLE_HOLD_MS + TURN_FOLD_MS);
      return () => {
        window.clearTimeout(foldTimer);
        window.clearTimeout(settleTimer);
      };
    } else {
      setSettle(null);
      if (wasActiveRef.current || reduce) setProcessOpen(false);
    }
    wasActiveRef.current = active;
  }, [active, reduce]);

  const fileDeltas = useMemo(() => fileEditDeltas(derived.orderedItems), [derived.orderedItems]);
  const thinkingHandle = useRef<ThinkingHandle>({ element: null, mountedAt: 0 }).current;
  const settling = settle !== null;

  // Completion is one motion: while the finished layout holds, nothing new
  // takes space; when the process folds, the answer's toolbar and the changed
  // files unfold in the same beat (turn-flow.css), so the height the fold
  // gives back is what they take and the viewport barely travels.
  return (
    <StreamingPresentation>
    <ThinkingHandleContext.Provider value={thinkingHandle}>
    <FileEditDeltaContext.Provider value={fileDeltas}>
    <section className={`turn-block ${active ? "is-active" : "is-complete"} ${settling ? `is-settling is-settle-${settle}` : ""}`.replace(/\s+/g, " ").trim()} data-turn-id={turnId}>
      {derived.initialUser ? (
        <div className={`transcript-entry entry-user_message ${sending ? "is-sending" : ""}`} key={derived.initialUser.clientMessageId || derived.initialUser.id}
          onAnimationEnd={(event) => {
            if (event.target === event.currentTarget && event.animationName === "loom-user-send-entry") setSending(false);
          }}>
          <ItemView item={derived.initialUser} onApproval={onApproval} onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} />
        </div>
      ) : null}

      {derived.hasProcess || active ? (
        <TurnProcess
          items={derived.processItems}
          allItems={derived.orderedItems}
          guidanceItems={derived.guidanceItems}
          active={active}
          settle={settle}
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

      {!active ? derived.errorItems.map((item) => (
        <div className="transcript-entry entry-error" key={item.id}>
          <ItemView item={item} onApproval={onApproval} onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} />
        </div>
      )) : null}

      {!active ? <TurnArtifacts items={items} workspace={workspace} /> : null}
    </section>
    </FileEditDeltaContext.Provider>
    </ThinkingHandleContext.Provider>
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

export const Transcript = memo(function Transcript({ items, running, currentTurnId, workspace, promptDisabled, onPrompt, onApproval, onOpenInsights }: TranscriptProps) {
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
              key={block.items.find((item) => item.type === "user_message" && item.source !== "steering")?.clientMessageId || block.id}
              turnId={block.id}
              items={block.items}
              active={Boolean(running && (block.id === activeTurnId || block.items.some((item) => item.status === "sending")))}
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
});
