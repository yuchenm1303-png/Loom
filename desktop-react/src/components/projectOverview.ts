/* What persistent-project mode shows, derived from the Host's projectRun/overview.
 * Pure functions, so the mapping is tested without a Host (scripts/tests/project-overview.test.mjs). */
import type { FlowAgent, FlowData, FlowTaskState } from "./ProjectFlow";
import type { ProjectRun } from "./projectRuns";

export interface OverviewAgent {
  id: string; runId: string; role: FlowAgent["role"]; state?: string; taskId?: string | null; taskTitle?: string;
  scope?: string | null; step: number; activity: string; startedAt?: string | null;
  /** A call this agent waits for a person to allow (the sandbox could not start it, say). */
  approval?: { callId: string; requestId: string; tool: string; command: string; reason: string; kind: string; error?: string } | null;
}
export interface OverviewEvent { sequence: number; runId: string; kind: string; at?: number | null; reason?: string; taskId?: string | null }
export interface ProjectProgram {
  id: string; objective: string; state: string; reason?: string; maxWorkers?: number; createdAt?: number;
  milestones: { index: number; runId: string; complete: boolean; tokens: number; episodes: number; summary: string }[];
  tokenBudget: number; maxEpisodes: number; currentRunId?: string | null;
}
export interface ProjectOverview {
  run: (ProjectRun & { ledgerPosition?: { chargedTokens: number }; ledgerTokens?: number; ledgerEpisodes?: number;
         createdAt?: number; milestone?: { index: number; programId: string }; completionRef?: string | null;
         baseCommit?: string; guidance?: string[] }) | null;
  program: ProjectProgram | null;
  children: ProjectRun[];
  integration?: ProjectRun | null;
  agents: OverviewAgent[];
  events: OverviewEvent[];
}

type Lang = "zh" | "en";
const words = {
  zh: { worked: (span: string, n: number) => `已工作 ${span} · ${n} 轮`, wholeRepo: "整个仓库", waiting: "等待调度",
        done: "目标已完成，结果已集成到本地分支。", cancelled: "项目已停止。", start: "描述一个大目标，它会被拆成阶段和任务，并行开发、独立复审后集成。",
        minutes: (m: number) => m < 1 ? "不到 1 分钟" : m < 60 ? `${m} 分钟` : `${Math.floor(m / 60)} 小时 ${m % 60} 分`,
        short: (m: number) => m < 1 ? "刚刚" : m < 60 ? `${m} 分` : `${Math.floor(m / 60)} 时`,
        budget: "token 预算已用完。", episodes: "执行轮次已用完。", agent: (n: number) => `Agent ${n}`,
        planning: "规划阶段任务", reviewingProject: "整体复审", reviewing: (t: string) => `复审 ${t}`,
        asks: (who: string, command: string, sandbox: boolean) => sandbox ? `${who} 想在沙箱外运行 ${command}` : `${who} 想运行 ${command}，等你允许`,
        waitsFor: (command: string) => `等你允许：${command}` },
  en: { worked: (span: string, n: number) => `Worked ${span} · ${n} episodes`, wholeRepo: "Whole repository", waiting: "Waiting to schedule",
        done: "Objective complete; the result is integrated into the local branch.", cancelled: "The project was stopped.",
        start: "Describe a large objective. It is split into milestones and tasks, built in parallel, independently reviewed, then integrated.",
        minutes: (m: number) => m < 1 ? "under a minute" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`,
        short: (m: number) => m < 1 ? "now" : m < 60 ? `${m}m` : `${Math.floor(m / 60)}h`,
        budget: "The token budget is used up.", episodes: "The episode limit is reached.", agent: (n: number) => `Agent ${n}`,
        planning: "Planning the milestone", reviewingProject: "Whole-project review", reviewing: (t: string) => `Reviewing ${t}`,
        asks: (who: string, command: string, sandbox: boolean) => sandbox ? `${who} wants to run ${command} outside the sandbox` : `${who} wants to run ${command}`,
        waitsFor: (command: string) => `Waiting for you: ${command}` },
};

/** What the person decides for an agent waiting on approval: allow that one call, or decline it. */
function approvalAttention(agent: OverviewAgent, who: string, lang: Lang): NonNullable<FlowData["attention"]> {
  const approval = agent.approval!;
  const command = activityText(approval.command, lang).replace(/^(运行|Running) /, "");
  // An answer the Host could not carry out is said, or the same question would simply stay.
  const failed = approval.error ? (lang === "en" ? ` (your last answer failed: ${approval.error})` : `（上次的回应没有生效：${approval.error}）`) : "";
  return { reason: words[lang].asks(who, command, approval.kind === "sandbox") + failed, detail: approval.reason, action: "approval", agentId: agent.id };
}

/** One project event as a sentence, or "" for bookkeeping a person does not need to read. */
export function eventText(event: OverviewEvent, lang: Lang = "zh"): string {
  const task = event.taskId ? `${event.taskId} ` : "";
  const zh: Record<string, string> = {
    created: "项目已创建", workflow_start: "项目开始运行", parallel_task_started: `${task}开始开发`,
    parallel_task_merged: `${task}复审通过，已合并`, parallel_aggregate_review: "全部任务已合并，开始整体复审",
    review_evidence_rejected: `${task}复审证据不足，退回补充`, reply_rejected: `${task}回复格式不对，已要求重发`,
    interrupted_episode_continued: `${task}Host 重启后继续`, transient_failure_retry: `${task}模型服务暂时不可用，稍后重试`,
    episode_limit_reached: `${task}单轮步数到上限，下一轮继续`, no_progress_retry: `${task}原地打转，已要求换个方法`,
    scope_rejected: `${task}改动超出范围，已退回`, delivered: "已交付候选结果", integrated: "已集成到本地分支",
    integration_review_reused: "源分支未变，直接复用复审结果", needs_review: "需要你处理", budget_limited: "预算用完，已暂停",
    guidance_added: "收到你的补充要求", milestone_assigned: "新阶段开始规划", cancelled: "项目已停止",
  };
  const en: Record<string, string> = {
    created: "Project created", workflow_start: "Project started", parallel_task_started: `${task}started`,
    parallel_task_merged: `${task}reviewed and merged`, parallel_aggregate_review: "All tasks merged; whole-project review",
    review_evidence_rejected: `${task}review evidence sent back`, reply_rejected: `${task}reply sent back for its format`,
    interrupted_episode_continued: `${task}continued after a Host restart`, transient_failure_retry: `${task}provider unavailable; retrying`,
    episode_limit_reached: `${task}episode bound reached; continuing`, no_progress_retry: `${task}going in circles; told to change approach`,
    scope_rejected: `${task}change outside its scope; sent back`, delivered: "Candidate delivered", integrated: "Integrated into the local branch",
    integration_review_reused: "Source unchanged; review reused", needs_review: "Needs you", budget_limited: "Budget used up; paused",
    guidance_added: "Your guidance received", milestone_assigned: "Planning a new milestone", cancelled: "Project stopped",
  };
  return (lang === "en" ? en : zh)[event.kind] || "";
}

const verbs = {
  zh: { read_workspace_text: "读取", write_workspace_text: "写入", replace_workspace_text: "修改", apply_patch: "修改",
        list_workspace_files: "查看", exec: "运行" } as Record<string, string>,
  en: { read_workspace_text: "Reading", write_workspace_text: "Writing", replace_workspace_text: "Editing", apply_patch: "Editing",
        list_workspace_files: "Listing", exec: "Running" } as Record<string, string>,
};

/** "read_workspace_text README.md" as "读取 README.md"; a command line ("python.exe -m unittest") as "运行 python -m unittest". */
export function activityText(activity: string, lang: Lang = "zh"): string {
  // An absolute path says where, not what: keep its last part (probe_readme.py, not C:\Users\...).
  const short = (word: string) => /^([A-Za-z]:)?[\\/]/.test(word) ? word.split(/[\\/]/).filter(Boolean).pop() || word : word;
  const [first, ...rest] = activity.split(" ").map(short);
  const verb = verbs[lang][first];
  if (verb) return rest.length ? `${verb} ${rest.join(" ")}` : verb;
  if (/\.exe$/i.test(first) || /^(python|node|npm|git|pytest|py)$/i.test(first)) {
    return `${verbs[lang].exec} ${[first.replace(/\.exe$/i, ""), ...rest].join(" ")}`;
  }
  return activity;
}

/** The Host's reason, readable: the coordination wrapper and the review gate's prefix are bookkeeping. */
export function humanReason(reason: string, lang: Lang = "zh"): string {
  const text = reason.replace(/^Parallel coordination stopped:\s*/, "").replace(/^Parallel task needs inspection:\s*/, "")
    .replace(/^Candidate review facts rejected:\s*/, "");
  if (text === reason) return reason;
  return (lang === "en" ? "A task's review was rejected: " : "有任务的复审未通过：") + text;
}

function branchName(ref?: string | null) {
  return ref ? ref.replace(/^refs\/heads\//, "") : "main";
}

function taskStates(run: ProjectRun, overview: ProjectOverview): Record<string, FlowTaskState> {
  const states: Record<string, FlowTaskState> = {};
  // A team task's agents run on its child run; the child maps back to its task.
  const taskOf = (agent: OverviewAgent) => agent.taskId || (run.teamChildren || []).find((item) => item.runId === agent.runId)?.taskId;
  const working = new Set(overview.agents.filter((agent) => agent.role === "worker").map(taskOf));
  const reviewing = new Set(overview.agents.filter((agent) => agent.role === "reviewer").map(taskOf));
  for (const task of run.plan) {
    const child = (run.teamChildren || []).find((item) => item.taskId === task.id);
    const merged = ["merged", "integrated"].includes(child?.state || "") || ["verified", "integrated"].includes(task.state);
    states[task.id] = run.state === "integrated" || merged ? "integrated"
      : reviewing.has(task.id) || task.state === "candidate" ? "review"
      : working.has(task.id) ? "working"
      : child?.state === "needs_review" ? "rework" : "pending";
  }
  return states;
}

export function toFlowData(overview: ProjectOverview, now: number, lang: Lang = "zh", model = "", workspaceName = ""): FlowData {
  const t = words[lang];
  const run = overview.run;
  const program = overview.program;
  if (!run) {
    return {
      phase: "draft", objective: "", narration: { summary: "", text: t.start }, milestone: 1, state: "paused", model,
      repository: { name: workspaceName || "workspace", commit: "", subject: "", branch: "main" }, scope: [],
      knowledge: { checks: 0, notes: 0, latest: "" }, tasks: [], budget: { tokens: 0, tokenLimit: 1 }, workers: 2, maxWorkers: 2,
      controls: { pause: false, resume: false, cancel: false }, agents: [],
    };
  }
  const integration = overview.integration;
  const finished = (program ? program.state === "completed" : run.state === "integrated" && run.workflowStatus === "completed");
  const stopped = run.state === "cancelled" || program?.state === "cancelled";
  // A team task refused at the budget blocks coordination; the remedy is still more budget, not a review.
  const budgetOut = run.control === "budget_limited" || program?.state === "budget_limited"
    || overview.children.some((child) => child.control === "budget_limited");
  // An unattended project has no one at its threads: an agent waiting for approval waits for the person here.
  const asking = overview.agents.findIndex((agent) => agent.approval);
  const blocked = run.state === "needs_review" || run.workflowStatus === "blocked" || program?.state === "needs_review" || budgetOut || asking >= 0;
  const paused = !blocked && (run.state === "created" || run.workflowControl === "paused" && run.workflowStatus !== "completed" && run.control !== "active");
  const state: FlowData["state"] = finished ? "completed" : stopped ? "paused" : blocked ? "needs_you" : paused ? "paused" : "running";
  const started = (program?.createdAt || run.createdAt || now / 1000) * 1000;
  const minutes = Math.max(0, Math.round((now - started) / 60000));
  const episodes = (program ? program.milestones.reduce((sum, item) => sum + item.episodes, 0) : 0) + (run.ledgerEpisodes ?? run.episodesUsed);
  const tokens = (program ? program.milestones.reduce((sum, item) => sum + item.tokens, 0) : 0) + (run.ledgerPosition?.chargedTokens ?? run.tokensUsed);
  // While the project waits for a person the amber line says why; the narration keeps the last thing that happened.
  const latest = overview.events.filter((event) => !["needs_review", "budget_limited", "parallel_blocked"].includes(event.kind))
    .map((event) => eventText(event, lang)).find(Boolean) || "";
  const reason = budgetOut ? (run.episodesUsed >= run.maxEpisodes ? t.episodes : t.budget) : (run.workflowError || run.reason || program?.reason || "");
  const scopes = new Map<string, number>();
  for (const task of run.plan) for (const scope of task.fileScopes || []) {
    const top = scope.split(/[\\/]/).filter(Boolean)[0] || scope;
    // A file at the top stays a file (README.md); anything else is its top folder.
    const key = scope.includes("/") || scope.includes("\\") || !/\.[A-Za-z0-9]+$/.test(scope) ? `${top}/` : top;
    scopes.set(key, (scopes.get(key) || 0) + 1);
  }
  const states = taskStates(run, overview);
  const titleOf = (id?: string | null) => run.plan.find((task) => task.id === id)?.instructions || "";
  // A team task that stopped for inspection blocks the whole project; "recover" reviews it again and resumes.
  const stoppedTask = overview.children.some((child) => child.state === "needs_review" && !child.activeEpisode);
  const childTask = (runId: string) => (run.teamChildren || []).find((item) => item.runId === runId)?.taskId;
  return {
    phase: "live",
    objective: program?.objective || run.objective.split("\n\nThis is milestone")[0],
    narration: { summary: t.worked(t.minutes(minutes), episodes), text: finished ? t.done : stopped ? t.cancelled : latest },
    milestone: run.milestone?.index || (program ? program.milestones.length + 1 : 1),
    state,
    attention: state !== "needs_you" ? undefined : asking >= 0 ? approvalAttention(overview.agents[asking], t.agent(asking + 1), lang)
      : { reason: humanReason(reason, lang) || (lang === "en" ? "The project stopped for inspection." : "项目停下等你检查。"),
          action: budgetOut ? "budget" : stoppedTask || (run.state === "needs_review" && !run.activeEpisode) ? "recover" : undefined },
    model,
    repository: { name: (run.repository || "").split(/[\\/]/).filter(Boolean).pop() || workspaceName, commit: (integration?.baseCommit || run.baseCommit || "").slice(0, 7),
                  subject: "", branch: branchName(run.completionRef) },
    scope: [...scopes.entries()].map(([path, count]) => ({ path, count })),
    knowledge: { checks: run.knowledge?.acceptedChecks || 0, notes: run.knowledge?.notes.length || 0,
                 latest: run.knowledge?.notes[run.knowledge.notes.length - 1] || "" },
    tasks: run.plan.map((task) => {
      const at = overview.agents.findIndex((agent) => (agent.taskId || childTask(agent.runId)) === task.id);
      return { id: task.id, title: task.instructions.split(/\.(?:\s|$)|[。\n]/)[0].slice(0, 80) || task.id, state: states[task.id],
               detail: task.instructions, agentId: at >= 0 ? overview.agents[at].id : undefined, agentName: at >= 0 ? t.agent(at + 1) : undefined };
    }),
    budget: { tokens, tokenLimit: program?.tokenBudget || run.tokenBudget },
    workers: program?.maxWorkers || run.maxWorkers || 1,
    maxWorkers: 2,
    controls: { pause: state === "running", resume: state === "paused" && !stopped && !finished, cancel: !finished && !stopped },
    agents: overview.agents.map((agent, index) => {
      const startedAt = agent.startedAt ? Date.parse(agent.startedAt) : now;
      const title = agent.role === "planner" ? t.planning
        // A team task's review runs at project scope on its own one-task run: name the task.
        : agent.role === "reviewer" ? (agent.runId !== run.id && childTask(agent.runId) ? t.reviewing(childTask(agent.runId) || "")
                                       : agent.scope === "project" ? t.reviewingProject : t.reviewing(agent.taskId || ""))
        : titleOf(agent.taskId).split(/[.。\n]/)[0];
      return { id: agent.id, role: agent.role, name: t.agent(index + 1), step: agent.step, waiting: !!agent.approval,
               activity: agent.approval ? t.waitsFor(activityText(agent.approval.command, lang).replace(/^(运行|Running) /, ""))
                 : agent.activity ? activityText(agent.activity, lang) : title || t.waiting, elapsed: t.short(Math.max(0, Math.round((now - startedAt) / 60000))) };
    }),
  };
}
