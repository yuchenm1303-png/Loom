/* Persistent-project mode: the composer as a hub, after shawn's agent-composer sketch.
 *
 * What the project draws on plugs into the composer from the left (repository, scope,
 * knowledge), the agents working on it hang off the right, the plan of the current
 * milestone hangs below, and the conversation reads above. Cards are joined to the
 * composer by thick cables of the cards' own material, so they read as plugged in.
 * Cables are measured from the rendered cards and follow any width.
 *
 * Before a project starts the composer takes its objective; while it runs, guidance.
 * Motion means an event, and nothing moves otherwise:
 *   an agent finishes a step     one pulse runs down its cable into the composer
 *   an agent arrives / leaves    its card fades in / out and its cable grows / unplugs
 *   a task changes state         its mark is born again (spring)
 * Text only fades; marks and cables move (motion-tokens.css). Styles: project-flow.css.
 * The data comes from projectOverview.ts (projectRun/overview); this file only draws it. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowUp, Bot, BookMarked, ChartGantt, Check, ChevronDown, Circle, CircleDot, Compass, File, FileCode, FileImage,
  FileSpreadsheet, FileText, FolderOpen, GitBranch, Hammer, LoaderCircle, Minus, Paperclip, Pause, Play, Plus, ShieldCheck,
  Sparkles, Square, X,
} from "lucide-react";
import { ProviderMark } from "./ModelPanel";
import { ComposerPanel, useComposerPanel } from "./composerPanel";
import { RETAIN } from "../motion/tokens";
import { prefersReducedMotionNow, useReducedMotion } from "../motion/useReducedMotion";
import "./project-flow.css";

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
  attention?: { reason: string; detail?: string; action?: "recover" | "budget" | "approval"; agentId?: string };
  model: string;
  repository: { name: string; commit: string; subject: string; branch: string };
  scope: { path: string; count: number }[];
  knowledge: { checks: number; notes: number; latest: string };
  tasks: { id: string; title: string; state: FlowTaskState; detail?: string; agentId?: string; agentName?: string }[];
  /** The model the agents run on, with a few to switch to. Built from the app's model snapshot. */
  models?: { current: ModelOption & { reasoning?: string; vision?: boolean }; options: ModelOption[] };
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
    stats: { episodes: number; trouble: number; perHour: number };
  };
  budget: { tokens: number; tokenLimit: number };
  workers: number;
  maxWorkers: number;
  controls: { pause: boolean; resume: boolean; cancel: boolean };
  agents: FlowAgent[];
}

export interface ModelOption { selection: string; name: string; group: string; groupId: string }

export interface FlowActions {
  onStart?(objective: string, workers: number, tokens: number, attachments: string[]): void;
  /** The system file dialog; resolves to the chosen paths (none when cancelled). */
  onPickFiles?(): Promise<string[]>;
  /** Files added while the program runs. */
  onAttach?(paths: string[]): void;
  onModel?(selection: string): void;
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

const roleIcon = { planner: Compass, worker: Hammer, reviewer: ShieldCheck };
/** Budgets a new program can start with; the chip cycles through them. The Host enforces it at every request. */
const START_BUDGETS = [2_000_000, 4_000_000, 8_000_000, 16_000_000];
const INPUT_MAX_HEIGHT = 240;
const taskIcon: Record<FlowTaskState, typeof Check> = { pending: Circle, working: LoaderCircle, review: CircleDot, rework: CircleDot, integrated: Check };

const zhText = {
  repository: "仓库", scope: "工作范围", knowledge: "项目知识", checks: "条检查", notes: "条笔记", plan: (n: number) => `阶段 ${n} 计划`,
  planEmpty: "规划完成后在这里显示任务", outcome: "已完成", outcomeCount: (n: number) => `${n} 个阶段`, episodes: (n: number) => `${n} 轮`,
  donePlaceholder: "目标已完成", loom: "织造时间线", phases: { plan: "规划", work: "开发", review: "复审" },
  loomStats: (s: { episodes: number; trouble: number; perHour: number }) => `${s.episodes} 轮 · 重试 ${s.trouble} · ${compact(s.perHour)}/时`,
  spanTitle: (phase: string, minutes: number, tokens: number, trouble: boolean) =>
    `${phase} · ${minutes} 分 · ${compact(tokens)} token${trouble ? " · 未正常结束，已重试" : ""}`,
  dirty: (n: number) => `仓库里有 ${n} 个未提交的改动`,
  dirtyWhy: (head: string, branch: string) => `织造从最后一次提交${head ? `（${head}）` : ""}开始，完成后合并回 ${branch || "当前分支"}；未提交的改动会挡住这一步，所以先提交它们。`,
  dirtyShow: "查看并提交", dirtyHide: "收起", dirtyMore: (n: number) => `还有 ${n} 个`, commitAll: "全部提交",
  commitMessage: "保存改动，准备开始织造", commitHint: "不想提交的文件，先加入 .gitignore 或移出仓库。", startBlocked: "先提交未提交的改动",
  now: "现在", files: "参考文件", filesEmpty: "需求文档、规格或样例数据，每一轮的 agent 都能读到", addFiles: "添加文件",
  remove: "移除", modelCard: "模型", vision: "识图", modelHint: "已开始的 agent 沿用原模型，新开始的使用所选模型", mode: "织造", stage: (n: number) => `阶段 ${n}`, notStarted: "未开始",
  draftPlaceholder: "描述一个要持续完成的目标，它会被拆成阶段和任务，独立复审后集成", steerPlaceholder: "补充要求或调整方向，下一轮生效",
  review: "独立复审", agents: "并行 agent", fewer: "减少", more: "增加", send: "发送", start: "开始", pause: "暂停", resume: "继续", stop: "停止",
  roles: { planner: "规划", worker: "开发", reviewer: "复审" }, objective: "目标", guidance: "补充要求", hub: "织造",
  attention: { recover: "重新复审并继续", budget: "追加预算", approval: "允许一次" }, decline: "拒绝", noAgents: "等待调度", budgetHint: "总 token 预算，点击切换", expand: "展开", collapse: "收起",
};
const enText: typeof zhText = {
  repository: "Repository", scope: "Scope", knowledge: "Knowledge", checks: "checks", notes: "notes", plan: (n: number) => `Milestone ${n} plan`,
  planEmpty: "Tasks appear here once the plan is made", outcome: "Complete", outcomeCount: (n: number) => `${n} milestones`,
  episodes: (n: number) => `${n} episodes`, donePlaceholder: "The objective is complete",
  loom: "Timeline", phases: { plan: "Plan", work: "Build", review: "Review" },
  loomStats: (s: { episodes: number; trouble: number; perHour: number }) => `${s.episodes} episodes · ${s.trouble} retried · ${compact(s.perHour)}/h`,
  spanTitle: (phase: string, minutes: number, tokens: number, trouble: boolean) =>
    `${phase} · ${minutes} min · ${compact(tokens)} tokens${trouble ? " · did not settle; retried" : ""}`,
  dirty: (n: number) => `The repository has ${n} uncommitted change${n === 1 ? "" : "s"}`,
  dirtyWhy: (head: string, branch: string) => `Weave starts from the last commit${head ? ` (${head})` : ""} and merges back into ${branch || "the current branch"} when done; uncommitted changes would block that, so commit them first.`,
  dirtyShow: "Review and commit", dirtyHide: "Hide", dirtyMore: (n: number) => `${n} more`, commitAll: "Commit all",
  commitMessage: "Save work before Weave", commitHint: "Add files you do not want committed to .gitignore, or move them out of the repository.",
  startBlocked: "Commit the uncommitted changes first",
  now: "now", files: "Reference files", filesEmpty: "Specs, documents or sample data; every episode can read them",
  addFiles: "Add files", remove: "Remove", modelCard: "Model", vision: "vision",
  modelHint: "Agents already started keep their model; new ones use the choice", mode: "Weave", stage: (n: number) => `milestone ${n}`, notStarted: "not started",
  draftPlaceholder: "Describe an objective to pursue; it is split into milestones and tasks, independently reviewed, then integrated",
  steerPlaceholder: "Add guidance or change direction; it applies from the next episode",
  review: "Independent review", agents: "Parallel agents", fewer: "Fewer", more: "More", send: "Send", start: "Start", pause: "Pause", resume: "Resume", stop: "Stop",
  roles: { planner: "Planner", worker: "Builder", reviewer: "Reviewer" }, objective: "Objective", guidance: "Guidance", hub: "Weave",
  attention: { recover: "Re-review and continue", budget: "Add budget", approval: "Allow once" }, decline: "Decline", noAgents: "Waiting to schedule", budgetHint: "Total token budget; click to change", expand: "More", collapse: "Less",
};
const stateWord = {
  zh: { running: "运行中", paused: "已暂停", needs_you: "等你处理", completed: "已完成", starting: "启动中" },
  en: { running: "running", paused: "paused", needs_you: "needs you", completed: "completed", starting: "starting" },
};

type Side = "left" | "right" | "below";
interface Cable { id: string; side: Side; d: string; socket: { x: number; y: number } }

function compact(value: number) {
  // 16M, 2.4M: a whole number of millions has no ".0".
  return value >= 1_000_000 ? `${Number((value / 1_000_000).toFixed(1))}M` : value >= 1000 ? `${Math.round(value / 1000)}k` : String(value);
}

/** Changes when `value` changes after the first render: keys a one-shot animation to that moment. */
function useMoment(value: unknown) {
  const [moment, setMoment] = useState(0);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
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
    // RETAIN.base: the in-place exit (base-out) plus its margin, so the fade is never cut.
    const ids = gone.map(({ item }) => item.id);
    const timer = window.setTimeout(() => setGone((list) => list.filter(({ item }) => !ids.includes(item.id))), RETAIN.base);
    return () => window.clearTimeout(timer);
  }, [gone]);
  const out: (T & { leaving?: boolean })[] = [...items];
  for (const { item, index } of gone) {
    if (!out.some((entry) => entry.id === item.id)) out.splice(Math.min(index, out.length), 0, { ...item, leaving: true });
  }
  return out;
}

/** Cables from every registered card to the hub. Cards register by id; `layout` re-measures. */
function useCables(stage: React.RefObject<HTMLDivElement | null>, hub: React.RefObject<HTMLElement | null>,
                   cards: React.RefObject<Map<string, { side: Side; node: HTMLElement }>>, layout: string) {
  const [cables, setCables] = useState<Cable[]>([]);
  const measure = useCallback(() => {
    const root = stage.current, center = hub.current;
    if (!root || !center) return;
    const base = root.getBoundingClientRect();
    const box = (r: DOMRect) => ({ left: r.left - base.left, right: r.right - base.left, top: r.top - base.top,
                                   bottom: r.bottom - base.top, height: r.height });
    const m = box(center.getBoundingClientRect());
    const entries = [...cards.current.entries()];
    const next: Cable[] = [];
    for (const side of ["left", "right"] as const) {
      const list = entries.filter(([, item]) => item.side === side)
        .map(([id, item]) => ({ id, b: box(item.node.getBoundingClientRect()) }))
        .sort((a, b) => a.b.top - b.b.top);
      list.forEach(({ id, b }, index) => {
        const y1 = Math.min(b.top + 34, b.top + b.height / 2);
        // Plugs spread down the composer's side in card order; turns stagger like cables on a loom.
        const y2 = m.top + 34 + (m.height - 68) * (list.length > 1 ? index / (list.length - 1) : 0.5);
        const x1 = side === "left" ? b.right : b.left, x2 = side === "left" ? m.left : m.right;
        const turn = side === "left" ? x2 - 18 - index * 15 : x2 + 18 + index * 15;  // a cable's width plus a gap
        const dir = y2 >= y1 ? 1 : -1, sx = x2 > x1 ? 1 : -1;
        const r = Math.max(0, Math.min(12, Math.abs(y2 - y1) / 2, Math.abs(turn - x1) - 2));
        const d = Math.abs(y2 - y1) < 1 ? `M${x1},${y1} L${x2},${y2}`
          : `M${x1},${y1} L${turn - sx * r},${y1} Q${turn},${y1} ${turn},${y1 + dir * r} L${turn},${y2 - dir * r} `
            + `Q${turn},${y2} ${turn + sx * r},${y2} L${x2},${y2}`;
        // Agents' cables run from the composer outward, so a pulse can travel to the hub reversed.
        next.push({ id, side, d, socket: { x: x2, y: y2 } });
      });
    }
    for (const [id, item] of entries.filter(([, item]) => item.side === "below")) {
      const b = box(item.node.getBoundingClientRect());
      const x = Math.max(b.left + 72, m.left + 72);
      next.push({ id, side: "below", d: `M${x},${b.top} L${x},${m.bottom}`, socket: { x, y: m.bottom } });
    }
    setCables(next);
  }, [stage, hub, cards]);
  useLayoutEffect(() => {
    measure();
    const observer = new ResizeObserver(measure);
    if (stage.current) observer.observe(stage.current);
    return () => observer.disconnect();
  }, [measure, layout]);
  return cables;
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

function Pulse({ d, step, back = false }: { d: string; step: number; back?: boolean }) {
  const moment = useMoment(step);
  return moment ? <path key={moment} className="pf-pulse" data-back={back || undefined} d={d} pathLength={100} /> : null;
}

/** One soft wash across a well when its value changes (not when it first appears). */
function Wash({ on }: { on: unknown }) {
  const moment = useMoment(on);
  return moment ? <span key={moment} className="pf-task-wash" aria-hidden="true" /> : null;
}

export function ProjectFlow({ data, actions = {}, lang = "zh" }: { data: FlowData; actions?: FlowActions; lang?: "zh" | "en" }) {
  const text = lang === "en" ? enText : zhText;
  const stage = useRef<HTMLDivElement>(null);
  const hub = useRef<HTMLElement>(null);
  const cards = useRef(new Map<string, { side: Side; node: HTMLElement }>());
  const [hot, setHot] = useState("");
  const [draft, setDraft] = useState("");
  const [objectiveOpen, setObjectiveOpen] = useState(false);
  const [dirtyOpen, setDirtyOpen] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const modelPanel = useComposerPanel("model");
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
  const register = useCallback((id: string, side: Side, node: HTMLElement | null) => {
    if (node) cards.current.set(id, { side, node }); else cards.current.delete(id);
  }, []);
  const draftMode = data.phase === "draft";
  const agents = useDeparting(data.agents);
  // Files the person picked before starting; once started, the Host's copies are what count.
  const [picked, setPicked] = useState<string[]>([]);
  const files = draftMode ? picked.map((path) => ({ name: path.split(/[\\/]/).pop() || path, bytes: undefined as number | undefined, path }))
    : (data.references || []).map((item) => ({ ...item, path: item.name }));
  const filesShown = draftMode ? Boolean(actions.onPickFiles) : files.length > 0 || Boolean(actions.onAttach);
  const contextList = [{ id: "repo" }, ...(data.scope.length ? [{ id: "scope" }] : []), ...(filesShown ? [{ id: "files" }] : []),
                       ...(data.knowledge.checks + data.knowledge.notes ? [{ id: "knowledge" }] : [])];
  const context = useDeparting(contextList);
  const layout = [context.map((item) => item.id).join(","), agents.map((agent) => agent.id).join(","), files.length,
                  data.models?.options.length ?? -1,
                  data.tasks.length, data.scope.length, data.attention ? 1 : 0, data.phase].join("|");
  const cables = useCables(stage, hub, cards, layout);
  const share = Math.min(1, data.budget.tokens / Math.max(1, data.budget.tokenLimit));
  const done = data.tasks.filter((task) => task.state === "integrated").length;
  const leaving = new Set([...context.filter((item) => item.leaving).map((item) => item.id),
                           ...agents.filter((item) => item.leaving).map((item) => `agent:${item.id}`)]);
  const shown = (id: string) => context.some((item) => item.id === id);
  const slot = (id: string) => ({ "data-leaving": leaving.has(id) || undefined });
  const hover = (id: string) => ({ onPointerEnter: () => setHot(id), onPointerLeave: () => setHot("") });
  const shownWorkers = draftMode ? workers : data.workers;
  // Uncommitted changes would stop the Host at the start (and at the merge back): the start waits for them.
  const blocked = draftMode && Boolean(data.workspace?.changeCount);
  const submit = () => {
    const value = draft.trim();
    if (!value || actions.busy || blocked) return;
    if (draftMode) { actions.onStart?.(value, workers, tokens, picked); setPicked([]); } else actions.onSteer?.(value);
    setDraft("");
  };
  const addFiles = async () => {
    const paths = await actions.onPickFiles?.() || [];
    if (!paths.length) return;
    if (draftMode) setPicked((list) => [...list, ...paths.filter((path) => !list.includes(path))]);
    else actions.onAttach?.(paths);
  };

  return (
    <div className="pf-stage" ref={stage} data-state={data.state} data-phase={data.phase}>
      <svg className="pf-cables" aria-hidden="true">
        {cables.map((cable) => (
          <g key={cable.id} className="pf-cable" data-side={cable.side} data-hot={hot === cable.id || hot === cable.id.replace("agent:", "task:") || undefined}
             data-leaving={leaving.has(cable.id) || undefined}>
            <path className="pf-cable-body" d={cable.d} pathLength={100} />
            <rect className="pf-socket" rx={4}
                  x={cable.socket.x - (cable.side === "below" ? 10 : 5)} y={cable.socket.y - (cable.side === "below" ? 5 : 10)}
                  width={cable.side === "below" ? 20 : 10} height={cable.side === "below" ? 10 : 20} />
          </g>
        ))}
        {cables.filter((cable) => cable.id.startsWith("agent:")).map((cable) => {
          const agent = data.agents.find((item) => `agent:${item.id}` === cable.id);
          return agent && !agent.done ? <Pulse key={cable.id} d={cable.d} step={agent.step} /> : null;
        })}
        {/* A task lands: its work runs back out of the hub into the repository. */}
        {cables.filter((cable) => cable.id === "repo").map((cable) => <Pulse key="merge" d={cable.d} step={done} back />)}
      </svg>

      <div className="pf-col pf-left">
        {shown("repo") ? (
          <div className="pf-slot pf-offset-2" {...hover("repo")} {...slot("repo")}>
            <Card id="repo" side="left" icon={<GitBranch size={12} />} label={text.repository} register={register}>
              <div className="pf-well pf-repo">
                <strong>{data.repository.name}</strong>
                <span key={data.repository.commit} className="pf-repo-commit">{[data.repository.commit, data.repository.subject].filter(Boolean).join(" · ")}</span>
                <Wash on={data.repository.commit} />
              </div>
              <div className="pf-well pf-branch"><GitBranch size={12} /><span>{data.repository.branch}</span><ChevronDown size={12} /></div>
            </Card>
          </div>
        ) : null}
        {shown("scope") ? (
          <div className="pf-slot" {...hover("scope")} {...slot("scope")}>
            <Card id="scope" side="left" icon={<FolderOpen size={12} />} label={text.scope} count={data.scope.length} register={register}>
              {data.scope.map((item) => (
                <div key={item.path} className="pf-well pf-row"><FolderOpen size={12} /><span>{item.path}</span><em className="pf-count">{item.count}</em></div>
              ))}
            </Card>
          </div>
        ) : null}
        {shown("files") ? (
          // What the person handed the project: copies every episode can read, never the originals.
          <div className="pf-slot" {...hover("files")} {...slot("files")}>
            <Card id="files" side="left" className="pf-files" icon={<Paperclip size={12} />} label={text.files}
                  count={files.length || undefined} register={register}>
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
              {(draftMode ? actions.onPickFiles : actions.onAttach && actions.onPickFiles) ? (
                <button type="button" className="pf-add" disabled={actions.busy} onClick={addFiles}><Plus size={12} />{text.addFiles}</button>
              ) : null}
            </Card>
          </div>
        ) : null}
        {shown("knowledge") ? (
          <div className="pf-slot pf-offset-2" {...hover("knowledge")} {...slot("knowledge")}>
            <Card id="knowledge" side="left" icon={<BookMarked size={12} />} label={text.knowledge}
                  count={data.knowledge.checks + data.knowledge.notes} register={register}>
              <div className="pf-facts"><span><strong>{data.knowledge.checks}</strong> {text.checks}</span><span><strong>{data.knowledge.notes}</strong> {text.notes}</span></div>
              {data.knowledge.latest ? <p className="pf-well pf-note" title={data.knowledge.latest}><Inline text={data.knowledge.latest} /></p> : null}
            </Card>
          </div>
        ) : null}
      </div>

      <div className="pf-col pf-center">
        <div className="pf-thread">
          {data.objective ? (
            <>
              {/* An objective is often a page of spec: three lines, and the rest on request, so the hub stays in view. */}
              <div className="pf-bubble" data-open={objectiveOpen || undefined}>
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
          {data.narration.text ? <p className="pf-narration" key={data.narration.text}>{data.narration.text}</p> : null}
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
            // What would stop a start, shown before it is tried, with the one way through it.
            <div className="pf-attention pf-dirty" role="status" data-open={dirtyOpen || undefined}>
              <div className="pf-dirty-line">
                <span className="pf-attention-dot" />
                <span className="pf-attention-text">{text.dirty(data.workspace.changeCount)}</span>
                <button type="button" className="pf-attention-quiet" onClick={() => setDirtyOpen((value) => !value)}>
                  {dirtyOpen ? text.dirtyHide : text.dirtyShow}
                </button>
              </div>
              <p className="pf-dirty-why">{text.dirtyWhy(data.workspace.head, data.workspace.branch)}</p>
              {dirtyOpen ? (
                <div className="pf-dirty-body">
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
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        <section className="pf-composer" ref={hub} aria-label={text.hub} data-hot={hot.startsWith("agent:") || undefined}>
          <header className="pf-composer-head">
            <span className="pf-mode">
              <span className="pf-mode-dot" data-state={data.state} />
              {text.mode} · {draftMode ? text.notStarted : `${text.stage(data.milestone)} · ${stateWord[lang][data.state]}`}
            </span>
            {!data.models && data.model ? <span className="pf-model">{data.model}</span> : null}
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
            <textarea ref={input} value={draft} rows={draftMode ? 3 : 2} disabled={data.state === "completed"}
                      onChange={(event) => setDraft(event.target.value)}
                      onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); } }}
                      placeholder={draftMode ? text.draftPlaceholder : data.state === "completed" ? text.donePlaceholder : text.steerPlaceholder}
                      aria-label={draftMode ? text.objective : text.guidance} />
          </div>
          {actions.error ? <p className="pf-error" role="alert">{actions.error}</p> : null}
          <footer className="pf-composer-foot">
            <span className="pf-chip"><ShieldCheck size={13} />{text.review}</span>
            {draftMode ? (
              <button type="button" className="pf-chip pf-chip-button" title={text.budgetHint}
                      onClick={() => setTokens((value) => START_BUDGETS[(START_BUDGETS.indexOf(value) + 1) % START_BUDGETS.length])}>
                <span className="pf-chip-value" key={tokens}>{compact(tokens)}</span> token
              </button>
            ) : null}
            <span className="pf-stepper" aria-label={text.agents} title={text.agents}>
              <button type="button" aria-label={text.fewer} disabled={!draftMode || shownWorkers <= 1}
                      onClick={() => setWorkers((count) => Math.max(1, count - 1))}><Minus size={10} strokeWidth={2.6} /></button>
              <Bot size={13} /><span className="pf-stepper-value" key={shownWorkers}>{shownWorkers}</span>
              <button type="button" aria-label={text.more} disabled={!draftMode || shownWorkers >= data.maxWorkers}
                      onClick={() => setWorkers((count) => Math.min(data.maxWorkers, count + 1))}><Plus size={10} strokeWidth={2.6} /></button>
            </span>
            {data.models ? (
              // The model the agents think with. Its card unfolds out of this name and folds back into it.
              <div className="cp-anchor pf-model-anchor" ref={modelPanel.anchorRef}
                   style={{ "--pf-chip": `${modelPanel.triggerRef.current?.offsetWidth ?? 120}px` } as React.CSSProperties}>
                <button ref={modelPanel.triggerRef} type="button" className="pf-model pf-model-chip" data-open={modelPanel.open || undefined}
                        aria-haspopup="dialog" aria-expanded={modelPanel.open} title={data.models.current.name} onClick={modelPanel.toggle}>
                  <ProviderMark id={data.models.current.groupId} name={data.models.current.group} size="small" />
                  <span key={data.models.current.selection}>{data.models.current.name}</span>
                  <ChevronDown size={11} />
                </button>
                <ComposerPanel panel={modelPanel} label={text.modelCard} size="s" className="pf-model-pop">
                  <div className="pf-model-pop-body">
                    <div className="pf-model-pop-head"><Sparkles size={12} /><span>{text.modelCard}</span>
                      {data.models.current.reasoning ? <em className="pf-count">{data.models.current.reasoning}</em> : null}</div>
                    <div className="pf-well pf-model-now" key={data.models.current.selection}>
                      <ProviderMark id={data.models.current.groupId} name={data.models.current.group} size="large" />
                      <span className="pf-model-copy">
                        <strong title={data.models.current.name}>{data.models.current.name}</strong>
                        <span>{data.models.current.group}{data.models.current.vision ? <em>{text.vision}</em> : null}</span>
                      </span>
                    </div>
                    {data.models.options.length && actions.onModel ? (
                      <div className="pf-model-options">
                        {data.models.options.map((option) => (
                          <button key={option.selection} type="button" className="pf-model-option" data-cp-item="" disabled={actions.busy}
                                  title={option.name} onClick={() => { actions.onModel?.(option.selection); modelPanel.close(true); }}>
                            <ProviderMark id={option.groupId} name={option.group} size="small" />
                            <span>{option.name}</span>
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {data.phase === "live" && data.state === "running" ? <p className="pf-model-hint">{text.modelHint}</p> : null}
                  </div>
                </ComposerPanel>
              </div>
            ) : null}
            <button type="button" className="pf-send" disabled={!draft.trim() || actions.busy || blocked} aria-label={draftMode ? text.start : text.send}
                    title={blocked ? text.startBlocked : undefined}
                    onClick={submit}>{actions.busy ? <LoaderCircle size={15} className="pf-busy" /> : <ArrowUp size={15} />}</button>
          </footer>
        </section>

        <div className="pf-slot pf-below" ref={plan} {...hover("plan")}>
          {data.outcome ? (
            <Card id="plan" side="below" className="pf-outcome" icon={<Check size={12} />} label={text.outcome}
                  count={text.outcomeCount(data.outcome.milestones.length)} register={register}>
              {data.outcome.summary ? (
                <p className="pf-well pf-note pf-outcome-summary" title={data.outcome.summary}><Inline text={data.outcome.summary} /></p>
              ) : null}
              {data.outcome.milestones.map((item) => (
                // What each milestone took, as a share of the largest: where the work went at a glance.
                <div key={item.index} className="pf-well pf-row pf-milestone"
                     style={{ "--pf-share": item.tokens / Math.max(1, ...data.outcome!.milestones.map((other) => other.tokens)) } as React.CSSProperties}>
                  <span className="pf-milestone-name">{text.stage(item.index)}</span>
                  <span className="pf-milestone-bar" aria-hidden="true"><span /></span>
                  <em>{text.episodes(item.episodes)} · {compact(item.tokens)}</em>
                </div>
              ))}
            </Card>
          ) : (
          <Card id="plan" side="below" icon={<Compass size={12} />} label={text.plan(data.milestone)}
                count={data.tasks.length ? `${done}/${data.tasks.length}` : undefined}
                under={data.tasks.length ? (
                  // The stage at a glance: one segment per task, coloured by its state; a segment fills as its task lands.
                  <div className="pf-strip" aria-hidden="true">
                    {data.tasks.map((task) => <span key={task.id} className="pf-strip-cell" data-state={task.state}><span key={task.state} /></span>)}
                  </div>
                ) : undefined} register={register}>
            {data.tasks.length ? data.tasks.map((task) => {
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
      </div>

      <div className="pf-col pf-right">
        {agents.map((agent, index) => {
          const Icon = roleIcon[agent.role];
          return (
            <div key={agent.id} className={`pf-slot pf-agent-slot${index % 2 ? " pf-offset-3" : ""}`} {...hover(`agent:${agent.id}`)} {...slot(`agent:${agent.id}`)}
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
                  <div className="pf-dashes" aria-hidden="true"><span className="pf-dashes-glint" key={agent.step} /></div>
                </div>
              </Card>
            </div>
          );
        })}
        {!agents.length && data.phase === "live" && data.state === "running" ? <p className="pf-empty pf-agents-empty">{text.noAgents}</p> : null}
      </div>

      {data.timeline ? (
        // The milestone woven so far: a lane per task, a span per episode, the clock along the bottom.
        <section className="pf-loom" aria-label={text.loom}>
          <header className="pf-card-head pf-loom-head">
            <span className="pf-card-icon"><ChartGantt size={12} /></span><span>{text.loom}</span>
            <span className="pf-loom-legend" aria-hidden="true">
              {(["plan", "work", "review"] as const).map((phase) => <span key={phase} data-phase={phase}><i />{text.phases[phase]}</span>)}
            </span>
            <em className="pf-count">{text.loomStats(data.timeline.stats)}</em>
          </header>
          <div className="pf-loom-body">
            <div className="pf-loom-grid" aria-hidden="true">
              {data.timeline.ticks.map((tick) => <span key={tick.label} style={{ left: `${tick.at * 100}%` }}><b>{tick.label}</b></span>)}
              {data.timeline.live ? <span className="pf-loom-now"><b>{text.now}</b></span> : null}
            </div>
            {data.timeline.lanes.map((lane) => (
              <div key={lane.key} className="pf-loom-lane" data-hot={hot === `lane:${lane.taskId || lane.key}` || undefined}
                   onPointerEnter={() => setHot(`lane:${lane.taskId || lane.key}`)} onPointerLeave={() => setHot("")}>
                <span className="pf-loom-label">{lane.label}</span>
                <div className="pf-loom-track">
                  {data.timeline!.spans.filter((span) => span.lane === lane.key).map((span) => (
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
