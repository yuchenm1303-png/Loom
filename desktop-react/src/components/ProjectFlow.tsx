/* Persistent-project mode: the composer as a hub, after shawn's agent-composer sketch.
 *
 * What the project draws on plugs into the composer from the left (repository, scope,
 * knowledge), the agents working on it hang off the right, the plan of the current
 * milestone hangs below, and the conversation reads above. Cards are joined to the
 * composer by thick cables of the cards' own material, so they read as plugged in.
 * Cables are measured from the rendered cards and follow any width.
 *
 * The composer is the conversation's: what is typed goes to the conversation's agent, which
 * answers it like chat and can propose a project; a proposal shows above the hub and the
 * person starts it there. While a project runs the agent reads and steers it.
 * Motion means an event, and nothing moves otherwise:
 *   an agent finishes a step     one pulse runs down its cable into the composer
 *   an agent arrives / leaves    its card fades in / out and its cable grows / unplugs
 *   a task changes state         its mark is born again (spring)
 *   a card arrives / leaves    the cards around it glide out of its way, cables in tow (projectArrange.ts)
 * Text only fades; marks and cables move (motion-tokens.css). Styles: project-flow.css.
 * The data comes from projectOverview.ts (projectRun/overview); this file only draws it. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowDown, ArrowUp, Bot, BookMarked, ChartGantt, Check, Circle, CircleDot, Compass, Copy, File, FileCode, FileImage,
  FileSpreadsheet, FileText, FolderOpen, GitBranch, Hammer, LoaderCircle, Minus, Paperclip, Pause, Play, Plus, ShieldAlert, ShieldCheck,
  History, ListChecks, Search, Sparkles, Square, X, Gauge, RefreshCw, ExternalLink,
} from "lucide-react";
import { BuildHistory, type BuildHistoryEntry } from "./BuildHistory";
import { NewConversationRow } from "./NewConversationRow";
import { MarkdownMessage } from "./MarkdownMessage";
import { ApprovalCard, ErrorRow } from "./Transcript";
import { ThinkingGlyph } from "./ThinkingGlyph";
import { WeaveStage } from "./WeaveFlow";
import { ProcessNotesContext } from "./transcriptContexts";
import { isExecutingActivityStatus, itemStatus } from "./activityModel";
import { ProviderMark } from "./ModelPanel";
import { UsageRing, resetText, usageDetail, usageScopeText, usageWindowLabel, useModelUsage } from "./ModelUsageIndicator";
import type { FlowMessage, WeaveProposal } from "./projectOverview";
import { DUR, EASE } from "../motion/tokens";
import { glideProgress, HUB_CLOCK, HUB_EASE, LEAVE_RETAIN, useGlide, visibleBox } from "./projectArrange";
import { ReasoningControl, reasoningLabel } from "./ReasoningControl";
import { ComposerAttachmentStrip } from "./ComposerAttachmentStrip";
import {
  MAX_COMPOSER_ATTACHMENTS, appendComposerAttachments, attachmentFromPath, releaseAttachmentPreview, resolveComposerFiles,
} from "./composerAttachments";
import { imageAttachmentState } from "./modelInput";
import { UserMessageContent, optimisticUserMessageContent } from "./UserMessageContent";
import type { ApprovalDecision, Attachment, ModelReasoningState, TranscriptItem } from "../types/loom";
import { shortPath } from "./ProjectWorkspacePicker";
import { prefersReducedMotionNow, useReducedMotion } from "../motion/useReducedMotion";
import "./composer.css";
import "./composer-attachment-polish.css";
import "./project-flow.css";
import "./project-folder.css";

export type FlowTaskState = "pending" | "working" | "review" | "rework" | "integrated";

export interface FlowAgent {
  id: string;
  role: "planner" | "worker" | "reviewer";
  name: string;
  activity: string;
  step: number;
  elapsed: string;
  done?: boolean;
  /** Waits for the person to allow a call. */
  waiting?: boolean;
}

export interface FlowData {
  /** draft: nothing started yet, the composer takes the objective. */
  phase: "draft" | "live";
  objective: string;
  narration: { summary: string; text: string };
  milestone: number;
  state: "running" | "paused" | "needs_you" | "completed" | "starting";
  attention?: { reason: string; detail?: string; action?: "recover" | "budget" | "approval" | "resume"; agentId?: string };
  model: string;
  repository: { name: string; commit: string; subject: string; branch: string; path?: string };
  scope: { path: string; count: number }[];
  knowledge: { checks: number; notes: number; latest: string };
  tasks: { id: string; title: string; state: FlowTaskState; detail?: string; agentId?: string; agentName?: string }[];
  /** The conversation's own task list, kept by its agent; a branch of its own, with or without a project. */
  taskList?: { id: string; title: string; state: FlowTaskState; detail?: string }[];
  /** A long goal this conversation is pursuing by itself, with no project. */
  goal?: { text: string; detail: string; live: boolean; action?: "pause" | "resume"; dismiss: boolean } | null;
  /** The model the agents run on, with a few to switch to. Built from the app's model snapshot. */
  models?: { current: ModelOption & { reasoning?: ModelReasoningState; vision?: boolean }; recent: ModelOption[];
             groups: { id: string; name: string; options: ModelOption[] }[] };
  /** Before a start: the repository's uncommitted changes, which a start must not leave behind. */
  workspace?: { changeCount: number; changes: { status: string; path: string }[]; head: string; branch: string };
  /** Files the person attached; every episode reads copies of them. */
  references?: { name: string; bytes?: number }[];
  /** A finished program: why its last planner found the objective met, and what each milestone took. */
  outcome?: { summary: string; milestones: { index: number; tokens: number; episodes: number }[] };
  /** The milestone woven so far: a lane per task, a span per episode, positions as shares of the time axis. */
  timeline?: {
    lanes: { key: string; label: string; taskId?: string }[];
    spans: { id: string; lane: string; phase: "plan" | "work" | "review"; from: number; to: number; live: boolean;
             trouble: boolean; tokens: number; minutes: number }[];
    ticks: { at: number; label: string }[];
    live: boolean;
    /** peak: most episodes out at once; waitMinutes: time the scheduler held ready work back (projectRun/overview metrics). */
    stats: { episodes: number; trouble: number; perHour: number; peak?: number; waitMinutes?: number };
  };
  budget: { tokens: number; tokenLimit: number };
  workers: number;
  /** Builders and reviewers run unsandboxed (chosen when the project started). */
  fullAccess?: boolean;
  /** Persisted permission of the conversation, separate from an already-started run. */
  conversationPermissionMode?: string;
  maxWorkers: number;
  controls: { pause: boolean; resume: boolean; cancel: boolean };
  agents: FlowAgent[];
  /** The conversation above the hub: the person's messages and the agent's answers, newest last. */
  conversation?: FlowMessage[];
  /** The agent is answering now; `activity` is its latest line of commentary. */
  chatting?: boolean;
  activity?: string;
  /** A project the agent proposed, waiting for the person to start it. */
  proposal?: WeaveProposal;
  /** The 建造 conversations, newest first; `when` is said already, `live` while one answers or runs. */
  history?: BuildHistoryEntry[];
  historyError?: string;
}

export interface ModelOption { selection: string; name: string; group: string; groupId: string }

export interface FlowActions {
  workspaceControls?: ReactNode;
  onChooseWorkspace?(): void;
  /** A message to the conversation's agent, with the files pasted, dropped or picked into the field. */
  onChat?(text: string, attachments: { path: string; name: string }[], tokens?: number): void;
  /** The long goal's stop, resume and dismiss. */
  onGoal?(action: "pause" | "resume" | "dismiss"): void;
  /** Whether the model can take the images attached to a message, and whether that is known (the chat composer's gate). */
  imagesAllowed?: boolean;
  imageSupport?: boolean | null;
  /** Stop the agent's answer. */
  onInterrupt?(): void;
  onPermissionModeChange?(mode: string): Promise<unknown>;
  /** Open another 建造 conversation from the history card, or begin a new one. */
  onOpenThread?(id: string): Promise<unknown> | void;
  onNewThread?(): void;
  /** Rename, archive or delete one of them from the history card (the sidebar's actions). */
  onRenameThread?(id: string, title: string): Promise<unknown> | void;
  onArchiveThread?(id: string): Promise<unknown> | void;
  onDeleteThread?(id: string): Promise<unknown> | void;
  /** Answer a step the agent asks the person to allow, as the chat's approval card does. */
  onApproval?(item: TranscriptItem, decision: ApprovalDecision, feedback?: string): void;
  /** Start the proposed project, with the budget and agents set in the footer. */
  onStart?(objective: string, workers: number, tokens: number, attachments: string[], fullAccess: boolean): void;
  /** Set the proposal aside. */
  onDismiss?(): void;
  /** The system file dialog; resolves to the chosen paths (none when cancelled). */
  onPickFiles?(): Promise<string[]>;
  /** Files added while the program runs. */
  onAttach?(paths: string[]): void;
  onModel?(selection: string): void;
  /** The reasoning level of the model the agents use, as the app's model card sets it. */
  onReasoning?(kind: string, value: string): Promise<void> | void;
  /** Commit every uncommitted change with this message; `count` is how many were shown. */
  onCommitWorkspace?(message: string, count: number): void;
  onSteer?(text: string): void;
  onWorkers?(count: number): void;
  onPause?(): void;
  onResume?(): void;
  onCancel?(): void;
  /** The attention line's action; an approval also has "decline". */
  onAttention?(choice: "accept" | "decline"): void;
  busy?: boolean;
  error?: string;
}

// The model card's tab for the recently used models; provider ids never take this form.
const RECENT = "@recent";
const roleIcon = { planner: Compass, worker: Hammer, reviewer: ShieldCheck };
/** Budgets a new program can start with; the chip cycles through them. The Host enforces it at every request. */
const START_BUDGETS = [2_000_000, 4_000_000, 8_000_000, 16_000_000];
const INPUT_MAX_HEIGHT = 240;
const taskIcon: Record<FlowTaskState, typeof Check> = { pending: Circle, working: LoaderCircle, review: CircleDot, rework: CircleDot, integrated: Check };

const zhText = {
  repository: "仓库", scope: "工作范围", knowledge: "项目知识", checks: "条检查", notes: "条笔记", plan: (n: number) => `阶段 ${n} 计划`,
  taskList: "任务清单", planEmpty: "规划完成后在这里显示任务", outcome: "已完成", outcomeCount: (n: number) => `${n} 个阶段`, episodes: (n: number) => `${n} 轮`,
  donePlaceholder: "目标已完成", loom: "建造时间线", phases: { plan: "规划", work: "开发", review: "复审" },
  loomStats: (s: { episodes: number; trouble: number; perHour: number; peak?: number; waitMinutes?: number }) =>
    `${s.episodes} 轮 · 重试 ${s.trouble} · ${compact(s.perHour)}/时${(s.peak || 0) > 1 ? ` · 最多同时 ${s.peak}` : ""}${s.waitMinutes ? ` · 排队 ${s.waitMinutes} 分` : ""}`,
  spanTitle: (phase: string, minutes: number, tokens: number, trouble: boolean) =>
    `${phase} · ${minutes} 分 · ${compact(tokens)} token${trouble ? " · 未正常结束，已重试" : ""}`,
  dirty: (n: number) => `仓库里有 ${n} 个未提交的改动`,
  dirtyWhy: (head: string, branch: string) => `建造从最后一次提交${head ? `（${head}）` : ""}开始，这些改动不会带进去；可以照常开始。完成后合并回 ${branch || "当前分支"} 时，已跟踪文件还没提交的改动会让合并先等你处理，未跟踪的文件不受影响。`,
  dirtyShow: "查看并提交", dirtyHide: "收起", dirtyMore: (n: number) => `还有 ${n} 个`, commitAll: "全部提交",
  commitMessage: "保存改动，准备开始建造", commitHint: "不想提交的文件，先加入 .gitignore 或移出仓库。",
  now: "现在", files: "参考文件", filesEmpty: "需求文档、规格或样例数据，每一轮的 agent 都能读到", addFiles: "添加文件",
  remove: "移除", modelCard: "模型", modelSearch: "搜索模型", modelGroups: "模型来源", modelRecent: "最近", modelNone: "没有匹配的模型", vision: "识图", modelHint: "已开始的 agent 沿用原模型，新开始的使用所选模型", mode: "建造", stage: (n: number) => `阶段 ${n}`,
  draftPlaceholder: "和它聊聊，或描述一个想持续完成的大目标", steerPlaceholder: "继续对话：问进度、补充要求或调整方向",
  attach: "添加附件，也可以粘贴或拖入文件", attachLimit: `每条消息最多 ${MAX_COMPOSER_ATTACHMENTS} 个附件`,
  proposal: "建造提案", startProposal: "开始建造", dismissProposal: "不用了", replying: "正在思考…", stopReply: "停止回复", copy: "复制", copied: "已复制", latest: "最新",
  proposalHint: "从最后一次提交开始，在独立的检出里分阶段开发，每个任务独立复审后集成回当前分支",
  review: "沙箱", reviewHint: "开发和复审 agent 只能改写自己的独立检出，读不到你的其他文件夹；每个任务合入前都由另一个 agent 独立复审",
  fullAccess: "完全访问", fullAccessHint: "开发和复审 agent 不受沙箱限制，可以联网、安装依赖、读写你电脑上的任何文件；每个任务合入前仍由另一个 agent 独立复审", accessToggle: "点击切换沙箱 / 完全访问", settings: "运行设置", agents: "并行 agent", fewer: "减少", more: "增加", send: "发送", start: "开始", pause: "暂停", resume: "继续", stop: "停止",
  roles: { planner: "规划", worker: "开发", reviewer: "复审" }, objective: "目标", guidance: "补充要求", hub: "建造",
  attention: { recover: "重新复审并继续", budget: "追加预算", approval: "允许一次", resume: "继续" }, decline: "拒绝", noAgents: "等待调度", budgetHint: "项目的总 token 预算，普通对话不受它限制：点击或滚轮切换，↑↓ 也可以", agentsHint: "项目同时工作的 agent 上限，普通对话不受它影响", keyHint: ["发送", "换行"], expand: "展开", collapse: "收起",
  branches: "分支面板", agentCards: "Agent", foldBranch: (name: string) => `收起${name}`, unfoldBranch: (name: string) => `展开${name}`,
  history: "对话记录", historyEmpty: "还没有别的建造对话", newThread: "新建建造对话",
  goalDismiss: "收起", taskListPending: "这条对话用到工具时会列出任务清单", scopePending: "任务规划后显示工作范围", knowledgePending: "还没有复审记录或项目笔记",
  agentsPending: "当前没有可查看的 agent，任务调度后自动启用", timelinePending: "还没有执行记录，建造开始后自动启用",
  filesUnavailable: "当前没有可查看或添加的参考文件", historyUnavailable: "当前环境未提供对话记录",
};
const enText: typeof zhText = {
  repository: "Repository", scope: "Scope", knowledge: "Knowledge", checks: "checks", notes: "notes", plan: (n: number) => `Milestone ${n} plan`,
  taskList: "Task list", planEmpty: "Tasks appear here once the plan is made", outcome: "Complete", outcomeCount: (n: number) => `${n} milestones`,
  episodes: (n: number) => `${n} episodes`, donePlaceholder: "The objective is complete",
  loom: "Timeline", phases: { plan: "Plan", work: "Build", review: "Review" },
  loomStats: (s: { episodes: number; trouble: number; perHour: number; peak?: number; waitMinutes?: number }) =>
    `${s.episodes} episodes · ${s.trouble} retried · ${compact(s.perHour)}/h${(s.peak || 0) > 1 ? ` · up to ${s.peak} at once` : ""}${s.waitMinutes ? ` · ${s.waitMinutes} min queued` : ""}`,
  spanTitle: (phase: string, minutes: number, tokens: number, trouble: boolean) =>
    `${phase} · ${minutes} min · ${compact(tokens)} tokens${trouble ? " · did not settle; retried" : ""}`,
  dirty: (n: number) => `The repository has ${n} uncommitted change${n === 1 ? "" : "s"}`,
  dirtyWhy: (head: string, branch: string) => `Weave starts from the last commit${head ? ` (${head})` : ""}; these changes are not included, and you can start anyway. When it merges back into ${branch || "the current branch"}, uncommitted changes to tracked files make the merge wait for you; untracked files do not matter.`,
  dirtyShow: "Review and commit", dirtyHide: "Hide", dirtyMore: (n: number) => `${n} more`, commitAll: "Commit all",
  commitMessage: "Save work before Weave", commitHint: "Add files you do not want committed to .gitignore, or move them out of the repository.",
  now: "now", files: "Reference files", filesEmpty: "Specs, documents or sample data; every episode can read them",
  addFiles: "Add files", remove: "Remove", modelCard: "Model", modelSearch: "Search models", modelGroups: "Model sources", modelRecent: "Recent", modelNone: "No matching models", vision: "vision",
  modelHint: "Agents already started keep their model; new ones use the choice", mode: "Weave", stage: (n: number) => `milestone ${n}`,
  draftPlaceholder: "Talk it through, or describe a large objective to pursue",
  steerPlaceholder: "Keep talking: ask about progress, add requirements or change direction",
  attach: "Attach files, or paste and drop them here", attachLimit: `At most ${MAX_COMPOSER_ATTACHMENTS} attachments per message`,
  proposal: "Proposed project", startProposal: "Start weaving", dismissProposal: "Not now", replying: "Thinking…", stopReply: "Stop the reply", copy: "Copy", copied: "Copied", latest: "Latest",
  proposalHint: "Builds from the last commit in its own checkout, milestone by milestone; every task is reviewed before it is merged into the current branch",
  review: "Sandbox", reviewHint: "Builders and reviewers can change only their own isolated checkout and cannot read your other folders; every task is reviewed by a separate agent before it is integrated",
  fullAccess: "Full access", fullAccessHint: "Builders and reviewers run outside the sandbox: they can use the network, install dependencies and read or change any file on this computer; every task is still reviewed by a separate agent before it is integrated", accessToggle: "Click to switch between sandbox and full access", settings: "Run settings", agents: "Parallel agents", fewer: "Fewer", more: "More", send: "Send", start: "Start", pause: "Pause", resume: "Resume", stop: "Stop",
  roles: { planner: "Planner", worker: "Builder", reviewer: "Reviewer" }, objective: "Objective", guidance: "Guidance", hub: "Weave",
  attention: { recover: "Re-review and continue", budget: "Add budget", approval: "Allow once", resume: "Continue" }, decline: "Decline", noAgents: "Waiting to schedule", budgetHint: "The project's total token budget; plain conversation turns are not limited by it. Click or scroll to change, or use ↑↓", agentsHint: "Most agents a project runs at once; plain conversation turns are not affected", keyHint: ["send", "new line"], expand: "More", collapse: "Less",
  branches: "Branch panels", agentCards: "Agents", foldBranch: (name: string) => `Hide ${name}`, unfoldBranch: (name: string) => `Show ${name}`,
  history: "Conversations", historyEmpty: "No other Weave conversations yet", newThread: "New Weave conversation",
  goalDismiss: "Dismiss", taskListPending: "A task list appears when this conversation uses tools", scopePending: "File scope appears after tasks are planned", knowledgePending: "No review checks or project notes yet",
  agentsPending: "No agents to inspect yet; available once tasks are dispatched", timelinePending: "No execution records yet; available once the build starts",
  filesUnavailable: "No reference files to view or add here", historyUnavailable: "Conversation history is not available here",
};
type Side = "left" | "right" | "below";
/** A cable's corner radius, the most its lanes stand apart (a cable's 9px plus a gap), and its plugs apart on the hub. */
const CABLE_R = 12, LANE_STEP = 15, PLUG_APART = 22;
interface Cable { id: string; side: Side; d: string; socket: { x: number; y: number } }

// The branches the person folded away, by id. Storage may be missing or refuse; then nothing is folded.
const FOLDED_KEY = "loom.buildFolded";
function readFolded(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(FOLDED_KEY) || "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch { return []; }
}
function writeFolded(ids: string[]) {
  try { localStorage.setItem(FOLDED_KEY, JSON.stringify(ids)); } catch { /* kept for this session only */ }
}
// The history card is the one branch that starts folded; opened, it stays open across conversations,
// so the person can walk through them from it.
const HISTORY_KEY = "loom.buildHistoryOpen";
function readHistoryOpen() {
  try { return localStorage.getItem(HISTORY_KEY) === "1"; } catch { return false; }
}

function compact(value: number) {
  // 16M, 2.4M: a whole number of millions has no ".0".
  return value >= 1_000_000 ? `${Number((value / 1_000_000).toFixed(1))}M` : value >= 1000 ? `${Math.round(value / 1000)}k` : String(value);
}

/** Changes when `value` changes after the first render: keys a one-shot animation to that moment. */
function useMoment(value: unknown) {
  const [moment, setMoment] = useState(0);
  const last = useRef(value);
  useEffect(() => {
    if (last.current === value) return;
    last.current = value;
    setMoment((count) => count + 1);
  }, [value]);
  return moment;
}

/** The list as it was a moment ago too: an item that leaves stays (marked leaving) for its exit.
 *  The departure is derived during render, like useMotionPresence: noticing it in an effect let
 *  React unmount the card first, and the re-mounted copy replayed its entrance instead of leaving. */
function useDeparting<T extends { id: string }>(items: T[]): (T & { leaving?: boolean })[] {
  const reduce = useReducedMotion();
  const key = items.map((item) => item.id).join("\u0000");
  const [seenKey, setSeenKey] = useState(key);
  const [gone, setGone] = useState<{ item: T; index: number }[]>([]);
  const latest = useRef(items);  // the items of the last commit: what a departed item looked like
  if (seenKey !== key) {
    setSeenKey(key);
    const now = new Set(items.map((item) => item.id));
    const left = latest.current.map((item, index) => ({ item, index })).filter(({ item }) => !now.has(item.id));
    if (left.length && !reduce) {
      setGone((list) => [...list.filter(({ item }) => !now.has(item.id) && !left.some((l) => l.item.id === item.id)), ...left]);
    }
  }
  useLayoutEffect(() => { latest.current = items; });
  useEffect(() => {
    if (!gone.length) return;
    // Keep the in-place exit until it has finished.
    const ids = gone.map(({ item }) => item.id);
    // Its exit runs in three beats (contents, surface, cable: project-flow.css), then the others close its room: it is
    // taken out when all of that has come to rest.
    const timer = window.setTimeout(() => setGone((list) => list.filter(({ item }) => !ids.includes(item.id))), reduce ? 0 : LEAVE_RETAIN);
    return () => window.clearTimeout(timer);
  }, [gone, reduce]);
  const out: (T & { leaving?: boolean })[] = [...items];
  for (const { item, index } of gone) {
    if (!out.some((entry) => entry.id === item.id)) out.splice(Math.min(index, out.length), 0, { ...item, leaving: true });
  }
  return out;
}

/** Cables from every registered card to the hub. Cards register by id.
 *
 *  A cable is drawn where the cards are on this very frame: while the hub moves, every frame writes the
 *  paths straight into the SVG (no React render per frame, which drew them a frame or more behind the
 *  cards and re-rendered the whole hub sixty times a second). React only knows which cables there are; what it
 *  renders for them is read from the last frame drawn, so a render never writes an older path back.
 *
 *  Two parts of a cable are not tied to where its card is: where it plugs into the hub (the plugs of a side are
 *  re-spaced when a card comes or goes) and the lane its turn stands in (lanes are re-dealt when cables change
 *  order). They are towed by the card, on the card's own clock: both wait while it waits, and start and land with it.
 *  A cable whose card stays put goes on the hub's clock. It is one formula for the card and for both of these: from
 *  where it was seen to where the layout has it now. */
function useCables(stage: React.RefObject<HTMLDivElement | null>, hub: React.RefObject<HTMLElement | null>,
                   cards: React.RefObject<Map<string, { side: Side; node: HTMLElement }>>, layout: string) {
  const [cables, setCables] = useState<Cable[]>([]);
  const svg = useRef<SVGSVGElement>(null);
  // What each cable showed on the last frame drawn.
  const shown = useRef(new Map<string, { x1: number; y1: number; y2: number; turn: number }>());
  // The cables on their way to a new plug or lane: where they set off from, and the card that tows each (none: the hub's clock).
  const going = useRef(new Map<string, { y2: number; turn: number; card: HTMLElement | null }>());
  const hubClock = useRef<Animation | null>(null);
  const drawn = useRef<Cable[]>([]);
  const listed = useRef("");
  const frame = useRef(0);
  // The last time the glide drew a frame: while it runs, it draws them (after it has placed the cards).
  const driven = useRef(0);
  const before = useRef(0);

  const geometry = useCallback(() => {
    const root = stage.current, center = hub.current;
    if (!root || !center) return null;
    const base = root.getBoundingClientRect();
    const box = (r: DOMRect) => ({ left: r.left - base.left, right: r.right - base.left, top: r.top - base.top,
                                   bottom: r.bottom - base.top, height: r.height });
    const m = box(center.getBoundingClientRect());
    // Where the layout puts the hub (offsets ignore the glide's transforms): the plugs are dealt on it,
    // then carried along with the hub as it is drawn now.
    const hubTop = layoutTop(center, root), hubShift = m.top - hubTop;
    const entries = [...cards.current.entries()];
    const out: Geometry[] = [];
    for (const side of ["left", "right"] as const) {
      const toward = side === "left" ? 1 : -1;  // the direction from the cards to the hub
      // A cable's card end is where the card is drawn now (gliding, unrolling); its hub end is dealt from
      // where the cards are going. A plug then stays put while its card moves, the cable bending between
      // them, instead of sliding along the hub with every card that glides or unrolls.
      let list = entries.filter(([, item]) => item.side === side)
        .map(([id, item]) => {
          const b = box(visibleBox(item.node));
          const head = layoutTop(item.node, root) + Math.min(34, item.node.offsetHeight / 2);
          // A card scrolled out of its column keeps its cable, which runs to the column's edge.
          const view = sideView(item.node), low = view ? view.top - base.top : -Infinity, high = view ? view.bottom - base.top : Infinity;
          const inView = (y: number) => Math.max(low, Math.min(high, y));
          return { id, x1: side === "left" ? b.right : b.left, y1: inView(Math.min(b.top + 34, b.top + b.height / 2)), head: inView(head), y2: 0, lane: 0,
                   card: item.node, held: Boolean(item.node.closest("[data-leaving]")) };
        })
        .sort((a, b) => a.head - b.head);
      // A card that leaves keeps its plug while its cable retracts; the others are dealt as if it had gone
      // already, so they move once, now, not again when it is taken out of the page.
      const all = list;
      list = list.filter((item) => !item.held);
      for (const item of all) if (item.held) out.push({ id: item.id, card: item.card, side, toward, x1: item.x1, y1: item.y1, x2: side === "left" ? m.left : m.right,
                                                       y2: item.head + hubShift, turn: (side === "left" ? m.left : m.right) - toward * (CABLE_R + 2), held: true });
      if (!list.length) continue;
      const x2 = side === "left" ? m.left : m.right;
      // Plugs: level with their card where the hub's side allows (a straight cable), kept in card
      // order and a plug apart, inside the side's ends.
      const top = hubTop + 24, bottom = hubTop + center.offsetHeight - 24, apart = Math.min(PLUG_APART, (bottom - top) / Math.max(1, list.length - 1));
      list.forEach((item, index) => { item.y2 = Math.max(top + index * apart, Math.min(item.head, bottom - (list.length - 1 - index) * apart)); });
      for (let index = 1; index < list.length; index += 1) list[index].y2 = Math.max(list[index].y2, list[index - 1].y2 + apart);
      // Lanes: the turns stand in the gap, nearest the hub first. A cable running down takes a nearer
      // lane than every card below it, a cable running up a nearer lane than every card above it:
      // then no cable crosses another (the two groups never share a height). Dealt on the layout too.
      const down = list.filter((item) => item.y2 - item.head > 1);
      const up = list.filter((item) => item.head - item.y2 > 1).reverse();
      down.forEach((item, index) => { item.lane = index; });
      up.forEach((item, index) => { item.lane = index; });
      const lanes = Math.max(down.length, up.length);
      // The lanes share what the gap has between the nearest card edge and the plugs, a cable's width
      // plus a gap apart at most, so no turn stands behind a card.
      const near = x2 - toward * (CABLE_R + 2);
      const edge = side === "left" ? Math.max(...list.map((item) => item.x1)) : Math.min(...list.map((item) => item.x1));
      const room = Math.abs(near - (edge + toward * (CABLE_R + 4)));
      const step = lanes > 1 ? Math.max(4, Math.min(LANE_STEP, room / (lanes - 1))) : 0;
      for (const { id, card, x1, y1, y2, lane } of list) out.push({ id, card, side, toward, x1, y1, x2, y2: y2 + hubShift, turn: near - toward * lane * step });
    }
    for (const [id, item] of entries.filter(([, item]) => item.side === "below")) {
      const b = box(visibleBox(item.node));
      const x = Math.max(b.left + 72, m.left + 72);
      out.push({ id, card: item.node, side: "below", toward: 0, x1: x, y1: b.top, x2: x, y2: m.bottom, turn: x });
    }
    return out;
  }, [stage, hub, cards]);

  /** Draws one frame; says whether anything is still on its way. */
  const draw = useCallback((fresh: boolean, hold = 0) => {
    const list = geometry();
    if (!list) return false;
    const now = performance.now();
    const dt = Math.min(0.032, Math.max(0, (now - before.current) / 1000));
    before.current = now;
    const still = prefersReducedMotionNow();
    if (still) { going.current.clear(); hubClock.current?.cancel(); hubClock.current = null; }
    if (fresh && !still) {
      // Every cable that has somewhere else to be sets off from where it is now, on its card's clock if the card is
      // moving and that clock has some way to go. A clock under way keeps going: only the end changes.
      const startClock = () => {
        hubClock.current?.cancel();
        hubClock.current = new Animation(new KeyframeEffect(null, [], { duration: HUB_CLOCK, delay: hold, easing: HUB_EASE, fill: "both" }));
        hubClock.current.play();
      };
      let hubAgain = false;
      for (const item of list) {
        const seen = shown.current.get(item.id);
        if (!seen || item.held) continue;
        if (Math.abs(seen.y2 - item.y2) <= 0.5 && Math.abs(seen.turn - item.turn) <= 0.2) continue;
        const towed = glideProgress(item.card);
        if (towed !== null && towed < 0.9) {
          going.current.set(item.id, { y2: (seen.y2 - item.y2 * towed) / (1 - towed), turn: (seen.turn - item.turn * towed) / (1 - towed), card: item.card });
        } else {
          going.current.set(item.id, { y2: seen.y2, turn: seen.turn, card: null });
          hubAgain = true;
        }
      }
      if (hubAgain) {
        // The others on the hub's clock go on from here too.
        for (const [id, on] of going.current) {
          if (on.card) continue;
          const seen = shown.current.get(id);
          if (seen) { on.y2 = seen.y2; on.turn = seen.turn; }
        }
        startClock();
      }
    }
    const hubProgress = hubClock.current ? Number(hubClock.current.effect?.getComputedTiming().progress ?? 1) : 1;
    const next = new Map<string, { x1: number; y1: number; y2: number; turn: number }>();
    const out: Cable[] = [];
    let following = false;
    for (const item of list) {
      const seen = shown.current.get(item.id);
      if (item.held && seen) {
        // Leaving: its tip stays with the dot it closes into and its plug where it was seen, as it retracts.
        going.current.delete(item.id);
        next.set(item.id, seen);
        out.push(cablePath({ ...item, x1: seen.x1, y1: seen.y1 }, seen.y2, seen.turn));
        continue;
      }
      const on = going.current.get(item.id);
      let y2 = item.y2, turn = item.turn;
      if (on) {
        const progress = on.card ? (glideProgress(on.card) ?? 1) : hubProgress;
        if (progress >= 1) going.current.delete(item.id);
        else { y2 = on.y2 + (item.y2 - on.y2) * progress; turn = on.turn + (item.turn - on.turn) * progress; }
      }
      // A lane is dealt on a layout that may be moving: a cable can change lanes as its card passes its plug. That is
      // followed, never snapped to.
      if (seen && !still && item.side !== "below") {
        const followed = seen.turn + (turn - seen.turn) * (1 - Math.exp(-dt * TURN_FOLLOW));
        if (Math.abs(followed - turn) > 0.2) { turn = followed; following = true; }
      }
      next.set(item.id, { x1: item.x1, y1: item.y1, y2, turn });
      out.push(cablePath(item, y2, turn));
    }
    shown.current = next;
    if (hubProgress >= 1) { hubClock.current = null; }
    drawn.current = out;
    // Written here, on the frame the cards moved.
    const root = svg.current;
    if (root) {
      for (const cable of out) {
        for (const node of root.querySelectorAll<SVGElement>(`[data-of="${CSS.escape(cable.id)}"]`)) {
          if (node instanceof SVGPathElement) node.setAttribute("d", cable.d);
          else if (node instanceof SVGRectElement) {
            const below = cable.side === "below";
            node.setAttribute("x", String(cable.socket.x - (below ? 10 : 5)));
            node.setAttribute("y", String(cable.socket.y - (below ? 5 : 10)));
          }
        }
      }
    }
    // The list only gains and loses cables, it is never reordered, whatever order they are dealt in: React would move a
    // node to match, and a node that moves is inserted again, so `@starting-style` grows its cable again from nothing
    // (the history card's cable vanished as the model card closed).
    const key = out.map((cable) => `${cable.id}:${cable.side}`).sort().join("|");
    if (key !== listed.current) {
      listed.current = key;
      setCables((current) => {
        const now = new Map(out.map((cable) => [cable.id, cable]));
        const kept = current.filter((cable) => now.has(cable.id)).map((cable) => now.get(cable.id)!);
        return [...kept, ...out.filter((cable) => !current.some((item) => item.id === cable.id))];
      });
    }
    return going.current.size > 0 || following;
  }, [geometry]);

  const follow = useCallback(() => {
    const step = () => {
      // The glide's frame, if it is running, comes with the cards already placed: that one counts.
      const moving = performance.now() - driven.current < 34 || draw(false);
      frame.current = moving ? requestAnimationFrame(step) : 0;
    };
    frame.current = requestAnimationFrame(step);
  }, [draw]);
  const measure = useCallback((fresh = false, hold = 0) => {
    if (draw(fresh, hold) && !frame.current) follow();
  }, [draw, follow]);
  const glideFrame = useCallback((fresh = false, hold = 0) => {
    driven.current = performance.now();
    measure(fresh, hold);
  }, [measure]);
  useLayoutEffect(() => () => { cancelAnimationFrame(frame.current); hubClock.current?.cancel(); }, []);
  useLayoutEffect(() => {
    // The stage has a fixed size, so watching it alone missed every reflow inside it (a card growing,
    // the thread pushing the hub down) and left the cables where the cards had been. Watch what moves.
    // A layout change itself is drawn by the glide (useGlide calls measure(true) on its first frame).
    const root = stage.current;
    const sides = [...(root?.querySelectorAll<HTMLElement>(".pf-side") ?? [])];
    const observer = new ResizeObserver(() => { measure(); sides.forEach(markScrollEdges); });
    if (root) for (const node of [root, ...root.querySelectorAll<HTMLElement>(".pf-col, .pf-composer, .pf-card")]) observer.observe(node);
    // Scrolled, a column's cables are written on the frame it moved.
    const scrolled = (event: Event) => {
      markScrollEdges(event.currentTarget as HTMLElement);
      if (draw(false) && !frame.current) follow();
    };
    for (const side of sides) side.addEventListener("scroll", scrolled, { passive: true });
    return () => {
      observer.disconnect();
      for (const side of sides) side.removeEventListener("scroll", scrolled);
    };
  }, [measure, draw, follow, layout]);
  // Rendered as drawn last, whatever the state's copy from when the list last changed says.
  const latest = new Map(drawn.current.map((cable) => [cable.id, cable]));
  return { cables: cables.map((cable) => latest.get(cable.id) ?? cable), measure: glideFrame, svg };
}

/** The room around a side column's cards for their shadows, which a scroller would otherwise cut: --pf-side-shade. */
const SIDE_SHADE = 16;
/** Where a card's side column shows it, once the column has more than it can show. */
function sideView(node: HTMLElement) {
  const side = node.closest<HTMLElement>(".pf-side");
  if (!side || side.scrollHeight <= side.clientHeight + 1) return null;
  const box = side.getBoundingClientRect();
  return { top: box.top + SIDE_SHADE, bottom: box.bottom - SIDE_SHADE };
}
/** Fades the edges past which a side column has more to show. Marked by hand: scrolling must not render the hub. */
function markScrollEdges(side: HTMLElement) {
  side.toggleAttribute("data-more-up", side.scrollTop > 1);
  side.toggleAttribute("data-more-down", side.scrollHeight - side.clientHeight - side.scrollTop > 1);
}

/** How far down the stage the layout puts a node: offsets leave out transforms (a glide) and clips (an unroll). */
function layoutTop(node: HTMLElement, root: HTMLElement) {
  let top = 0;
  let item: HTMLElement | null = node;
  while (item && item !== root) {
    top += item.offsetTop;
    const parent = item.offsetParent as HTMLElement | null;
    if (parent && parent !== root && !root.contains(parent)) break;
    if (parent && parent !== root) top += parent.clientTop - parent.scrollTop;
    item = parent;
  }
  return top;
}

/** How fast a turn follows its lane, per second: a tenth of a second to be there, under anything a clock asks of it. */
const TURN_FOLLOW = 30;
interface Geometry { id: string; card: HTMLElement; side: Side; toward: number; x1: number; y1: number; x2: number; y2: number; turn: number; held?: boolean }

/** One path shape for every cable, straight or bent: a bend of zero radius is a straight line, so a cable
 *  that straightens or starts to bend changes smoothly instead of switching drawings. */
function cablePath({ id, side, toward, x1, y1, x2 }: Geometry, y2: number, turn: number): Cable {
  if (side === "below") return { id, side, d: `M${x1},${y1} L${x2},${y2}`, socket: { x: x2, y: y2 } };
  const dir = y2 >= y1 ? 1 : -1;
  const r = Math.max(0, Math.min(CABLE_R, Math.abs(y2 - y1) / 2, (turn - x1) * toward - 2, (x2 - turn) * toward));
  const f = (value: number) => Math.round(value * 10) / 10;
  const d = `M${f(x1)},${f(y1)} L${f(turn - toward * r)},${f(y1)} Q${f(turn)},${f(y1)} ${f(turn)},${f(y1 + dir * r)} `
    + `L${f(turn)},${f(y2 - dir * r)} Q${f(turn)},${f(y2)} ${f(turn + toward * r)},${f(y2)} L${f(x2)},${f(y2)}`;
  return { id, side, d, socket: { x: x2, y: y2 } };
}

function Card({ id, side, icon, label, count, under, register, className = "", children }: {
  id: string; side: Side; icon: ReactNode; label: string; count?: ReactNode; under?: ReactNode;
  register(id: string, side: Side, node: HTMLElement | null): void; className?: string; children: ReactNode;
}) {
  return (
    <section className={`pf-card ${className}`.trim()} ref={(node) => register(id, side, node)} data-side={side}>
      <header className="pf-card-head">
        <span className="pf-card-icon">{icon}</span><span>{label}</span>
        {count != null ? <em className="pf-count">{count}</em> : null}
      </header>
      {under}
      {children}
    </section>
  );
}

const FILE_KINDS: [RegExp, string][] = [[/\.(md|txt|rst|pdf|docx?)$/i, "doc"], [/\.(csv|tsv|xlsx?|json|ya?ml|toml)$/i, "data"],
  [/\.(png|jpe?g|gif|webp|svg)$/i, "image"], [/\.(py|[cm]?[jt]sx?|go|rs|java|c|cpp|h|cs|rb|sh|ps1|sql|html|css)$/i, "code"]];
function fileKind(name: string) {
  return FILE_KINDS.find(([pattern]) => pattern.test(name))?.[1] || "other";
}
const fileIcons: Record<string, typeof File> = { doc: FileText, data: FileSpreadsheet, image: FileImage, code: FileCode, other: File };
function fileIcon(name: string) {
  return fileIcons[fileKind(name)];
}
function bytes(value: number) {
  return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`;
}

/** Text with `code` spans drawn as code: plans and objectives are written in Markdown habits. */
function Inline({ text }: { text: string }) {
  const parts = text.split("`");
  return <>{parts.map((part, index) => index % 2 ? <code key={index} className="pf-code">{part}</code> : part)}</>;
}

// The hub has no reply column beside its work log: what the agent says between steps is read inside the stage, always.
const SHOW_NOTES = { show: true };

/** What the agent said between two steps, as a line of the stage (weave.css .wv-note). */
function StageNote({ item, workspace }: { item: TranscriptItem; workspace?: string }) {
  return <MarkdownMessage content={String(item.text || "").trim()} compact glow={false} messageKey={item.id} workspace={workspace} />;
}

/** An answer's copy button: it shows on hover (or focus) under the answer, and says so once it has copied. */
function CopyReply({ value, label, done }: { value: string; label: string; done: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1400);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return (
    <div className="pf-reply-tools">
      <button type="button" aria-label={copied ? done : label} title={copied ? done : label} data-copied={copied || undefined}
              onClick={() => void navigator.clipboard.writeText(value).then(() => setCopied(true), () => undefined)}>
        {copied ? <Check key="done" size={12} /> : <Copy key="copy" size={12} />}
      </button>
    </div>
  );
}

/** What the person said. A message with files draws them as the chat's transcript does: chips while it is sent,
 *  then the Host's manifest read back from the text. */
function Said({ message, workspace }: { message: FlowMessage; workspace?: string }) {
  const parsed = optimisticUserMessageContent(message.text, message.attachments);
  if (!parsed.attachments.length) return <div className="pf-bubble pf-said"><span className="pf-said-text">{message.text}</span></div>;
  return <div className="pf-bubble pf-said pf-said-files"><UserMessageContent parsed={parsed} workspace={workspace} /></div>;
}

function Pulse({ of, d, step, back = false }: { of: string; d: string; step: number; back?: boolean }) {
  const moment = useMoment(step);
  return moment ? <path key={moment} className="pf-pulse" data-of={of} data-back={back || undefined} d={d} pathLength={100} /> : null;
}

/** One soft wash across a well when its value changes (not when it first appears). */
function Wash({ on }: { on: unknown }) {
  const moment = useMoment(on);
  return moment ? <span key={moment} className="pf-task-wash" aria-hidden="true" /> : null;
}

export function ProjectFlow({ data, actions = {}, lang = "zh", conversationId, renewing = false, brandNew = false }: { data: FlowData; actions?: FlowActions; lang?: "zh" | "en";
  /** The same hub goes on into another conversation (ProjectMode.tsx): what was being written stays behind. */
  conversationId?: string;
  /** The hub is going over to another conversation: what it says is fading, and nothing can be sent to either. */
  renewing?: boolean;
  /** That conversation was made just now: the hub has shown its empty state already. */
  brandNew?: boolean; }) {
  const text = lang === "en" ? enText : zhText;
  const stage = useRef<HTMLDivElement>(null);
  const hub = useRef<HTMLElement>(null);
  const cards = useRef(new Map<string, { side: Side; node: HTMLElement }>());
  const [hot, setHot] = useState("");
  const [draft, setDraft] = useState("");
  // Files on the next message, as the chat composer holds them (composerAttachments.ts): pasted, dropped or picked.
  const [attached, setAttached] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState("");
  const [dragging, setDragging] = useState(false);
  const attachedNow = useRef(attached);
  attachedNow.current = attached;
  useEffect(() => () => attachedNow.current.forEach(releaseAttachmentPreview), []);
  const [objectiveOpen, setObjectiveOpen] = useState(false);
  const [dirtyOpen, setDirtyOpen] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  // The model card is a branch like the others: the chip in the footer plugs it into the hub and unplugs it.
  const [modelOpen, setModelOpen] = useState(false);
  // Inside it: which provider (or the recent ones) the list shows, a search over all of them, and the one being switched to.
  const [modelTab, setModelTab] = useState("");
  const [modelQuery, setModelQuery] = useState("");
  const [switching, setSwitching] = useState("");
  const models = data.models;
  useEffect(() => setSwitching(""), [models?.current.selection, actions.error]);
  useEffect(() => { if (!modelOpen) { setModelTab(""); setModelQuery(""); } }, [modelOpen]);
  const modelTotal = models ? models.groups.reduce((sum, group) => sum + group.options.length, 0) : 0;
  const query = modelQuery.trim().toLowerCase();
  // Opened, the list starts on the current model's provider; a tab that is gone falls back there too.
  const modelView = modelTab === RECENT && models?.recent.length ? RECENT
    : models?.groups.some((group) => group.id === modelTab) ? modelTab : models?.current.groupId ?? "";
  const modelList = !models ? [] : query
    ? models.groups.flatMap((group) => group.options).filter((option) => `${option.name} ${option.group}`.toLowerCase().includes(query))
    : modelView === RECENT ? models.recent
    : models.groups.find((group) => group.id === modelView)?.options ?? models.groups[0]?.options ?? [];
  const mixed = Boolean(query) || modelView === RECENT;
  // The chosen source stays in sight in its row. Set by hand: scrollIntoView would also scroll the
  // stage, which is clipped and must never move.
  const modelTabs = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const row = modelTabs.current, tab = row?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!row || !tab) return;
    const left = tab.offsetLeft - row.offsetLeft, right = left + tab.offsetWidth;
    if (left < row.scrollLeft) row.scrollLeft = left - 8;
    else if (right > row.scrollLeft + row.clientWidth) row.scrollLeft = right - row.clientWidth + 8;
  }, [modelView, modelOpen, query]);
  const reasoningState = models?.current.reasoning;
  const reasoningText = reasoningState ? reasoningLabel(reasoningState, lang === "zh") : "";
  // The plan scrolls; keep the task in progress in view as work moves down it, unless the person is reading it.
  const plan = useRef<HTMLDivElement>(null);
  const current = data.tasks.find((task) => task.state === "working" || task.state === "review" || task.state === "rework")?.id;
  useEffect(() => {
    const box = plan.current?.querySelector<HTMLElement>(".pf-card");
    const row = box?.querySelector<HTMLElement>(`[data-task="${current}"]`);
    if (!box || !row || hot === "plan") return;
    const top = row.offsetTop - box.clientHeight / 2 + row.offsetHeight / 2;
    box.scrollTo({ top: Math.max(0, top), behavior: prefersReducedMotionNow() ? "auto" : "smooth" });
  }, [current]);  // eslint-disable-line react-hooks/exhaustive-deps
  const input = useRef<HTMLTextAreaElement>(null);
  // The field grows with what is typed (an objective is often a page), up to a cap, then scrolls.
  useLayoutEffect(() => {
    const field = input.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, INPUT_MAX_HEIGHT)}px`;
  }, [draft]);
  const [workers, setWorkers] = useState(data.workers);
  const [tokens, setTokens] = useState(START_BUDGETS[2]);
  const [fullAccess, setFullAccess] = useState(false);
  const [accessChanging, setAccessChanging] = useState(false);
  const accessPending = useRef(false);
  const [accessError, setAccessError] = useState("");
  const controlledAccess = data.conversationPermissionMode !== undefined;
  const accessMode = data.conversationPermissionMode || (fullAccess ? "full-access" : "workspace");
  const accessIsFull = accessMode === "full-access";
  const accessLabel = accessMode === "approval" ? (lang === "zh" ? "逐次批准" : "Approval")
    : accessMode === "read-only" ? (lang === "zh" ? "只读" : "Read only") : accessIsFull ? text.fullAccess : text.review;
  const accessLockedHint = lang === "zh" ? "请先停止当前回复，再切换对话权限" : "Stop the current turn before changing conversation access";
  const changeAccess = async () => {
    if (accessPending.current || actions.busy || (controlledAccess && data.chatting)) return;
    if (!controlledAccess) { setFullAccess(value => !value); return; }
    if (!actions.onPermissionModeChange) return;
    accessPending.current = true; setAccessChanging(true); setAccessError("");
    try { await actions.onPermissionModeChange(accessIsFull ? "workspace" : "full-access"); }
    catch { setAccessError(lang === "zh" ? "权限切换失败，当前权限未改变；请停止当前回复后重试" : "Access could not change; the current setting is unchanged. Stop the turn and retry."); }
    finally { accessPending.current = false; setAccessChanging(false); }
  };
  const settingsTouched = useRef({ workers: false, tokens: false });
  const stepBudget = (by: number, wrap: boolean) => {
    settingsTouched.current.tokens = true;
    setTokens((value) => {
      const next = START_BUDGETS.indexOf(value) + by;
      if (wrap) return START_BUDGETS[(next + START_BUDGETS.length) % START_BUDGETS.length];
      return START_BUDGETS[Math.max(0, Math.min(START_BUDGETS.length - 1, next))];
    });
  };
  // Suggestions fill untouched controls; a proposal must not overwrite the person's draft choices.
  const proposal = data.proposal;
  useEffect(() => {
    if (!proposal) return;
    if (!settingsTouched.current.workers) setWorkers(Math.min(data.maxWorkers, Math.max(1, proposal.maxWorkers || 1)));
    if (!settingsTouched.current.tokens) setTokens(START_BUDGETS.includes(proposal.tokenBudget) ? proposal.tokenBudget : START_BUDGETS[2]);
  }, [proposal?.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const register = useCallback((id: string, side: Side, node: HTMLElement | null) => {
    if (node) cards.current.set(id, { side, node }); else cards.current.delete(id);
  }, []);
  const draftMode = data.phase === "draft";
  // Every branch has its button in the hub's head: folded, the card unplugs (exit, cable back into
  // the hub) and the rest glide into its room. Remembered across sessions, like the panels' widths.
  const [folded, setFolded] = useState(readFolded);
  const fold = (id: string) => setFolded((list) => {
    const next = list.includes(id) ? list.filter((item) => item !== id) : [...list, id];
    writeFolded(next);
    return next;
  });
  const [historyOpen, setHistoryOpen] = useState(readHistoryOpen);
  const history = data.history;
  const open = (id: string) => id === "history" ? historyOpen : !folded.includes(id);
  const showHistory = (value: boolean) => {
    try { localStorage.setItem(HISTORY_KEY, value ? "1" : "0"); } catch { /* kept for this session only */ }
    setHistoryOpen(value);
  };
  // Every branch opens on its own, and no card ever closes another: a column's lists give height, then it scrolls (.pf-side).
  const toggle = (id: string) => {
    if (id !== "history") { fold(id); return; }
    showHistory(!historyOpen);
  };
  const toggleModel = () => setModelOpen(!modelOpen);
  // The quota is the third picker-sized branch of the right column, grown from the ring beside the model chip.
  const [usageOpen, setUsageOpen] = useState(false);
  const usage = useModelUsage(models?.current.selection ?? "");
  const usageZh = lang !== "en";
  const toggleUsage = () => setUsageOpen(!usageOpen);
  const agents = useDeparting(open("agents") ? data.agents : []);
  const branches = useDeparting([...(data.taskList?.length && open("tasks") ? [{ id: "tasks" }] : []),
                                 ...(historyOpen && history ? [{ id: "history" }] : []), ...(modelOpen && data.models ? [{ id: "model" }] : []),
                                 ...(usageOpen && data.models ? [{ id: "usage" }] : [])]);
  // Before a project the plan is a different card from the project's: one leaves as the other is born.
  const below = useDeparting(open("plan") ? [{ id: draftMode ? "plan-draft" : "plan" }] : []);
  const loom = useDeparting(data.timeline && open("loom") ? [{ id: "loom" }] : []);
  // Files the person picked before starting; once started, the Host's copies are what count.
  const [picked, setPicked] = useState<string[]>([]);
  const files = draftMode ? picked.map((path) => ({ name: path.split(/[\\/]/).pop() || path, bytes: undefined as number | undefined, path }))
    : (data.references || []).map((item) => ({ ...item, path: item.name }));
  const filesShown = draftMode ? Boolean(actions.onPickFiles) : files.length > 0 || Boolean(actions.onAttach);
  const contextList = [{ id: "repo" }, ...(data.scope.length ? [{ id: "scope" }] : []), ...(filesShown ? [{ id: "files" }] : []),
                       ...(data.knowledge.checks + data.knowledge.notes ? [{ id: "knowledge" }] : [])];
  const context = useDeparting(contextList.filter((item) => open(item.id)));
  // The buttons, in the order the cards hang: the context on the left, the plan below, the agents and the timeline.
  // Keep the navigation inventory stable, including before a project has data.
  // Empty branches have a visible, explained disabled entry rather than disappearing.
  const toggles: { id: string; icon: typeof Check; label: string; unavailable?: string }[] = [
    { id: "repo", icon: GitBranch, label: text.repository },
    { id: "scope", icon: FolderOpen, label: text.scope, unavailable: data.scope.length ? undefined : text.scopePending },
    { id: "files", icon: Paperclip, label: text.files, unavailable: filesShown ? undefined : text.filesUnavailable },
    { id: "knowledge", icon: BookMarked, label: text.knowledge,
      unavailable: data.knowledge.checks + data.knowledge.notes ? undefined : text.knowledgePending },
    data.outcome ? { id: "plan", icon: Check, label: text.outcome } : { id: "plan", icon: Compass, label: text.plan(data.milestone) },
    { id: "tasks", icon: ListChecks, label: text.taskList, unavailable: data.taskList?.length ? undefined : text.taskListPending },
    { id: "agents", icon: Bot, label: text.agentCards, unavailable: data.agents.length ? undefined : text.agentsPending },
    { id: "loom", icon: ChartGantt, label: text.loom, unavailable: data.timeline ? undefined : text.timelinePending },
    { id: "history", icon: History, label: text.history, unavailable: history ? undefined : text.historyUnavailable },
  ];
  // A card that starts leaving changes the layout already (it is lifted out and the others close up), not only once it is gone.
  const ids = (list: { id: string; leaving?: boolean }[]) => list.map((item) => `${item.id}${item.leaving ? "~" : ""}`).join(",");
  const layout = [ids(context), ids(branches), ids(agents), ids(below), ids(loom), files.length, history?.length ?? 0,
                  modelTotal, modelView, query, reasoningState?.value ?? "",
                  data.tasks.length, data.taskList?.length ?? 0, data.scope.length, data.attention ? 1 : 0, data.phase,
                  dirtyOpen, objectiveOpen, draft, data.narration.summary, data.narration.text,
                  data.conversation?.length ?? 0, proposal?.id ?? "", data.chatting ? 1 : 0].join("|");
  const { cables, measure, svg } = useCables(stage, hub, cards, layout);
  // A low stage squeezes the thread above the hub: it shows the newest line, and scrolls up to the objective.
  // It follows what arrives (an answer being written, a step) while the reader is at the bottom; scrolled up, it
  // stays where they read and offers the way back down.
  const thread = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const gliding = useRef(false);
  const [behind, setBehind] = useState(false);
  useLayoutEffect(() => {
    const box = thread.current;
    if (box && following.current && box.scrollHeight > box.clientHeight) box.scrollTop = box.scrollHeight;
  }, [layout, data.narration.text, dirtyOpen, objectiveOpen]);
  useEffect(() => {
    const box = thread.current;
    if (!box) return;
    let height = box.scrollHeight;
    const grew = () => {
      if (box.scrollHeight === height) return;
      height = box.scrollHeight;
      if (following.current) box.scrollTop = box.scrollHeight;
      else setBehind(true);
    };
    let top = box.scrollTop;
    const onScroll = () => {
      const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
      const up = box.scrollTop < top - 1;
      top = box.scrollTop;
      if (atEnd) {
        gliding.current = false;
        following.current = true;
        setBehind(false);
        return;
      }
      // Only the reader leaves the end, by scrolling up: a stage folding above or a glide back down does not.
      if (gliding.current || !up) return;
      following.current = false;
      setBehind(true);
    };
    // Text grows inside a message without the thread's own box changing: watch every entry.
    const sizes = new ResizeObserver(grew);
    const watch = () => { sizes.disconnect(); sizes.observe(box); for (const child of box.children) sizes.observe(child); };
    const entries = new MutationObserver(() => { watch(); grew(); });
    watch();
    entries.observe(box, { childList: true });
    box.addEventListener("scroll", onScroll, { passive: true });
    return () => { sizes.disconnect(); entries.disconnect(); box.removeEventListener("scroll", onScroll); };
  }, []);
  const toLatest = () => {
    const box = thread.current;
    following.current = true;
    gliding.current = true;
    setBehind(false);
    box?.scrollTo({ top: box.scrollHeight, behavior: prefersReducedMotionNow() ? "auto" : "smooth" });
  };
  const conversation = data.conversation || [];
  const notesWorkspace = data.repository.path;
  const renderNote = useCallback((item: TranscriptItem) => <StageNote item={item} workspace={notesWorkspace} />, [notesWorkspace]);
  const tail = conversation[conversation.length - 1];
  // The quiet moments of a turn (before its first word, between steps) are said by the thinking row; a step that
  // runs, an answer being written or a question for the person says so already.
  const thinking = Boolean(data.chatting) && !(tail?.streaming
    || (tail?.role === "work" && tail.live && tail.items!.some((item) => isExecutingActivityStatus(itemStatus(item))))
    || (tail?.role === "notice" && tail.items![0].type === "approval"));
  // A card just opened comes into sight. Before the glide measures, so that this scroll is part of where everything glides to.
  const openedCards = useRef<Set<string> | null>(null);
  const openedKey = `${ids(context)}|${ids(branches)}`;
  useLayoutEffect(() => {
    const now = [...context, ...branches].filter((item) => !item.leaving).map((item) => item.id);
    const fresh = openedCards.current && now.find((id) => !openedCards.current!.has(id));
    openedCards.current = new Set(now);
    const card = fresh ? stage.current?.querySelector<HTMLElement>(`[data-glide="${fresh}"]`) : null;
    const side = card?.closest<HTMLElement>(".pf-side");
    if (!card || !side) return;
    const view = side.getBoundingClientRect(), box = card.getBoundingClientRect();
    const above = box.top - view.top - SIDE_SHADE, below = box.bottom - view.bottom + SIDE_SHADE;
    if (above < 0) side.scrollTop += above;
    else if (below > 0) side.scrollTop += Math.min(above, below);
  }, [openedKey]);  // eslint-disable-line react-hooks/exhaustive-deps
  useGlide(stage, layout, measure);
  const conversationShown = useRef(conversationId);
  useEffect(() => {
    if (conversationShown.current === conversationId) return;
    conversationShown.current = conversationId;
    setDraft("");
    attached.forEach(releaseAttachmentPreview);
    setAttached([]);
    setAttachError("");
    setPicked([]);
    setDirtyOpen(false);
    setObjectiveOpen(false);
    setCommitMessage("");
    settingsTouched.current = { workers: false, tokens: false };
    following.current = true;
    setBehind(false);
    if (thread.current) thread.current.scrollTop = thread.current.scrollHeight;
    if (emptyConversation) input.current?.focus();
  }, [conversationId]);  // eslint-disable-line react-hooks/exhaustive-deps
  // Words fade, they never travel: another conversation's come in once the hub and they have reached their places.
  const [wordsHeld, setWordsHeld] = useState(false);
  if (renewing && !wordsHeld) setWordsHeld(true);
  useEffect(() => {
    if (renewing || !wordsHeld) return;
    let frame = 0;
    const check = () => {
      if (glideProgress(thread.current!) !== null || glideProgress(hub.current!) !== null) frame = requestAnimationFrame(check);
      else setWordsHeld(false);
    };
    frame = requestAnimationFrame(check);
    return () => cancelAnimationFrame(frame);
  }, [renewing, wordsHeld]);
  // The cards both conversations have stay where they are bound for, and what is in them comes into focus.
  const contentsOf = useRef(conversationId);
  useLayoutEffect(() => {
    if (contentsOf.current === conversationId) return;
    contentsOf.current = conversationId;
    if (brandNew || prefersReducedMotionNow()) return;
    for (const part of stage.current?.querySelectorAll<HTMLElement>(".pf-slot:not([data-leaving]) > .pf-card > *, .pf-loom:not([data-leaving]) > *") ?? []) {
      part.animate([{ opacity: 0 }, { opacity: 1 }], { duration: DUR.base, easing: EASE.out, fill: "backwards" }).id = "pf-contents";
    }
  }, [conversationId]);  // eslint-disable-line react-hooks/exhaustive-deps
  const share = Math.min(1, data.budget.tokens / Math.max(1, data.budget.tokenLimit));
  const done = data.tasks.filter((task) => task.state === "integrated").length;
  const leaving = new Set([...context.filter((item) => item.leaving).map((item) => item.id),
                           ...branches.filter((item) => item.leaving).map((item) => item.id),
                           ...below.filter((item) => item.leaving).map((item) => item.id),
                           ...loom.filter((item) => item.leaving).map((item) => item.id),
                           ...agents.filter((item) => item.leaving).map((item) => `agent:${item.id}`)]);
  const shown = (id: string) => context.some((item) => item.id === id);
  // A card that leaves goes with what it showed, not emptied by the change it leaves for.
  const past = useRef(data);
  useEffect(() => { if (!leaving.size) past.current = data; });
  const seen = (item: { leaving?: boolean }) => item.leaving ? past.current : data;
  const leavingView = (id: string) => leaving.has(id) ? past.current : data;
  const timeline = data.timeline ?? past.current.timeline;
  const slot = (id: string) => ({ "data-leaving": leaving.has(id) || undefined, inert: leaving.has(id) });
  const hover = (id: string) => ({ onPointerEnter: () => setHot(id), onPointerLeave: () => setHot("") });
  const shownWorkers = draftMode ? workers : data.workers;
  // What is typed is always a message to the conversation's agent; projects start from its proposals.
  const attaching = Boolean(actions.onChat);
  const imageNotice = imageAttachmentState(attached.some((item) => item.isImage), actions.imagesAllowed ?? true, actions.imageSupport, lang === "zh");
  const submit = () => {
    if (accessPending.current || renewing) return;
    const value = draft.trim();
    if (!value && !attached.length) return;
    if (imageNotice.blocked) return;
    if (actions.onChat) actions.onChat(value, attached.map((item) => ({ path: item.path, name: item.name })), tokens);
    else if (!draftMode && value) actions.onSteer?.(value);
    // What the person just said is where they are reading.
    following.current = true;
    setBehind(false);
    setDraft("");
    attached.forEach(releaseAttachmentPreview);
    setAttached([]);
    setAttachError("");
  };
  const attach = (incoming: Attachment[]) => setAttached((current) => {
    const result = appendComposerAttachments(current, incoming);
    if (result.overflowed) setAttachError(text.attachLimit);
    return result.attachments;
  });
  const unattach = (id: string) => setAttached((current) => {
    releaseAttachmentPreview(current.find((item) => item.id === id));
    return current.filter((item) => item.id !== id);
  });
  const pickAttachments = async () => {
    const paths = await actions.onPickFiles?.() || [];
    setAttachError("");
    attach(paths.map((path) => attachmentFromPath(path)));
  };
  const pasteFiles = async (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = [...event.clipboardData.files];
    if (!attaching || !pasted.length) return;
    // Taken here so an image does not also land in the field as rich content that is dropped on send.
    event.preventDefault();
    setAttachError("");
    attach(await resolveComposerFiles(pasted, setAttachError));
  };
  const dropFiles = async (event: React.DragEvent) => {
    setDragging(false);
    if (!attaching || !event.dataTransfer.files.length) return;
    event.preventDefault();
    setAttachError("");
    attach(await resolveComposerFiles([...event.dataTransfer.files], setAttachError));
  };
  const startProposal = () => {
    if (!proposal || actions.busy || accessPending.current) return;
    actions.onStart?.(proposal.objective, workers, tokens, picked, accessIsFull);
    setPicked([]);
  };
  const stopping = Boolean(data.chatting && !draft.trim() && !attached.length && actions.onInterrupt);
  // New, in a conversation nothing has been said in, stays there: the field is where to begin.
  const emptyConversation = draftMode && !proposal && !data.chatting && !data.conversation?.length;
  const newThread = actions.onNewThread && (() => {
    if (emptyConversation) { input.current?.focus(); return; }
    if (!actions.busy) actions.onNewThread?.();
  });
  const settingUp = draftMode;
  const addFiles = async () => {
    const paths = await actions.onPickFiles?.() || [];
    if (!paths.length) return;
    if (draftMode) setPicked((list) => [...list, ...paths.filter((path) => !list.includes(path))]);
    else actions.onAttach?.(paths);
  };

  return (
    <div className="pf-stage" ref={stage} data-state={data.state} data-phase={data.phase} data-renewing={renewing || wordsHeld || undefined}>
      <svg className="pf-cables" aria-hidden="true" ref={svg}>
        {cables.map((cable) => (
          <g key={cable.id} className="pf-cable" data-cable={cable.id} data-side={cable.side} data-hot={hot === cable.id || hot === cable.id.replace("agent:", "task:") || hot === cable.id.replace("-draft", "") || undefined}
             data-leaving={leaving.has(cable.id) || undefined}>
            <path className="pf-cable-body" data-of={cable.id} d={cable.d} pathLength={100} />
            <rect className="pf-socket" data-of={cable.id} rx={4}
                  x={cable.socket.x - (cable.side === "below" ? 10 : 5)} y={cable.socket.y - (cable.side === "below" ? 5 : 10)}
                  width={cable.side === "below" ? 20 : 10} height={cable.side === "below" ? 10 : 20} />
          </g>
        ))}
        {cables.filter((cable) => cable.id.startsWith("agent:") && !leaving.has(cable.id)).map((cable) => {
          // While an agent works, the work runs out along its cable to it as a stream of beads; it stops when the
          // agent is done or waits for the person.
          const agent = data.agents.find((item) => `agent:${item.id}` === cable.id);
          return agent && !agent.done && !agent.waiting && data.state === "running"
            ? <path key={`flow:${cable.id}`} className="pf-flow" data-of={cable.id} d={cable.d} /> : null;
        })}
        {cables.filter((cable) => cable.id.startsWith("agent:")).map((cable) => {
          const agent = data.agents.find((item) => `agent:${item.id}` === cable.id);
          return agent && !agent.done ? <Pulse key={cable.id} of={cable.id} d={cable.d} step={agent.step} /> : null;
        })}
        {/* A task lands: its work runs back out of the hub into the repository. */}
        {draftMode ? null : cables.filter((cable) => cable.id === "repo").map((cable) => <Pulse key={`merge:${conversationId}`} of={cable.id} d={cable.d} step={done} back />)}
      </svg>

      <div className="pf-col pf-left pf-side">
        {shown("repo") ? (
          <div className="pf-slot pf-offset-2" data-glide="repo" {...hover("repo")} {...slot("repo")}>
            <Card id="repo" side="left" icon={<GitBranch size={12} />} label={text.repository} register={register}>
              <div className="pf-well pf-repo">
                <strong title={data.repository.name}>{data.repository.name}</strong>
                {data.repository.path ? <span className="pf-folder-path" title={data.repository.path}>{shortPath(data.repository.path)}</span> : null}
                {data.repository.commit || data.repository.subject ? (
                  <span key={data.repository.commit} className="pf-repo-commit" title={data.repository.subject || undefined}>
                    {data.repository.commit ? <code>{data.repository.commit.slice(0, 7)}</code> : null}
                    {data.repository.subject ? <span>{data.repository.subject}</span> : null}
                  </span>
                ) : null}
                {draftMode ? null : <Wash key={conversationId} on={data.repository.commit} />}
              </div>
              <div className="pf-well pf-branch"><GitBranch size={12} /><span>{data.repository.branch}</span></div>
              {actions.workspaceControls ?? (
                <button type="button" className="pf-switch" disabled={data.phase !== "draft" || actions.busy || !actions.onChooseWorkspace}
                        title={data.phase !== "draft" ? (lang === "zh" ? "已开始的项目保留原文件夹；新任务请新建会话" : "This project keeps its folder; use a new conversation for another project") : undefined}
                        onClick={() => actions.onChooseWorkspace?.()}>
                  <FolderOpen size={14} /><span>{lang === "zh" ? "选择文件夹" : "Choose folder"}</span>
                </button>
              )}
            </Card>
          </div>
        ) : null}
        {shown("scope") ? (
          // data-fit: when the stage is low, the card gives height and its rows scroll between head and foot.
          <div className="pf-slot" data-glide="scope" data-fit="" {...hover("scope")} {...slot("scope")}>
            <Card id="scope" side="left" className="pf-listed" icon={<FolderOpen size={12} />} label={text.scope} count={leavingView("scope").scope.length} register={register}>
              <div className="pf-list">
                {leavingView("scope").scope.map((item) => (
                  <div key={item.path} className="pf-well pf-row"><FolderOpen size={12} /><span>{item.path}</span><em className="pf-count">{item.count}</em></div>
                ))}
              </div>
            </Card>
          </div>
        ) : null}
        {shown("files") ? (
          // What the person handed the project: copies every episode can read, never the originals.
          <div className="pf-slot" data-glide="files" data-fit="" {...hover("files")} {...slot("files")}>
            <Card id="files" side="left" className="pf-files pf-listed" icon={<Paperclip size={12} />} label={text.files}
                  count={files.length || undefined} register={register}>
              <div className="pf-list">
              {files.map((file) => {
                const Icon = fileIcon(file.name);
                return (
                  <div key={file.path} className="pf-well pf-row pf-file" title={file.name}>
                    <span className="pf-file-mark" data-kind={fileKind(file.name)}><Icon size={12} /></span>
                    <span className="pf-file-name">{file.name}</span>
                    {file.bytes != null ? <em className="pf-file-size">{bytes(file.bytes)}</em> : null}
                    {draftMode ? (
                      <button type="button" className="pf-file-remove" aria-label={`${text.remove} ${file.name}`}
                              onClick={() => setPicked((list) => list.filter((path) => path !== file.path))}><X size={11} /></button>
                    ) : null}
                  </div>
                );
              })}
              {!files.length ? <p className="pf-empty">{text.filesEmpty}</p> : null}
              </div>
              {(draftMode ? actions.onPickFiles : actions.onAttach && actions.onPickFiles) ? (
                <button type="button" className="pf-add" disabled={actions.busy} onClick={addFiles}><Plus size={12} />{text.addFiles}</button>
              ) : null}
            </Card>
          </div>
        ) : null}
        {shown("knowledge") ? (
          <div className="pf-slot pf-offset-2" data-glide="knowledge" {...hover("knowledge")} {...slot("knowledge")}>
            <Card id="knowledge" side="left" icon={<BookMarked size={12} />} label={text.knowledge}
                  count={leavingView("knowledge").knowledge.checks + leavingView("knowledge").knowledge.notes} register={register}>
              <div className="pf-facts"><span><strong>{leavingView("knowledge").knowledge.checks}</strong> {text.checks}</span><span><strong>{leavingView("knowledge").knowledge.notes}</strong> {text.notes}</span></div>
              {leavingView("knowledge").knowledge.latest ? <p className="pf-well pf-note" title={leavingView("knowledge").knowledge.latest}><Inline text={leavingView("knowledge").knowledge.latest} /></p> : null}
            </Card>
          </div>
        ) : null}
      </div>

      <div className="pf-col pf-center">
        <div className="pf-thread" ref={thread} data-glide="thread">
          {/* The conversation: Weave is chat first, so what was said reads here like the chat's transcript. */}
          {conversation.map((message) => message.role === "user" ? (
            <Said key={message.id} message={message} workspace={data.repository.path} />
          ) : message.role === "work" ? (
            // The steps between two messages, drawn by the chat's own work log: live rows while they run, one line after.
            <div key={message.id} className="pf-work">
              <ProcessNotesContext.Provider value={SHOW_NOTES}>
                <WeaveStage items={message.items!} keepOpen={Boolean(message.live && !message.superseded)} superseded={message.superseded}
                            live={message.live} workspace={data.repository.path} foldNotes renderNote={renderNote} />
              </ProcessNotesContext.Provider>
            </div>
          ) : message.role === "notice" ? (
            <div key={message.id} className="pf-notice" data-leaving={message.items![0].leaving || undefined} inert={Boolean(message.items![0].leaving)}>
              {message.items![0].type === "approval"
                ? (actions.onApproval ? <ApprovalCard item={message.items![0]} onApproval={actions.onApproval} /> : null)
                : <ErrorRow item={message.items![0]} />}
            </div>
          ) : (
            <div key={message.id} className="pf-reply" data-streaming={message.live || undefined}>
              <MarkdownMessage content={message.text} compact glow streaming={message.streaming} messageKey={message.id}
                               workspace={data.repository.path} />
              {/* An answer is copied once its turn is over: until then this is narration, whatever follows it. */}
              {message.live ? null : <CopyReply value={message.text} label={text.copy} done={text.copied} />}
            </div>
          ))}
          {thinking ? (
            // The chat's thinking row: the bead and a lit label (the agent's commentary is read in the stage above it).
            <div className="inline-thinking pf-thinking" role="status" aria-live="polite">
              <ThinkingGlyph />
              <span className="thinking-shimmer" key={data.activity || ""} title={data.activity || undefined}>{data.activity || text.replying}</span>
            </div>
          ) : null}
          {behind ? (
            <div className="pf-latest">
              <button type="button" onClick={toLatest}><ArrowDown size={12} />{text.latest}</button>
            </div>
          ) : null}
          {proposal ? (
            // What the agent proposes to build: the person reads it whole and starts it, or not.
            <section className="pf-proposal" aria-label={text.proposal} data-open={objectiveOpen || undefined}>
              <header className="pf-proposal-head">
                <Sparkles size={12} /><span>{text.proposal}</span>
                {proposal.title ? <strong title={proposal.title}>{proposal.title}</strong> : null}
              </header>
              <p className="pf-proposal-text"><Inline text={proposal.objective} /></p>
              {proposal.objective.length > 240 ? (
                <button type="button" className="pf-bubble-more" onClick={() => setObjectiveOpen((value) => !value)}>
                  {objectiveOpen ? text.collapse : text.expand}
                </button>
              ) : null}
              <p className="pf-proposal-hint">{text.proposalHint}</p>
              <div className="pf-proposal-actions">
                {actions.onDismiss ? (
                  <button type="button" className="pf-attention-quiet" disabled={actions.busy} onClick={actions.onDismiss}>{text.dismissProposal}</button>
                ) : null}
                <button type="button" className="pf-proposal-start" disabled={actions.busy || accessChanging || !actions.onStart} onClick={startProposal}>
                  <Play size={12} />{text.startProposal}
                </button>
              </div>
            </section>
          ) : null}
          {data.objective ? (
            <>
              {/* An objective is often a page of spec: three lines, and the rest on request, so the hub stays in view. */}
              <div className={`pf-bubble${data.conversation?.length ? " pf-objective" : ""}`} data-open={objectiveOpen || undefined}>
                <span className="pf-bubble-text"><Inline text={data.objective} /></span>
                {data.objective.length > 160 ? (
                  <button type="button" className="pf-bubble-more" onClick={() => setObjectiveOpen((value) => !value)}>
                    {objectiveOpen ? text.collapse : text.expand}
                  </button>
                ) : null}
              </div>
              <div className="pf-tag"><GitBranch size={11} />{data.repository.name}/{data.repository.branch}</div>
            </>
          ) : null}
          {data.narration.summary ? <span className="pf-summary">{data.narration.summary}</span> : null}
          {/* Before a project, the hint is for an empty conversation; once people talk it only repeats itself. */}
          {data.narration.text && !(draftMode && data.conversation?.length) ? <p className="pf-narration" key={data.narration.text}>{data.narration.text}</p> : null}
          {data.goal ? (
            <div className="pf-attention pf-goal" role="status" data-live={data.goal.live || undefined}>
              <span className="pf-attention-dot" />
              <span className="pf-attention-text" title={data.goal.detail || undefined}><Inline text={data.goal.text} /></span>
              {data.goal.dismiss ? <button type="button" className="pf-attention-quiet" disabled={actions.busy} onClick={() => actions.onGoal?.("dismiss")}>{text.goalDismiss}</button> : null}
              {data.goal.action ? <button type="button" disabled={actions.busy} onClick={() => actions.onGoal?.(data.goal!.action!)}>{data.goal.action === "pause" ? text.stop : text.resume}</button> : null}
            </div>
          ) : null}
          {data.attention ? (
            <div className="pf-attention" role="status">
              <span className="pf-attention-dot" />
              <span className="pf-attention-text" title={data.attention.detail || undefined}><Inline text={data.attention.reason} /></span>
              {data.attention.action === "approval" ? (
                <button type="button" className="pf-attention-quiet" disabled={actions.busy} onClick={() => actions.onAttention?.("decline")}>{text.decline}</button>
              ) : null}
              {data.attention.action ? (
                <button type="button" disabled={actions.busy} onClick={() => actions.onAttention?.("accept")}>{text.attention[data.attention.action]}</button>
              ) : null}
            </div>
          ) : null}
          {draftMode && data.workspace?.changeCount ? (
            // A reminder, never a lock: the start builds from the last commit, so these stay out of it.
            <div className="pf-attention pf-dirty" role="status" data-open={dirtyOpen || undefined}>
              {/* One line until opened; why it matters is read with the files, not before them. */}
              <div className="pf-dirty-line">
                <span className="pf-attention-dot" />
                <span className="pf-attention-text" title={text.dirtyWhy(data.workspace.head, data.workspace.branch)}>{text.dirty(data.workspace.changeCount)}</span>
                <button type="button" className="pf-attention-quiet" aria-expanded={dirtyOpen} aria-controls="pf-dirty-details" onClick={() => setDirtyOpen((value) => !value)}>
                  {dirtyOpen ? text.dirtyHide : text.dirtyShow}
                </button>
              </div>
              <div className="pf-disclosure" id="pf-dirty-details" data-open={dirtyOpen} inert={!dirtyOpen}>
                <div className="pf-disclosure-inner"><div className="pf-dirty-body">
                  <p className="pf-dirty-why">{text.dirtyWhy(data.workspace.head, data.workspace.branch)}</p>
                  <ul className="pf-dirty-files">
                    {data.workspace.changes.slice(0, 8).map((change) => (
                      <li key={change.path} title={change.path}>
                        <code data-status={change.status}>{change.status}</code><span>{change.path}</span>
                      </li>
                    ))}
                    {data.workspace.changeCount > 8 ? <li className="pf-dirty-more">{text.dirtyMore(data.workspace.changeCount - 8)}</li> : null}
                  </ul>
                  <div className="pf-dirty-commit">
                    <input value={commitMessage} placeholder={text.commitMessage} aria-label={text.commitMessage}
                           onChange={(event) => setCommitMessage(event.target.value)} />
                    <button type="button" disabled={actions.busy || !actions.onCommitWorkspace}
                            onClick={() => actions.onCommitWorkspace?.(commitMessage.trim() || text.commitMessage, data.workspace!.changeCount)}>
                      {text.commitAll}
                    </button>
                  </div>
                  <p className="pf-dirty-hint">{text.commitHint}</p>
                </div></div>
              </div>
            </div>
          ) : null}
        </div>

        <section className="pf-composer" ref={hub} data-glide="hub" aria-label={text.hub} data-hot={hot.startsWith("agent:") || undefined}
                 data-dragging={dragging || undefined} data-typed={Boolean(draft.trim()) || undefined}
                 onDragOver={attaching ? (event) => {
                   if (!event.dataTransfer.types.includes("Files")) return;
                   event.preventDefault();
                   setDragging(true);
                 } : undefined}
                 onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
                 onDrop={(event) => void dropFiles(event)}>
          <header className="pf-composer-head" style={{ flexWrap: "wrap" }}>
            {/* One button per branch, lit in its card's colour while the card is plugged in. */}
            <span className="pf-branch-toggles" role="group" aria-label={text.branches}>
              {toggles.map(({ id, icon: Icon, label, unavailable }) => (
                <button key={id} type="button" className="pf-branch-toggle" data-branch={id} aria-pressed={!unavailable && open(id)} aria-label={label}
                        disabled={Boolean(unavailable)} aria-description={unavailable}
                        style={unavailable ? { cursor: "help" } : undefined}
                        title={unavailable ? `${label} · ${unavailable}` : open(id) ? text.foldBranch(label) : text.unfoldBranch(label)} onClick={() => toggle(id)}
                        {...(id === "agents" ? {} : hover(id))}>
                  <Icon size={12} />
                </button>
              ))}
            </span>
            {/* The quill: a new 建造 conversation in this project, straight into a fresh hub. */}
            {newThread ? <NewConversationRow className="pf-new-thread" label={text.newThread} title={text.newThread} onNew={newThread} /> : null}
            {!data.models && data.model ? <span className="pf-model">{data.model}</span> : null}
            {/* While the person writes a draft, the head says what the keys do; it fades, it never takes room from the state. */}
            {draftMode ? (
              <span className="pf-keyhint" aria-hidden="true">
                <kbd>↵</kbd>{text.keyHint[0]}<i /><kbd>⇧↵</kbd>{text.keyHint[1]}
              </span>
            ) : null}
            {!draftMode ? (
              <span className="pf-budget" title={`${data.budget.tokens.toLocaleString()} / ${data.budget.tokenLimit.toLocaleString()} token`}>
                <span>{compact(data.budget.tokens)} / {compact(data.budget.tokenLimit)}</span>
                <span className="pf-budget-track" data-high={share > 0.8 || undefined}><span style={{ transform: `scaleX(${share})` }} /></span>
              </span>
            ) : null}
            {data.controls.pause ? <button type="button" className="pf-control" aria-label={text.pause} title={text.pause} disabled={actions.busy} onClick={actions.onPause}><Pause size={12} /></button> : null}
            {data.controls.resume ? <button type="button" className="pf-control" aria-label={text.resume} title={text.resume} disabled={actions.busy} onClick={actions.onResume}><Play size={12} /></button> : null}
            {data.controls.cancel ? <button type="button" className="pf-control" aria-label={text.stop} title={text.stop} disabled={actions.busy} onClick={actions.onCancel}><Square size={11} /></button> : null}
          </header>
          <div className="pf-well pf-input">
            <ComposerAttachmentStrip attachments={attached} imagesAllowed={actions.imagesAllowed ?? true} onRemove={unattach} />
            <textarea ref={input} value={draft} rows={draftMode ? 3 : 2}
                      onChange={(event) => setDraft(event.target.value)}
                      onPaste={(event) => void pasteFiles(event)}
                      onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); } }}
                      placeholder={draftMode ? text.draftPlaceholder : text.steerPlaceholder} aria-label={text.send} />
            {/* Files for this message attach from the field's own corner, apart from the head's reference-files card. */}
            {attaching && actions.onPickFiles ? (
              <button type="button" className="pf-tool pf-attach" aria-label={text.attach} title={text.attach}
                      onClick={() => void pickAttachments()}><Paperclip size={14} /></button>
            ) : null}
          </div>
          {actions.error ? <p className="pf-error" role="alert">{actions.error}</p> : null}
          {accessError ? <p className="pf-error" role="alert">{accessError}</p> : null}
          {attachError ? <p className="pf-error" role="alert">{attachError}</p> : null}
          {imageNotice.message ? <p className="pf-error pf-image-notice" role="status" data-blocked={imageNotice.blocked || undefined}>{imageNotice.message}</p> : null}
          <footer className="pf-composer-foot">
            {/* Settings stay visible from the first draft; a live run shows its actual immutable settings. */}
            <span className="pf-settings" role="group" aria-label={text.settings}>
              {settingUp || controlledAccess ? (
                <button type="button" className="pf-chip pf-chip-button pf-access-control" data-full={accessIsFull || undefined} aria-pressed={accessIsFull}
                        aria-busy={accessChanging || undefined} data-permission={accessMode}
                        disabled={actions.busy || accessChanging || (controlledAccess && (data.chatting || !actions.onPermissionModeChange))}
                        title={controlledAccess && data.chatting ? accessLockedHint : controlledAccess
                          ? (lang === "zh" ? "切换当前对话的真实权限；新建造任务沿用完全访问 / 沙箱选择，已启动任务保留原权限" : "Change this conversation's actual access. New builds use this full access / sandbox choice; started tasks keep their original access.")
                          : `${accessIsFull ? text.fullAccessHint : text.reviewHint}\n${text.accessToggle}`}
                        onClick={() => void changeAccess()}>
                  <span className="pf-chip-mark" key={String(accessIsFull)}>{accessIsFull ? <ShieldAlert size={13} /> : <ShieldCheck size={13} />}</span>
                  <span className="pf-chip-label" key={accessMode}>{controlledAccess && !settingUp ? (lang === "zh" ? "对话：" : "Chat: ") : ""}{accessLabel}</span>
                </button>
              ) : (
                <span className="pf-chip" data-full={data.fullAccess || undefined} title={data.fullAccess ? text.fullAccessHint : text.reviewHint}>
                  <span className="pf-chip-mark">{data.fullAccess ? <ShieldAlert size={13} /> : <ShieldCheck size={13} />}</span>
                  <span className="pf-chip-label">{data.fullAccess ? text.fullAccess : text.review}</span>
                </span>
              )}
              {controlledAccess && !settingUp && Boolean(data.fullAccess) !== accessIsFull ? <span className="pf-chip pf-task-access" data-full={data.fullAccess || undefined}
                title={data.fullAccess ? text.fullAccessHint : text.reviewHint}>
                <span className="pf-chip-label">{lang === "zh" ? "任务：" : "Tasks: "}{data.fullAccess ? text.fullAccess : text.review}</span>
              </span> : null}
              {settingUp ? (
                // The budget is a level, not a number to type: four steps, lit up to the chosen one.
                // A click climbs (and wraps), the wheel and the arrow keys go either way.
                <button type="button" className="pf-chip pf-chip-button pf-budget-chip" title={text.budgetHint}
                        aria-label={`${compact(tokens)} token`} data-level={START_BUDGETS.indexOf(tokens)}
                        onClick={() => stepBudget(1, true)}
                        onWheel={(event) => { if (event.deltaY) stepBudget(event.deltaY < 0 ? 1 : -1, false); }}
                        onKeyDown={(event) => {
                          const by = event.key === "ArrowUp" || event.key === "ArrowRight" ? 1 : event.key === "ArrowDown" || event.key === "ArrowLeft" ? -1 : 0;
                          if (by) { event.preventDefault(); stepBudget(by, false); }
                        }}>
                  <span className="pf-chip-value" key={tokens}>{compact(tokens)}</span>
                  <span className="pf-level" aria-hidden="true">
                    {START_BUDGETS.map((step, index) => <i key={step} data-on={index <= START_BUDGETS.indexOf(tokens) || undefined} />)}
                  </span>
                </button>
              ) : null}
              <span className="pf-stepper" role="group" aria-label={text.agents} title={settingUp ? text.agentsHint : text.agents} data-editable={settingUp || undefined}>
                {settingUp ? (
                  <button type="button" aria-label={text.fewer} disabled={shownWorkers <= 1}
                          onClick={() => { settingsTouched.current.workers = true; setWorkers((count) => Math.max(1, count - 1)); }}><Minus size={10} strokeWidth={2.4} /></button>
                ) : null}
                <Bot size={13} /><span className="pf-stepper-value" key={shownWorkers}>{shownWorkers}</span>
                {settingUp ? (
                  <button type="button" aria-label={text.more} disabled={shownWorkers >= data.maxWorkers}
                          onClick={() => { settingsTouched.current.workers = true; setWorkers((count) => Math.min(data.maxWorkers, count + 1)); }}><Plus size={10} strokeWidth={2.4} /></button>
                ) : null}
              </span>
            </span>
            {data.models ? (
              // The model the agents think with. The chip grows its card out of the hub on a cable, and takes it back.
              <button type="button" className="pf-model pf-model-chip" data-open={modelOpen || undefined}
                      aria-expanded={modelOpen} aria-controls="pf-model-card" title={data.models.current.name}
                      onClick={toggleModel} onKeyDown={(event) => { if (event.key === "Escape") setModelOpen(false); }}
                      {...hover("model")}>
                <ProviderMark id={data.models.current.groupId} name={data.models.current.group} size="small" />
                <span key={data.models.current.selection}>{data.models.current.name}</span>
              </button>
            ) : null}
            {/* What is left of the model's quota, right against the chip it reports on; it grows the quota card. */}
            {data.models ? (
              <button type="button" className="pf-usage" data-open={usageOpen || undefined} aria-expanded={usageOpen} aria-controls="pf-usage-card"
                      title={`${usageZh ? "余额与额度" : "Balance & quota"}${usage.percent !== undefined || usage.data?.balances.length ? ` · ${usageDetail(usage.data, usage.percent, usageZh)}` : ""}`}
                      onClick={toggleUsage} onKeyDown={(event) => { if (event.key === "Escape") setUsageOpen(false); }}
                      {...hover("usage")}>
                <UsageRing percent={usage.percent} />
              </button>
            ) : null}
            {/* While the agent answers, an empty field's button stops it; anything typed is sent (it steers the answer). */}
            <button type="button" className="pf-send" data-stop={stopping || undefined}
                    disabled={!stopping && (renewing || accessChanging || (!draft.trim() && !attached.length) || imageNotice.blocked)}
                    aria-label={stopping ? text.stopReply : text.send} title={stopping ? text.stopReply : undefined}
                    onClick={stopping ? actions.onInterrupt : submit}>{stopping ? <Square size={12} /> : <ArrowUp size={15} />}</button>
          </footer>
        </section>

        {below.map((item) => {
          const view = seen(item);
          return (
        <div key={item.id} className="pf-slot pf-below" ref={item.leaving ? undefined : plan} data-glide={item.id} {...hover(item.id)} {...slot(item.id)}>
          {view.outcome ? (
            <Card id={item.id} side="below" className="pf-outcome" icon={<Check size={12} />} label={text.outcome}
                  count={text.outcomeCount(view.outcome.milestones.length)} register={register}>
              {view.outcome.summary ? (
                <p className="pf-well pf-note pf-outcome-summary" title={view.outcome.summary}><Inline text={view.outcome.summary} /></p>
              ) : null}
              {view.outcome.milestones.map((item) => (
                // What each milestone took, as a share of the largest: where the work went at a glance.
                <div key={item.index} className="pf-well pf-row pf-milestone"
                     style={{ "--pf-share": item.tokens / Math.max(1, ...view.outcome!.milestones.map((other) => other.tokens)) } as React.CSSProperties}>
                  <span className="pf-milestone-name">{text.stage(item.index)}</span>
                  <span className="pf-milestone-bar" aria-hidden="true"><span /></span>
                  <em>{text.episodes(item.episodes)} · {compact(item.tokens)}</em>
                </div>
              ))}
            </Card>
          ) : (
          <Card id={item.id} side="below" icon={<Compass size={12} />} label={text.plan(view.milestone)}
                count={view.tasks.length ? `${view.tasks.filter((task) => task.state === "integrated").length}/${view.tasks.length}` : undefined}
                under={view.tasks.length ? (
                  // The stage at a glance: one segment per task, coloured by its state; a segment fills as its task lands.
                  <div className="pf-strip" aria-hidden="true">
                    {view.tasks.map((task) => <span key={task.id} className="pf-strip-cell" data-state={task.state}><span key={task.state} /></span>)}
                  </div>
                ) : undefined} register={register}>
            {view.tasks.length ? view.tasks.map((task) => {
              const Icon = taskIcon[task.state];
              const linked = task.agentId && hot === `agent:${task.agentId}`;
              return (
                <div key={task.id} className="pf-well pf-row pf-task" data-task={task.id} data-state={task.state} data-hot={linked || hot === `lane:${task.id}` || undefined}
                     title={task.detail || task.title}
                     onPointerEnter={() => task.agentId && setHot(`task:${task.agentId}`)} onPointerLeave={() => task.agentId && setHot("")}>
                  <span className="pf-task-mark" key={task.state}><Icon size={11} strokeWidth={2.4} /></span>
                  <span className="pf-task-title"><Inline text={task.title} /></span>
                  {task.agentName ? <em className="pf-task-agent">{task.agentName}</em> : null}
                  {task.state === "integrated" ? <span className="pf-task-wash" key={`wash-${task.state}`} aria-hidden="true" /> : null}
                </div>
              );
            }) : <p className="pf-empty">{text.planEmpty}</p>}
          </Card>
          )}
        </div>
          );
        })}
      </div>

      {/* With the model card open, the agents under it fold to one line each, so both fit the column. */}
      <div className="pf-col pf-right pf-side" data-crowded={branches.some((item) => !item.leaving && item.id !== "tasks") || undefined}>
        {branches.some((item) => item.id === "tasks") ? (() => {
          // What the conversation's agent is doing this turn, as its own list says; a leaving card keeps what it showed.
          const list = leavingView("tasks").taskList ?? [];
          return (
            <div className="pf-slot" data-glide="tasks" data-fit="" {...hover("tasks")} {...slot("tasks")}>
              <Card id="tasks" side="right" className="pf-listed pf-task-list" icon={<ListChecks size={12} />} label={text.taskList}
                    count={`${list.filter((task) => task.state === "integrated").length}/${list.length}`}
                    under={<div className="pf-strip" aria-hidden="true">
                      {list.map((task) => <span key={task.id} className="pf-strip-cell" data-state={task.state}><span key={task.state} /></span>)}
                    </div>} register={register}>
                <div className="pf-list">
                  {list.map((task) => {
                    const Icon = taskIcon[task.state];
                    return (
                      <div key={task.id} className="pf-well pf-row pf-task" data-state={task.state} title={task.detail || task.title}>
                        <span className="pf-task-mark" key={task.state}><Icon size={11} strokeWidth={2.4} /></span>
                        <span className="pf-task-title"><Inline text={task.title} /></span>
                      </div>
                    );
                  })}
                </div>
              </Card>
            </div>
          );
        })() : null}
        {history && branches.some((item) => item.id === "history") ? (
          // The other 建造 conversations: one click opens it here, in the hub.
          <div className="pf-slot" data-glide="history" data-fit="" {...hover("history")} {...slot("history")}>
            <Card id="history" side="right" className="pf-listed pf-history" icon={<History size={12} />} label={text.history}
                  register={register}>
              <BuildHistory entries={history} workspace={data.repository.path} lang={lang} error={data.historyError} busy={actions.busy}
                onOpen={actions.onOpenThread} onNew={newThread}
                onRename={actions.onRenameThread} onArchive={actions.onArchiveThread} onDelete={actions.onDeleteThread} />
            </Card>
          </div>
        ) : null}
        {data.models && branches.some((item) => item.id === "model") ? (
          <div className="pf-slot pf-model-slot" id="pf-model-card" data-glide="model" {...hover("model")} {...slot("model")}
               onKeyDown={(event) => { if (event.key === "Escape") setModelOpen(false); }}>
            <Card id="model" side="right" className="pf-model-card" icon={<Sparkles size={12} />} label={text.modelCard}
                  count={reasoningText || undefined} register={register}>
              {/* While agents run, a switch reaches only the new ones; said on the model itself, so it costs the card no row. */}
              <div className="pf-well pf-model-now" key={data.models.current.selection}
                   title={data.phase === "live" && data.state === "running" ? text.modelHint : undefined}>
                <ProviderMark id={data.models.current.groupId} name={data.models.current.group} size="large" />
                <span className="pf-model-copy">
                  <strong title={data.models.current.name}>{data.models.current.name}</strong>
                  <span>{data.models.current.group}{data.models.current.vision ? <em>{text.vision}</em> : null}</span>
                </span>
              </div>
              {data.models.current.reasoning && actions.onReasoning ? (
                <div className="cp-tokens pf-model-reasoning">
                  <ReasoningControl reasoning={data.models.current.reasoning} busy={actions.busy} compact onChange={actions.onReasoning} />
                </div>
              ) : null}
              {modelTotal > 1 && actions.onModel ? (
                <div className="pf-model-pick">
                  {modelTotal > 8 ? (
                    <label className="pf-model-search">
                      <Search size={12} />
                      <input type="search" value={modelQuery} placeholder={text.modelSearch} aria-label={text.modelSearch}
                             onChange={(event) => setModelQuery(event.target.value)}
                             onKeyDown={(event) => { if (event.key === "Escape" && modelQuery) { event.stopPropagation(); setModelQuery(""); } }} />
                    </label>
                  ) : null}
                  {!query ? (
                    // Where to look: the recent ones, then each provider. The tab only chooses what the list shows.
                    <div className="pf-model-tabs" role="tablist" aria-label={text.modelGroups} ref={modelTabs}
                         onWheel={(event) => { if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) event.currentTarget.scrollLeft += event.deltaY; }}>
                      {data.models.recent.length ? (
                        <button type="button" role="tab" className="pf-model-tab" aria-selected={modelView === RECENT}
                                onClick={() => setModelTab(RECENT)}><History size={12} /><span>{text.modelRecent}</span></button>
                      ) : null}
                      {data.models.groups.map((group) => (
                        <button key={group.id} type="button" role="tab" className="pf-model-tab" aria-selected={modelView === group.id}
                                title={`${group.name} · ${group.options.length}`} onClick={() => setModelTab(group.id)}>
                          <ProviderMark id={group.id} name={group.name} size="small" /><span>{group.name}</span>
                        </button>
                      ))}
                    </div>
                  ) : null}
                  <div className="pf-model-options" role={query ? undefined : "tabpanel"} key={query ? "search" : modelView}>
                    {modelList.length ? modelList.map((option) => {
                      const chosen = option.selection === data.models!.current.selection;
                      return (
                        <button key={option.selection} type="button" className="pf-model-option" aria-pressed={chosen}
                                disabled={actions.busy || Boolean(switching)} title={`${option.name} · ${option.group}`}
                                onClick={() => { if (!chosen) { setSwitching(option.selection); actions.onModel?.(option.selection); } }}>
                          {mixed ? <ProviderMark id={option.groupId} name={option.group} size="small" /> : null}
                          <span>{option.name}</span>
                          {switching === option.selection ? <LoaderCircle size={12} className="pf-busy" />
                            : chosen ? <Check size={12} strokeWidth={2.4} /> : null}
                        </button>
                      );
                    }) : <p className="pf-empty">{text.modelNone}</p>}
                  </div>
                </div>
              ) : null}
            </Card>
          </div>
        ) : null}
        {data.models && branches.some((item) => item.id === "usage") ? (
          // What is left of the model's plan, window by window, or the money on its account.
          <div className="pf-slot pf-usage-slot" id="pf-usage-card" data-glide="usage" {...hover("usage")} {...slot("usage")}
               onKeyDown={(event) => { if (event.key === "Escape") setUsageOpen(false); }}>
            <Card id="usage" side="right" className="pf-usage-card" icon={<Gauge size={12} />} label={usageZh ? "额度" : "Quota"}
                  count={usage.data?.provider || undefined} register={register}>
              {!usage.data ? <p className="pf-empty">{usageZh ? "正在查询…" : "Loading…"}</p> : null}
              {usage.data?.windows.map((row) => (
                <div key={row.label} className="pf-well pf-usage-row" data-low={row.remainingPercent <= 20 || undefined}>
                  <span>{usageWindowLabel(row.label, usageZh)}</span>
                  <strong>{row.remainingPercent}%</strong>
                  <span className="pf-usage-meter" aria-hidden="true" style={{ "--pf-left": row.remainingPercent / 100 } as React.CSSProperties}><i /></span>
                  {row.resetsAt ? <em title={new Date(row.resetsAt).toLocaleString(usageZh ? "zh-CN" : undefined)}>{resetText(row.resetsAt, usageZh)}</em> : null}
                </div>
              ))}
              {usage.data?.balances.map((row) => (
                <div key={row.currency} className="pf-well pf-usage-row">
                  <span>{usageZh ? "可用余额" : "Available"}</span>
                  <strong>{row.currency} {row.total}</strong>
                  {row.toppedUp !== undefined || row.granted !== undefined ? <em>
                    {[row.toppedUp !== undefined ? `${usageZh ? "充值" : "Paid"} ${row.toppedUp}` : "", row.granted !== undefined ? `${usageZh ? "赠送" : "Granted"} ${row.granted}` : ""]
                      .filter(Boolean).join(" · ")}
                  </em> : null}
                </div>
              ))}
              {usage.data?.message ? <p className="pf-usage-note" data-error={usage.data.status === "error" || undefined} role="status">{usage.data.message}</p> : null}
              {usageScopeText(usage.data, usageZh) ? <p className="pf-usage-note">{usageScopeText(usage.data, usageZh)}</p> : null}
              {usage.data ? (
                <div className="pf-usage-foot">
                  <span>{usage.data.status === "ok"
                    ? `${usage.data.stale ? (usageZh ? "上次成功 " : "Last good ") : (usageZh ? "更新于 " : "Updated ")}${new Date(usage.data.checkedAt).toLocaleTimeString(usageZh ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit", hour12: false })}`
                    : ""}</span>
                  {usage.data.dashboardUrl ? (
                    <button type="button" title={usageZh ? "打开供应商控制台" : "Open provider console"} aria-label={usageZh ? "打开供应商控制台" : "Open provider console"}
                            onClick={() => void window.loom?.openExternal?.(usage.data!.dashboardUrl!)}><ExternalLink size={12} /></button>
                  ) : null}
                  <button type="button" title={usageZh ? "刷新" : "Refresh"} aria-label={usageZh ? "刷新" : "Refresh"} disabled={usage.loading}
                          onClick={() => void usage.refresh(true)}>{usage.loading ? <LoaderCircle size={12} className="pf-busy" /> : <RefreshCw size={12} />}</button>
                </div>
              ) : null}
            </Card>
          </div>
        ) : null}
        {agents.map((agent, index) => {
          const Icon = roleIcon[agent.role];
          return (
            <div key={agent.id} className={`pf-slot pf-agent-slot${index % 2 ? " pf-offset-3" : ""}`} data-glide={`agent:${agent.id}`} {...hover(`agent:${agent.id}`)} {...slot(`agent:${agent.id}`)}
                 data-hot={hot === `task:${agent.id}` || undefined}>
              <Card id={`agent:${agent.id}`} side="right" className="pf-agent" icon={<Bot size={12} />} label={agent.name} register={register}>
                <div className="pf-well pf-agent-well" data-done={agent.done || undefined}>
                  <span className="pf-agent-mark"><Icon size={15} /></span>
                  <div className="pf-agent-copy">
                    <div className="pf-agent-line">
                      <strong>{text.roles[agent.role]}</strong>
                      {agent.done ? <span className="pf-agent-check"><Check size={8} strokeWidth={3.2} /></span> : <span className="pf-agent-live" data-waiting={agent.waiting || undefined} />}
                      <span className="pf-agent-time">{agent.elapsed}</span>
                    </div>
                    <span className="pf-agent-activity" key={`${agent.step}:${agent.activity}`} title={agent.activity}>{agent.activity}</span>
                  </div>
                  {/* A dash lights per step taken: the steps ahead are unknown, so the lit part closes in on the end
                      and reaches it when the agent is done. */}
                  <div className="pf-dashes" aria-hidden="true" style={{ "--pf-fill": agent.done ? 1 : 1 - 0.82 ** Math.max(0, agent.step) } as React.CSSProperties}>
                    <span className="pf-dashes-lit" />
                    <span className="pf-dashes-glint" key={agent.step} />
                  </div>
                </div>
              </Card>
            </div>
          );
        })}
        {!agents.length && open("agents") && data.phase === "live" && data.state === "running" ? <p className="pf-empty pf-agents-empty">{text.noAgents}</p> : null}
      </div>

      {timeline && loom.length ? (
        // The milestone woven so far: a lane per task, a span per episode, the clock along the bottom.
        <section className="pf-loom" aria-label={text.loom} data-glide="loom" {...slot("loom")}>
          <header className="pf-card-head pf-loom-head">
            <span className="pf-card-icon"><ChartGantt size={12} /></span><span>{text.loom}</span>
            <span className="pf-loom-legend" aria-hidden="true">
              {(["plan", "work", "review"] as const).map((phase) => <span key={phase} data-phase={phase}><i />{text.phases[phase]}</span>)}
            </span>
            <em className="pf-count">{text.loomStats(timeline.stats)}</em>
          </header>
          <div className="pf-loom-body">
            <div className="pf-loom-grid" aria-hidden="true">
              {timeline.ticks.map((tick) => <span key={tick.label} style={{ left: `${tick.at * 100}%` }}><b>{tick.label}</b></span>)}
              {timeline.live ? <span className="pf-loom-now"><b>{text.now}</b></span> : null}
            </div>
            {timeline.lanes.map((lane) => (
              <div key={lane.key} className="pf-loom-lane" data-hot={hot === `lane:${lane.taskId || lane.key}` || undefined}
                   onPointerEnter={() => setHot(`lane:${lane.taskId || lane.key}`)} onPointerLeave={() => setHot("")}>
                <span className="pf-loom-label">{lane.label}</span>
                <div className="pf-loom-track">
                  {timeline.spans.filter((span) => span.lane === lane.key).map((span) => (
                    <span key={span.id} className="pf-loom-span" data-phase={span.phase} data-live={span.live || undefined}
                          data-trouble={span.trouble || undefined} title={text.spanTitle(text.phases[span.phase], span.minutes, span.tokens, span.trouble)}
                          style={{ left: `${span.from * 100}%`, width: `max(4px, ${(span.to - span.from) * 100}%)` }}>
                      {span.live ? <span className="pf-agent-live" /> : null}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
