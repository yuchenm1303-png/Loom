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
  History,
  Pencil,
  Reply,
  Search,
  ThumbsDown,
  ThumbsUp,
  Zap,
} from "./icons";
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
import { useI18n } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { useReducedMotion } from "../motion/useReducedMotion";
import { useEarlierProcessHandoff } from "./useEarlierProcessHandoff";
import { LIVE_STATUS_GRACE_MS, LIVE_TEXT_HOLD_MS, TURN_FOLD_MS, TURN_SETTLE_HOLD_MS } from "../presentationTiming";
import type { TranscriptItem } from "../types/loom";
import { groupExecutionSequence, isActivityItem, isProcessCommentary, reportIds, settleLiveText } from "./executionSequence";
import {
  activitySegments,
  buildActivityRows,
  fileEditDeltas,
  isActiveActivityStatus,
  isFailureStatus,
  itemStatus,
  rowTone,
  turnProcessBreakdown,
} from "./activityModel";
import { latestTaskPlan, liveTaskProgress } from "./liveTaskProgress";
import { isLongNote } from "./processNote";
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
import { FileEditDeltaContext, LiveSequenceContext, ProcessNotesContext, useBornLive } from "./transcriptContexts";
import { Beads, WeaveStage, type ProcessHandoff } from "./WeaveFlow";
import { useRuntimeCopy } from "./runtimeCopy";
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

function decisionMessageIds(items: TranscriptItem[]): Set<string> {
  return new Set(items.filter(item => item.type === "assistant_message"
    && item.phase === "commentary").filter(item => {
      const parsed = parseDecisionMessage(item.text ?? "");
      return parsed.decisions.length > 0 || parsed.incomplete;
    }).map(item => item.id));
}

const NO_MESSAGE_IDS: ReadonlySet<string> = new Set();

function processHandoffGroups(items: TranscriptItem[]): string[][] {
  const agents = items.filter(isSubAgentToolItem);
  return [...(agents.length ? [agents.map(item => item.id)] : []),
    ...activitySegments(buildActivityRows(items.filter(item => !isSubAgentToolItem(item))))
      .map(rows => [...new Set(rows.flatMap(row => [row.key, row.item.id]))])];
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

/** How many tool steps a turn took, and whether any step or the turn itself went wrong. */
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
  /** "note" renders work-log commentary as a quiet caption instead of a message. */
  variant?: "note";
  onPrompt?(prompt: string): Promise<void> | void;
  decisionInteractive: boolean;
  promptDisabled?: boolean;
  workspace?: string;
}

function AssistantMessage({ item, streaming, onPrompt, decisionInteractive, promptDisabled, workspace, variant }: AssistantMessageProps) {
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
  const note = variant === "note";
  const [noteOpen, setNoteOpen] = useState(false);
  const longNote = note && isLongNote(answer);
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
      className={`message-shell assistant-message-shell ${note ? "is-note" : ""} ${longNote && !noteOpen ? "is-clamped" : ""} ${thinking ? "is-thinking" : ""}`.replace(/\s+/g, " ").trim()}
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
      {thinking || decisionMessage.decisions.length || decisionMessage.incomplete || (!answer && !reasoning) ? null : note ? (
        longNote ? (
          <button type="button" className="note-toggle" aria-expanded={noteOpen} onClick={() => setNoteOpen((open) => !open)}>
            {noteOpen ? copy.noteCollapse : copy.noteExpand}
          </button>
        ) : null
      ) : (
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
  const ending = copy.turnEnding(String(item.error || item.text || ""));
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
  variant,
}: {
  item: TranscriptItem;
  streaming?: boolean;
  onApproval(item: TranscriptItem, approved: boolean): void;
  onPrompt?(prompt: string): Promise<void> | void;
  decisionInteractive?: boolean;
  promptDisabled?: boolean;
  workspace?: string;
  variant?: "note";
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
        variant={variant}
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
  messageIds = NO_MESSAGE_IDS,
  collapseEnded = false,
}: {
  items: TranscriptItem[];
  onApproval(item: TranscriptItem, approved: boolean): void;
  onPrompt?(prompt: string): Promise<void> | void;
  keepActivityOpen?: boolean;
  active?: boolean;
  promptDisabled?: boolean;
  workspace?: string;
  handoff?: ProcessHandoff;
  /** Commentary that answers the user directly; it stays an ordinary message. */
  messageIds?: ReadonlySet<string>;
  /**
   * Stages that are over show as one line. This is a property of the live layout, which a finished turn keeps
   * while it holds and folds, not of the turn being active: completion must not open them before the fold.
   */
  collapseEnded?: boolean;
}) {
  // The render in which a turn completes is not yet "settling" (the hold starts in a layout effect), so the flag
  // drops for one unpainted render. Latch it: a sequence that was live stays a live layout until it unmounts,
  // which is when the process has folded. A finished turn's log opened later is a fresh mount and starts open.
  const [liveLayout, setLiveLayout] = useState(collapseEnded);
  if (collapseEnded && !liveLayout) setLiveLayout(true);
  const subAgentItems = useMemo(() => items.filter(isSubAgentToolItem), [items]);
  const visibleItems = useMemo(
    () => subAgentItems.length ? items.filter((item) => !isSubAgentToolItem(item)) : items,
    [items, subAgentItems.length],
  );
  const decisionIds = useMemo(() => decisionMessageIds(visibleItems), [visibleItems]);
  // What the model says to the reader is body text. It stands between the tool groups, as its first
  // reply does, instead of sitting indented inside one of them.
  const bodyIds = useMemo(() => (messageIds.size ? new Set([...decisionIds, ...messageIds]) : decisionIds), [decisionIds, messageIds]);
  const blocks = useMemo(() => groupExecutionSequence(visibleItems, bodyIds), [visibleItems, bodyIds]);
  // The live slice starts at its newest commentary, so that note arrives with no tool
  // before it. It is still log narration: give it the note presentation from its first
  // frame instead of drawing a full message that later shrinks into the work log.
  const notes = useContext(ProcessNotesContext);
  const isLeadNote = (item: TranscriptItem) => isProcessCommentary(item, decisionIds) && !messageIds.has(item.id);
  const latestAssistantId = useMemo(() => {
    for (let index = visibleItems.length - 1; index >= 0; index -= 1) {
      if (visibleItems[index].type === "assistant_message") return visibleItems[index].id;
    }
    return "";
  }, [visibleItems]);
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
      if (block.items.some((item) => isActivityItem(item) && isActiveActivityStatus(itemStatus(item)))) live.add(index);
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

  // A note the reader asked for, drawn inside a stage. Stable, so a stage only re-renders with its own items.
  const renderNote = useCallback((item: TranscriptItem) => (
    <ItemView item={item} variant="note" streaming={item.id === liveAssistantId} onApproval={onApproval}
      onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} />
  ), [liveAssistantId, onApproval, onPrompt, promptDisabled, workspace]);

  const renderEntry = (item: TranscriptItem) => (
    <ItemView
      item={item}
      variant={isLeadNote(item) ? "note" : undefined}
      streaming={Boolean(active && item.type === "assistant_message" && item.id === liveAssistantId)}
      onApproval={onApproval}
      onPrompt={onPrompt}
      promptDisabled={promptDisabled}
      workspace={workspace}
    />
  );

  const envelope = (key: string, ids: string[], content: ReactNode, activity = false) => handoff && !activity ? (
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
          <div className="entry-body"><SubAgentActivityNotice items={subAgentItems} /></div>
        </div>
        )
      ) : null}

      {blocks.map((block, index) => (
        block.kind === "activity" ? (deferredActivityBlocks.has(index) ? null : (
          envelope(`activity-${block.items[0]?.id ?? index}`, block.items.map(item => item.id),
          <div className={`transcript-entry entry-activity ${active ? "has-lifecycle-motion" : ""}`} key={`activity-${block.items[0]?.id ?? index}`}>
            <WeaveStage
              items={block.items}
              keepOpen={activeActivityBlocks.has(index) || continuingActivityBlock === index}
              superseded={liveLayout && index < latestActivityBlockIndex}
              live={liveLayout}
              workspace={workspace}
              handoff={handoff}
              renderNote={renderNote}
            />
          </div>, true
          )
        )) : (isLeadNote(block.item) && !notes.show ? null : (
          envelope(block.item.id, [block.item.id],
          <div
            className={`transcript-entry entry-${block.item.type} ${active ? "has-lifecycle-motion" : ""} ${isSteeringUserMessage(block.item) ? "entry-steering-user" : ""} ${block.item.leaving ? "is-leaving" : ""} ${isLeadNote(block.item) ? `wv-note is-lead ${block.item.id === latestAssistantId ? "is-latest" : ""}` : ""}`.replace(/\s+/g, " ").trim()}
            key={block.item.id}
            inert={Boolean(block.item.leaving)}
          >
            {/* What arrives between tool groups folds in and out on a plain wrapper: a padded card cannot shrink past its own padding. */}
            {block.item.type === "approval" || block.item.type === "error" ? <div className="entry-body">{renderEntry(block.item)}</div> : renderEntry(block.item)}
          </div>
          )
        ))
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

/**
 * What a live turn draws: a sentence still streaming after tool work stays out of sight until
 * its role is known (see settleLiveText). The hold is bounded, so a slow stream still shows up,
 * and a sentence drawn that way stays a message after its step completes.
 */
function useSettledLiveText(items: TranscriptItem[], active: boolean): { items: TranscriptItem[]; released: ReadonlySet<string> } {
  const [released, setReleased] = useState<ReadonlySet<string>>(NO_MESSAGE_IDS);
  const settled = useMemo(() => (active ? settleLiveText(items, released) : { items, held: "" }), [active, items, released]);
  const { held } = settled;
  useEffect(() => {
    if (!held) return;
    const timer = window.setTimeout(() => setReleased((current) => new Set(current).add(held)), LIVE_TEXT_HOLD_MS);
    return () => window.clearTimeout(timer);
  }, [held]);
  return { items: settled.items, released };
}

/** The switch for the model's per-step narration. It is born once, the first time a note is held back, and grows into its place. */
function NotesSwitch({ turnKey, live, label, pressed, onToggle }: { turnKey: string; live: boolean; label: string; pressed: boolean; onToggle(): void }) {
  // It is rendered beside the live sequence, not inside it, so the turn says whether it is live.
  const born = useBornLive(`notes:${turnKey}`, live);
  return (
    <div className="process-notes-row" data-born={born ? "live" : undefined}>
      <div className="process-notes-row-inner">
        <button type="button" className="process-notes-toggle" aria-pressed={pressed} onClick={onToggle}>{label}</button>
      </div>
    </div>
  );
}

function TurnProcess({
  items: streamedItems,
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
  const { items, released } = useSettledLiveText(streamedItems, active);
  // A just-completed turn keeps its live layout until the fold has finished,
  // so completion never re-expands the earlier history for a frame.
  const live = active || settle !== null;
  const summary = useMemo(() => activitySummary(items), [items]);
  const breakdown = useMemo(() => turnProcessBreakdown(items, copy), [items, copy]);
  const intermediateMessages = useMemo(
    () => items.reduce((count, item) => count + (item.type === "assistant_message" ? 1 : 0), 0),
    [items],
  );
  const operationCount = summary.steps + intermediateMessages;
  // One bead per step, in order, coloured by how it went: the shape of the run at a glance.
  const stepTones = useMemo(() => buildActivityRows(items.filter(isActivityItem)).map(rowTone), [items]);
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
  // Commentary that is a message to the reader: the reply to them, the model's own plan reports, long
  // sentences, and one now and then so a long run is never silent. The rest is log detail, hidden unless asked for.
  const messageIds = useMemo(() => reportIds(items, released), [items, released]);
  const decisionIds = useMemo(() => decisionMessageIds(items), [items]);
  const [showNotes, setShowNotes] = useState(false);
  const isQuietNote = useCallback(
    (item: TranscriptItem) => isProcessCommentary(item, decisionIds) && !messageIds.has(item.id) && String(item.text ?? "").trim().length > 0,
    [decisionIds, messageIds],
  );
  const noteCount = useMemo(() => items.reduce((count, item) => count + (isQuietNote(item) ? 1 : 0), 0), [items, isQuietNote]);
  const notesValue = useMemo(() => ({ show: showNotes }), [showNotes]);
  // Hidden narration draws nothing, so it must not count as a message that is "speaking".
  const shownItems = useMemo(() => (showNotes ? items : items.filter((item) => !isQuietNote(item))), [items, showNotes, isQuietNote]);
  const pendingPresentations = usePendingPresentations();
  // The finished live layout is held through the settle fold, including records
  // still retained in place, so nothing disappears at the moment of completion.
  const handoff = useEarlierProcessHandoff(items, progress, live, earlierOpen, pendingPresentations, processHandoffGroups);
  const earlierShown = useMemo(
    () => (showNotes ? handoff.earlier.length : handoff.earlier.filter((item) => !isQuietNote(item)).length),
    [handoff.earlier, showNotes, isQuietNote],
  );
  const earlierEntry = useMotionPresence(live && earlierShown > 0, 200);

  // The standalone thinking capsule: shown at once while the turn has produced
  // nothing yet, and after a short grace during later quiet gaps.
  const edge = useMemo(() => liveEdgeState(shownItems), [shownItems]);
  const wantsCapsule = active && edge.quiet && pendingPresentations.size === 0;
  const capsuleVisible = useSettledFlag(wantsCapsule, edge.started ? LIVE_STATUS_GRACE_MS : 0);
  const capsulePresence = useMotionPresence(capsuleVisible, 240);
  // A message that starts thinking takes the capsule's place in the same
  // frame: the capsule leaves without an exit so nothing is drawn twice.
  const handedOff = active && !edge.quiet && shownItems.some((item) => item.type === "assistant_message"
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
    <ProcessNotesContext.Provider value={notesValue}>
    <section
      ref={handoff.rootRef}
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
                {stepTones.length ? <Beads tones={stepTones} max={14} animated={settle !== null} /> : null}
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
        <div className="turn-process-grid" data-motion-phase={processPresence.phase}>
          <div className="turn-process-inner">
            <div className="turn-process-content">
              {noteCount > 0 ? (
                <NotesSwitch turnKey={allItems[0]?.id ?? ""} live={active} label={showNotes ? copy.notesHide : copy.notesShow(noteCount)} pressed={showNotes} onToggle={() => setShowNotes((value) => !value)} />
              ) : null}
              {live && earlierEntry.mounted ? (
                <div className="earlier-process-entry" data-motion-phase={earlierEntry.phase} inert={!earlierShown}>
                <div className={`earlier-task-process ${earlierOpen ? "is-open" : ""} ${handoff.folding.size ? "is-receiving" : ""}`.trim()}>
                  <button type="button" className="earlier-process-toggle" aria-expanded={earlierOpen}
                    aria-controls={earlierHistoryId}
                    aria-label={copy.earlierToggleLabel(earlierOpen, earlierShown)}
                    title={copy.earlierToggleTitle(earlierOpen)}
                    onClick={() => {
                      setEarlierOpen(!earlierOpen);
                    }}>
                    <span className="earlier-process-icon" aria-hidden="true"><History size={14} strokeWidth={1.8} /></span>
                    <span className="earlier-process-label">{copy.earlier}</span>
                    <span className="earlier-process-count" key={earlierShown} aria-hidden="true">{copy.earlierCount(earlierShown)}</span>
                    <ChevronRight size={13} className="earlier-process-chevron" aria-hidden="true" />
                  </button>
                  <div id={earlierHistoryId} className="earlier-process-history" data-motion-phase={earlierPresence.phase} inert={!earlierOpen}>
                    <div className="earlier-process-history-inner">
                    {earlierPresence.mounted ? <StreamingPresentation>
                      <Sequence items={handoff.earlier} active={false} messageIds={messageIds} onApproval={onApproval}
                        onPrompt={onPrompt} promptDisabled={promptDisabled} workspace={workspace} />
                    </StreamingPresentation> : null}
                    </div>
                  </div>
                </div>
                </div>
              ) : null}
              <Sequence items={live ? handoff.current : items} handoff={live ? handoff : undefined} active={active} collapseEnded={live} messageIds={messageIds} onApproval={onApproval} onPrompt={onPrompt} keepActivityOpen={active} promptDisabled={promptDisabled} workspace={workspace} />
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
    </ProcessNotesContext.Provider>
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
      // A motion preference change must preserve a manually opened history.
      if (wasActiveRef.current) setProcessOpen(false);
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasItems = items.length > 0;
  // The stage draws its edge fades once there is a transcript. Publishing that as an
  // attribute replaces `.conversation-stage:has(.transcript-entry)`, a :has() anchored on
  // this large container that was re-evaluated on every DOM change below it.
  useLayoutEffect(() => {
    const stage = scrollRef.current?.closest<HTMLElement>(".conversation-stage");
    if (!stage || !hasItems) return;
    stage.dataset.hasEntries = "";
    return () => { delete stage.dataset.hasEntries; };
  }, [hasItems]);
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
    <div ref={scrollRef} className={`transcript-scroll ${!items.length ? "is-empty" : ""}`}>
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
