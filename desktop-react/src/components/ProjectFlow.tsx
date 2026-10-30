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
  ArrowUp, Bot, BookMarked, Check, ChevronDown, Circle, CircleDot, Compass, FolderOpen, GitBranch, Hammer,
  LoaderCircle, Minus, Pause, Play, Plus, ShieldCheck, Square,
} from "lucide-react";
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
  budget: { tokens: number; tokenLimit: number };
  workers: number;
  maxWorkers: number;
  controls: { pause: boolean; resume: boolean; cancel: boolean };
  agents: FlowAgent[];
}

export interface FlowActions {
  onStart?(objective: string, workers: number, tokens: number): void;
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
  planEmpty: "规划完成后在这里显示任务", mode: "织造", stage: (n: number) => `阶段 ${n}`, notStarted: "未开始",
  draftPlaceholder: "描述一个要持续完成的目标，它会被拆成阶段和任务，独立复审后集成", steerPlaceholder: "补充要求或调整方向，下一轮生效",
  review: "独立复审", agents: "并行 agent", fewer: "减少", more: "增加", send: "发送", start: "开始", pause: "暂停", resume: "继续", stop: "停止",
  roles: { planner: "规划", worker: "开发", reviewer: "复审" }, objective: "目标", guidance: "补充要求", hub: "织造",
  attention: { recover: "重新复审并继续", budget: "追加预算", approval: "允许一次" }, decline: "拒绝", noAgents: "等待调度", budgetHint: "总 token 预算，点击切换", expand: "展开", collapse: "收起",
};
const enText: typeof zhText = {
  repository: "Repository", scope: "Scope", knowledge: "Knowledge", checks: "checks", notes: "notes", plan: (n: number) => `Milestone ${n} plan`,
  planEmpty: "Tasks appear here once the plan is made", mode: "Weave", stage: (n: number) => `milestone ${n}`, notStarted: "not started",
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

/** Text with `code` spans drawn as code: plans and objectives are written in Markdown habits. */
function Inline({ text }: { text: string }) {
  const parts = text.split("`");
  return <>{parts.map((part, index) => index % 2 ? <code key={index} className="pf-code">{part}</code> : part)}</>;
}

function Pulse({ d, step }: { d: string; step: number }) {
  const moment = useMoment(step);
  return moment ? <path key={moment} className="pf-pulse" d={d} pathLength={100} /> : null;
}

export function ProjectFlow({ data, actions = {}, lang = "zh" }: { data: FlowData; actions?: FlowActions; lang?: "zh" | "en" }) {
  const text = lang === "en" ? enText : zhText;
  const stage = useRef<HTMLDivElement>(null);
  const hub = useRef<HTMLElement>(null);
  const cards = useRef(new Map<string, { side: Side; node: HTMLElement }>());
  const [hot, setHot] = useState("");
  const [draft, setDraft] = useState("");
  const [objectiveOpen, setObjectiveOpen] = useState(false);
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
  const contextList = [{ id: "repo" }, ...(data.scope.length ? [{ id: "scope" }] : []),
                       ...(data.knowledge.checks + data.knowledge.notes ? [{ id: "knowledge" }] : [])];
  const context = useDeparting(contextList);
  const layout = [context.map((item) => item.id).join(","), agents.map((agent) => agent.id).join(","),
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
  const submit = () => {
    const value = draft.trim();
    if (!value || actions.busy) return;
    if (draftMode) actions.onStart?.(value, workers, tokens); else actions.onSteer?.(value);
    setDraft("");
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
      </svg>

      <div className="pf-col pf-left">
        {shown("repo") ? (
          <div className="pf-slot pf-offset-2" {...hover("repo")} {...slot("repo")}>
            <Card id="repo" side="left" icon={<GitBranch size={12} />} label={text.repository} register={register}>
              <div className="pf-well pf-repo">
                <strong>{data.repository.name}</strong>
                <span>{[data.repository.commit, data.repository.subject].filter(Boolean).join(" · ")}</span>
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
              <span className="pf-attention-text" title={data.attention.detail || undefined}>{data.attention.reason}</span>
              {data.attention.action === "approval" ? (
                <button type="button" className="pf-attention-quiet" disabled={actions.busy} onClick={() => actions.onAttention?.("decline")}>{text.decline}</button>
              ) : null}
              {data.attention.action ? (
                <button type="button" disabled={actions.busy} onClick={() => actions.onAttention?.("accept")}>{text.attention[data.attention.action]}</button>
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
            {data.model ? <span className="pf-model">{data.model}</span> : null}
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
                      placeholder={draftMode ? text.draftPlaceholder : text.steerPlaceholder}
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
            <button type="button" className="pf-send" disabled={!draft.trim() || actions.busy} aria-label={draftMode ? text.start : text.send}
                    onClick={submit}>{actions.busy ? <LoaderCircle size={15} className="pf-busy" /> : <ArrowUp size={15} />}</button>
          </footer>
        </section>

        <div className="pf-slot pf-below" ref={plan} {...hover("plan")}>
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
                <div key={task.id} className="pf-well pf-row pf-task" data-task={task.id} data-state={task.state} data-hot={linked || undefined}
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
    </div>
  );
}
