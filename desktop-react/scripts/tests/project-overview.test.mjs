// Persistent-project mode's reading of projectRun/overview (src/components/projectOverview.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/projectOverview.ts", import.meta.url), "utf8");
const module = { exports: {} };
new Function("module", "exports", "require", ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText)(
  module, module.exports, () => ({}));
const { toFlowData, eventText, activityText, humanReason, raisedBudget, toTimeline, toModelChoice, readableError, toConversation, toHistory, whenSaid, toTurnTasks, toGoal } = module.exports;

test("a conversation's task list becomes the plan card's rows, each status its state", () => {
  const rows = toTurnTasks([{ step: "读后端", status: "completed" }, { step: "读前端", status: "in_progress" },
                            { step: "跑测试", status: "blocked", blocker: "没有 Python" }, { step: "汇总", status: "pending" }]);
  assert.deepEqual(rows.map((row) => row.state), ["integrated", "working", "rework", "pending"]);
  assert.deepEqual(rows.map((row) => row.title), ["读后端", "读前端", "跑测试", "汇总"]);
  assert.equal(rows[2].detail, "没有 Python");
  assert.equal(new Set(rows.map((row) => row.id)).size, 4);
});

test("a step closed without being done is not a tick on the list", () => {
  const steps = [{ step: "训练", status: "completed", outcome: "passed" }, { step: "（可选）RT 实验", status: "completed", outcome: "not_covered" },
                 { step: "复核", status: "completed", outcome: "failed" }, { step: "旧记录", status: "completed" }, { step: "待评", status: "completed", outcome: "not_assessed" }];
  const rows = toTurnTasks(steps);
  assert.deepEqual(rows.map((row) => row.state), ["integrated", "rework", "rework", "integrated", "integrated"]);
  assert.deepEqual(rows.map((row) => row.detail), [undefined, "未覆盖", "验收失败", undefined, undefined]);
  assert.equal(toTurnTasks(steps, "en")[1].detail, "Not covered");
});

test("a long goal reads as one line with the move the person has", () => {
  const base = { objective: "研究 AI-RAN", status: "active", phase: "working", tokens: 1_200_000, tokenBudget: 8_000_000, turns: 3, reason: "", summary: "", checked: false };
  assert.equal(toGoal(null), null);
  assert.deepEqual(toGoal(base), { text: "长目标进行中 · 1.2M / 8M", detail: "研究 AI-RAN", live: true, action: "pause", dismiss: false });
  assert.match(toGoal({ ...base, phase: "checking" }).text, /独立检查/);
  assert.match(toGoal({ ...base, phase: "approval" }).text, /等你批准/, "nothing moves until it is answered, so it never reads as running");
  const waiting = toGoal({ ...base, retryAt: Date.now() / 1000 + 600 });
  assert.match(waiting.text, /模型服务暂时不可用，\d\d:\d\d 自动重试/, "a night of 429s never reads as running");
  assert.equal(waiting.live, false);
  assert.match(toGoal({ ...base, retryAt: Date.now() / 1000 - 5 }).text, /长目标进行中/, "a retry already due is running again");
  assert.match(toGoal({ ...base, deepens: 2 }).text, /检查已通过，正用剩余时间加深（第 2 轮）/, "a pass that went on says why it is still running");
  assert.match(toGoal({ ...base, deepens: 1, phase: "checking" }).text, /独立检查/);
  assert.match(toGoal({ ...base, agents: 3 }).text, /长目标进行中，3 个子代理在工作/, "the person sees the goal is delegating");
  assert.equal(toGoal({ ...base, status: "paused" }).action, "resume");
  assert.equal(toGoal({ ...base, status: "budget_limited" }).action, "resume");
  const blocked = toGoal({ ...base, status: "blocked", reason: "缺数据" });
  assert.equal(blocked.detail, "缺数据");
  const unchecked = toGoal({ ...base, status: "complete", phase: "done" });
  assert.match(unchecked.text, /没能做完/, "an unchecked finish never reads as checked");
  assert.equal(unchecked.action, undefined);
  assert.match(toGoal({ ...base, status: "complete", phase: "done", checked: true, summary: "成立" }).text, /已通过独立检查/);
});

const now = Date.parse("2026-10-30T10:00:00Z");
const run = (extra = {}) => ({
  id: "root", revision: 3, objective: "Build ledger\n\nThis is milestone 1 of a long-running program", repository: "C:/work/ledger",
  sourceThreadId: "t", state: "running", control: "active", phase: "parallel", completionPolicy: "local_integrate",
  workflowControl: "active", workflowStatus: "active", tokensUsed: 900, tokenBudget: 4000, episodesUsed: 5, maxEpisodes: 200, maxWorkers: 2,
  createdAt: now / 1000 - 90 * 60, baseCommit: "0a1d215cafe", completionRef: "refs/heads/main",
  plan: [
    { id: "task-1", instructions: "Parser. Validate rows.", acceptance: [], state: "verified", fileScopes: ["ledger/parser.py"] },
    { id: "task-2", instructions: "Tests.", acceptance: [], state: "pending", fileScopes: ["tests"] },
    { id: "task-3", instructions: "README.", acceptance: [], state: "pending", fileScopes: ["README.md"] },
  ],
  teamChildren: [{ runId: "c2", taskId: "task-2", state: "running" }, { runId: "c3", taskId: "task-3", state: "running" }],
  knowledge: { notes: ["a", "call python directly"], acceptedChecks: 4, recoveries: {} },
  ledgerPosition: { chargedTokens: 1200 }, ledgerEpisodes: 6, ...extra,
});
const overview = (extra = {}, agents = []) => ({ run: run(extra), program: null, children: [], integration: null, agents, events: [] });

test("no project yet is the draft: the composer takes the objective", () => {
  const data = toFlowData({ run: null, program: null, children: [], agents: [], events: [] }, now, "zh", "m", "ledger");
  assert.equal(data.phase, "draft");
  assert.equal(data.repository.name, "ledger");
  assert.deepEqual(data.controls, { pause: false, resume: false, cancel: false });
});

test("a running project: tasks from the plan and its team, agents from active episodes", () => {
  const data = toFlowData(overview({}, [
    { id: "s2", runId: "c2", role: "worker", taskId: "task-2", step: 7, activity: "python.exe -m unittest", startedAt: new Date(now - 5 * 60000).toISOString() },
    { id: "s3", runId: "c3", role: "reviewer", taskId: null, step: 1, activity: "read_workspace_text README.md", startedAt: new Date(now).toISOString() },
  ]), now, "zh", "deepseek", "");
  assert.equal(data.objective, "Build ledger");  // the milestone stance is the runner's, not the person's
  assert.deepEqual(data.tasks.map((task) => task.state), ["integrated", "working", "review"]);
  assert.deepEqual(data.tasks.map((task) => task.agentName), [undefined, "Agent 1", "Agent 2"]);  // who is on each task
  assert.deepEqual(data.scope, [{ path: "ledger/", count: 1 }, { path: "tests/", count: 1 }, { path: "README.md", count: 1 }]);
  assert.equal(data.state, "running");
  assert.deepEqual(data.controls, { pause: true, resume: false, cancel: true });
  assert.equal(data.budget.tokens, 1200);
  assert.equal(data.repository.commit, "0a1d215");
  assert.deepEqual(data.agents.map((agent) => [agent.role, agent.activity, agent.elapsed]),
    [["worker", "运行 python -m unittest", "5 分"], ["reviewer", "读取 README.md", "刚刚"]]);
  assert.equal(data.narration.summary, "已工作 1 小时 30 分 · 6 轮");
  assert.equal(data.knowledge.latest, "call python directly");
});

test("out of budget needs you, with the action that resolves it", () => {
  const data = toFlowData(overview({ control: "budget_limited" }), now, "en");
  assert.equal(data.state, "needs_you");
  assert.deepEqual(data.attention, { reason: "The token budget is used up.", action: "budget" });
  assert.equal(data.controls.pause, false);
});

test("a stopped run asks for inspection and offers resume only when nothing is in flight", () => {
  const stopped = toFlowData(overview({ state: "needs_review", reason: "3 consecutive episodes failed", activeEpisode: null }), now, "en");
  assert.equal(stopped.attention.action, "recover");
  assert.equal(stopped.attention.reason, "3 consecutive episodes failed");
  const unknown = toFlowData(overview({ state: "needs_review", reason: "outcome unknown", activeEpisode: { sessionId: "s", state: "outcome_unknown" } }), now, "en");
  assert.equal(unknown.attention.action, undefined);
});

test("a finished program reads as complete and offers no controls", () => {
  const data = toFlowData({ ...overview({ state: "integrated", workflowStatus: "completed" }),
    program: { id: "p", objective: "Build ledger", state: "completed", milestones: [{ index: 1, runId: "r1", complete: false, tokens: 3000, episodes: 20, summary: "" }],
               tokenBudget: 9000, maxEpisodes: 200, createdAt: now / 1000 - 600 } }, now, "zh");
  assert.equal(data.state, "completed");
  assert.deepEqual(data.controls, { pause: false, resume: false, cancel: false });
  assert.equal(data.budget.tokens, 4200);  // earlier milestones plus the current one
  assert.equal(data.budget.tokenLimit, 9000);
});

test("events and tool calls read as sentences", () => {
  assert.equal(eventText({ sequence: 1, runId: "c", kind: "parallel_task_merged", taskId: "task-2" }), "task-2 复审通过，已合并");
  assert.equal(eventText({ sequence: 1, runId: "c", kind: "episode_prepared" }), "");
  assert.equal(activityText("write_workspace_text tests/test_cli.py", "en"), "Writing tests/test_cli.py");
  assert.equal(activityText("something else"), "something else");
});

test("live data: a team task's reviewer names its task, and paths shrink to their file", () => {
  const data = toFlowData(overview({}, [
    { id: "r3", runId: "c3", role: "reviewer", taskId: null, scope: "project", step: 2, activity: "", startedAt: null },
    { id: "s2", runId: "c2", role: "worker", taskId: "task-2", step: 3, activity: "exec", startedAt: null },
  ]), now, "zh");
  assert.equal(data.agents[0].activity, "复审 task-3");
  assert.equal(activityText(String.raw`C:\Users\me\python.exe C:\work\state\agent\probe_readme.py --x`), "运行 python probe_readme.py --x");
  assert.equal(activityText("read_workspace_text /tmp/a/b/c.txt", "en"), "Reading c.txt");
  assert.equal(activityText("cmd /c git status"), "运行 cmd /c git status");  // a switch is not a path
});

test("a blocked team task offers one way back, and its reason reads as a sentence", () => {
  const blocked = { ...overview({ control: "paused", workflowStatus: "blocked", parallelStatus: "blocked",
    reason: "Parallel coordination stopped: Parallel task needs inspection: Candidate review facts rejected: Check for task-9:3 records a different expectation" }),
    children: [{ id: "c9", state: "needs_review", activeEpisode: null }] };
  const data = toFlowData(blocked, now, "zh");
  assert.equal(data.state, "needs_you");
  assert.equal(data.attention.action, "recover");
  assert.equal(data.attention.reason, "有任务的复审未通过：Check for task-9:3 records a different expectation");
  assert.equal(humanReason("plain reason"), "plain reason");
});

test("a team task stopped at the budget asks for budget, not a review", () => {
  const data = toFlowData({ ...overview({ control: "paused", workflowStatus: "blocked",
    reason: "Parallel coordination stopped: Parallel task needs inspection: Project token budget: 15961829 charged + up to 59719 for the next request would exceed 16000000" }),
    children: [{ id: "c12", state: "running", control: "budget_limited", activeEpisode: null }] }, now, "zh");
  assert.deepEqual(data.attention, { reason: "token 预算已用完。", action: "budget" });
});

test("an agent waiting for approval asks the person, naming the command", () => {
  const approval = { callId: "c", requestId: "r", tool: "exec", command: "git -c safe.directory=* log --oneline -5",
                     reason: "Sandbox denied: git could not read its working directory", kind: "sandbox" };
  const data = toFlowData(overview({}, [{ id: "s3", runId: "c3", role: "reviewer", taskId: null, step: 4, activity: "exec", startedAt: null, approval }]), now, "zh");
  assert.equal(data.state, "needs_you");
  assert.deepEqual(data.attention, { reason: "Agent 1 想在沙箱外运行 `git -c safe.directory=* log --oneline -5`",
                                     detail: approval.reason, action: "approval", agentId: "s3" });
  assert.equal(data.agents[0].waiting, true);
  assert.equal(data.agents[0].activity, "等你允许：git -c safe.directory=* log --oneline -5");
});

test("adding budget to a milestone near the end of its program adds a real share of the program", () => {
  // Live numbers: the program had 25.7M; its fourth milestone started with the 0.8M that was left.
  assert.deepEqual(raisedBudget({ tokenBudget: 802090, maxEpisodes: 40 }, { tokenBudget: 25722279, maxEpisodes: 600 }),
                   { tokenBudget: 802090 + 6430570, maxEpisodes: 40 + 150 });
  assert.deepEqual(raisedBudget({ tokenBudget: 4000, maxEpisodes: 200 }, null), { tokenBudget: 6000, maxEpisodes: 300 });
});

test("a program its planner found complete shows the evidence and what each milestone took", () => {
  const data = toFlowData({ ...overview({ state: "integrated", workflowStatus: "completed", objectiveComplete: true, reason: "All five commands work; `python -m unittest` ran 215 tests OK." }),
    program: { id: "p", objective: "Build ledger", state: "completed", tokenBudget: 9000, maxEpisodes: 200, createdAt: now / 1000 - 600,
               milestones: [{ index: 1, runId: "r1", complete: false, tokens: 3000, episodes: 20, summary: "" },
                            { index: 2, runId: "r2", complete: true, tokens: 100, episodes: 1, summary: "" }] } }, now, "zh");
  assert.equal(data.state, "completed");
  assert.deepEqual(data.outcome, { summary: "All five commands work; `python -m unittest` ran 215 tests OK.",
    milestones: [{ index: 1, tokens: 3000, episodes: 20 }, { index: 2, tokens: 100, episodes: 1 }] });
  assert.equal(toFlowData(overview(), now, "zh").outcome, undefined);  // a running project has no outcome yet
});

test("the timeline: a lane per task in plan order, spans as shares of the clock, retries marked", () => {
  const t0 = now / 1000 - 40 * 60;
  const span = (id, phase, taskId, from, to, outcome = "episode_settled", runId = "c2") =>
    ({ id, runId, phase, taskId, start: t0 + from * 60, end: to == null ? null : t0 + to * 60, tokens: 60000, outcome });
  const data = toTimeline({ ...overview(), timeline: [
    span("p", "planning", null, 0, 4, "episode_settled", "root"), span("w3", "working", "task-3", 4, 20),
    span("w2", "working", "task-2", 4, 10), span("r2", "verifying", "task-2", 10, 12, "review_evidence_rejected"),
    span("pr", "verifying", null, 30, null, "", "root") ] }, now, "zh");
  assert.deepEqual(data.lanes.map((lane) => lane.label), ["规划", "任务 2", "任务 3", "整体复审"]);  // plan order, not arrival order
  const byId = Object.fromEntries(data.spans.map((item) => [item.id, item]));
  assert.equal(byId.p.from, 0);
  assert.equal(byId.w3.to, 0.5);
  assert.equal(byId.r2.trouble, true);
  assert.equal(byId.pr.live, true);
  assert.equal(byId.pr.to, 1);  // a running span reaches now
  assert.deepEqual(data.ticks.map((tick) => tick.label), ["10m", "20m", "30m"]);
  assert.deepEqual(data.stats, { episodes: 5, trouble: 1, perHour: 450000 });
  assert.equal(toTimeline(overview(), now, "zh"), undefined);  // no episodes yet, no timeline
});

test("the model card: the current model, recent ones, every usable one by provider; never unusable ones", () => {
  const profile = (model, groupId, extra = {}) => ({ selection: `${groupId}:${model}`, id: model, kind: "builtin", name: model, adapter: "openai",
    baseUrl: "", model, groupId, groupName: groupId.toUpperCase(), ...extra });
  const current = { ...profile("flash", "go"), provider: "go", vision: true,
    reasoning: { kind: "openai-effort", value: "high", defaultValue: "medium", source: "", options: [{ value: "high", label: "高", description: "", advanced: false }] } };
  const snapshot = { primary: current, current, activeModelId: null, recentModels: ["m3", "gone", "flash"],
    profiles: [current, profile("pro", "go"), profile("m3", "minimax"), profile("gone", "x", { available: false }),
               profile("setup", "go", { setupOnly: true }), profile("lite", "go"), profile("other", "kimi")] };
  const choice = toModelChoice(snapshot);
  assert.deepEqual(choice.current, { selection: "go:flash", name: "flash", group: "GO", groupId: "go", reasoning: current.reasoning, vision: true });
  assert.deepEqual(choice.recent.map((item) => item.selection), ["minimax:m3"]);
  // Every usable model by provider in the app's order; the current model stays in its list.
  assert.deepEqual(choice.groups.map((group) => [group.id, group.options.map((item) => item.selection)]),
                   [["go", ["go:flash", "go:pro", "go:lite"]], ["minimax", ["minimax:m3"]], ["kimi", ["kimi:other"]]]);
  assert.equal(toModelChoice({ ...snapshot, current: { ...current, reasoning: null } }).current.reasoning, undefined);
  assert.equal(toModelChoice(null), undefined);
});

test("a program's attached files reach the files card", () => {
  const data = toFlowData({ ...overview(), program: { id: "p", objective: "Build ledger", state: "running", tokenBudget: 9000, maxEpisodes: 200,
    milestones: [], references: [{ name: "spec.md", bytes: 1800, source: "C:/x/spec.md" }] } }, now, "zh");
  assert.deepEqual(data.references, [{ name: "spec.md", bytes: 1800 }]);  // where it came from stays on the Host
});

test("before a start: the repository's branch, commit and uncommitted changes", () => {
  const workspace = { git: true, branch: "dev", head: "a1b2c3d", changeCount: 2,
                      changes: [{ status: "M", path: "src/app.py" }, { status: "??", path: "notes.txt" }] };
  const data = toFlowData({ run: null, program: null, children: [], agents: [], events: [], workspace }, now, "zh", "m", "termrelay");
  assert.deepEqual(data.repository, { name: "termrelay", commit: "a1b2c3d", subject: "", branch: "dev" });
  assert.deepEqual(data.workspace, { changeCount: 2, changes: workspace.changes, head: "a1b2c3d", branch: "dev" });
  const outside = toFlowData({ run: null, program: null, children: [], agents: [], events: [], workspace: { git: false, changeCount: 0, changes: [] } }, now);
  assert.equal(outside.workspace, undefined);
});

test("Host errors read as sentences, without the IPC wrapper", () => {
  const raw = "Error invoking remote method 'loom:call': Error: commit or separate existing workspace changes before creating a project run";
  assert.equal(readableError(raw), "仓库里有未提交的改动，先提交它们再开始。");
  assert.equal(readableError(raw, "en"), "The repository has uncommitted changes; commit them before starting.");
  assert.equal(readableError("Error invoking remote method 'loom:call': Error: something new"), "something new");
});

test("refused access waits for the person to fix it and continue, not for a review", () => {
  const blocked = { ...overview({ control: "paused", workflowStatus: "blocked",
    reason: "Parallel coordination stopped: Parallel task needs inspection: The model provider refused the credential 7 times in a row: 401" }),
    children: [{ id: "c2", state: "running", control: "credential_blocked", activeEpisode: null }] };
  const data = toFlowData(blocked, now, "zh");
  assert.equal(data.state, "needs_you");
  assert.equal(data.attention.action, "resume");
  assert.match(data.attention.reason, /重新登录账号或更新 API 密钥/);
});

test("worked time stops with the project: a stop after 90 s never reads as a quarter of an hour", () => {
  const start = now / 1000 - 17 * 60;
  const stopped = { ...overview({ state: "cancelled", createdAt: start }), timeline: [
    { id: "e1", runId: "root", phase: "planning", taskId: null, start: start + 3, end: start + 24, tokens: 1, outcome: "episode_settled" },
    { id: "e2", runId: "root", phase: "working", taskId: "task-1", start: start + 25, end: start + 93, tokens: 1, outcome: "cancelled" }],
    events: [{ sequence: 9, runId: "root", kind: "cancelled_episode_settled", at: start + 93 }] };
  const data = toFlowData(stopped, now, "en");
  assert.match(data.narration.summary, /^Worked 2 min/);
  // While it runs, the clock runs.
  assert.match(toFlowData(overview({ createdAt: start }), now, "en").narration.summary, /^Worked 17 min/);
});

test("a proposal after the last project ended turns the hub to that new start", () => {
  const proposal = { id: "p1", objective: "Build a CLI", tokenBudget: 4000000, maxWorkers: 1 };
  const data = toFlowData({ ...overview({ state: "cancelled" }), proposal }, now, "zh");
  assert.equal(data.phase, "draft");
  assert.equal(data.proposal.id, "p1");
  // A running project keeps the hub; a proposal is only shown once nothing runs.
  assert.equal(toFlowData({ ...overview(), proposal }, now, "zh").phase, "live");
});

test("the conversation reads what was said and answered; what the agent says is body text", () => {
  const items = [
    { id: "u1", threadId: "t", turnId: "a", type: "user_message", text: "你好" },
    { id: "a1", threadId: "t", turnId: "a", type: "assistant_message", text: "你好！想做什么？", phase: "final_answer" },
    { id: "u2", threadId: "t", turnId: "b", type: "user_message", text: "做个 CLI" },
    { id: "c2", threadId: "t", turnId: "b", type: "assistant_message", text: "先看看仓库", phase: "commentary" },
    { id: "r2", threadId: "t", turnId: "b", type: "assistant_message", text: "", runtimeAuthored: true },
  ];
  const live = toConversation(items, true);
  assert.deepEqual(live.messages.map((m) => [m.role, m.text, Boolean(m.streaming)]),
    [["user", "你好", false], ["assistant", "你好！想做什么？", false], ["user", "做个 CLI", false], ["assistant", "先看看仓库", true]]);
  assert.equal(live.activity, "");
  const answered = toConversation([...items, { id: "a2", threadId: "t", turnId: "b", type: "assistant_message", text: "提案已放在上面" }], true);
  assert.equal(answered.messages.at(-1).streaming, true);
  // Answered and over: a stage with nothing but the agent's words has nothing to fold.
  assert.deepEqual(toConversation([...items, { id: "a2", threadId: "t", turnId: "b", type: "assistant_message", text: "提案已放在上面" }], false)
    .messages.map((m) => m.role), ["user", "assistant", "user", "assistant"]);
});

test("commentary stays body text between the stages; it folds into the stage only once the turn is answered and over", () => {
  const items = [
    { id: "u1", threadId: "t", turnId: "a", type: "user_message", text: "清理 C 盘" },
    { id: "c1", threadId: "t", turnId: "a", type: "assistant_message", phase: "commentary", text: "先看看空间" },
    { id: "p1", threadId: "t", turnId: "a", type: "process", status: "completed", argv: ["powershell"] },
    { id: "c2", threadId: "t", turnId: "a", type: "assistant_message", phase: "commentary", text: "只剩 23.4GB，扫描大目录" },
    { id: "p2", threadId: "t", turnId: "a", type: "process", status: "running", argv: ["powershell"] },
  ];
  const shape = (messages) => messages.map((m) => [m.role, m.items?.map((item) => item.id) ?? m.text]);
  // Live: what it said stays where it said it, at full size, while the steps run under it.
  const live = toConversation(items, true);
  assert.deepEqual(shape(live.messages), [["user", "清理 C 盘"], ["assistant", "先看看空间"], ["work", ["p1"]], ["assistant", "只剩 23.4GB，扫描大目录"], ["work", ["p2"]]]);
  assert.equal(live.messages.some((m) => m.streaming), false);
  // Everything said in the running turn counts as live, so no copy button until it is over, also on lines steps came after.
  assert.deepEqual(live.messages.filter((m) => m.role === "assistant").map((m) => m.live), [true, true]);
  // The newest line is the one still arriving while it is the last thing on the page.
  assert.equal(toConversation(items.slice(0, 4), true).messages.at(-1).streaming, true);
  // Stopped: nothing moves.
  const stopped = toConversation([...items, { id: "e1", threadId: "t", turnId: "a", type: "error", text: "turn_cancelled" }], false);
  assert.deepEqual(shape(stopped.messages), [...shape(live.messages), ["notice", ["e1"]]]);
  assert.deepEqual(stopped.messages.filter((m) => m.role === "assistant").map((m) => m.live), [false, false]);
  // Answered and over: one stage holds the narration, the answer remains.
  const over = toConversation([...items, { id: "a1", threadId: "t", turnId: "a", type: "assistant_message", phase: "final_answer", text: "可清理约 9GB" }], false);
  assert.deepEqual(shape(over.messages), [["user", "清理 C 盘"], ["work", ["c1", "p1", "c2", "p2"]], ["assistant", "可清理约 9GB"]]);
  assert.equal(over.messages[1].superseded, true);
  // Still running with its answer arriving: nothing folds yet.
  const answering = toConversation([...items, { id: "a1", threadId: "t", turnId: "a", type: "assistant_message", phase: "final_answer", text: "可清理" }], true);
  assert.deepEqual(answering.messages.map((m) => m.role), ["user", "assistant", "work", "assistant", "work", "assistant"]);
});

test("the conversation keeps the work between messages: one stage per run of steps, folded once anything follows", () => {
  const items = [
    { id: "u1", threadId: "t", turnId: "a", type: "user_message", text: "跑测试" },
    { id: "s1", threadId: "t", turnId: "a", type: "tool_call", toolName: "read_workspace_text", status: "completed" },
    { id: "c1", threadId: "t", turnId: "a", type: "assistant_message", phase: "commentary", text: "再跑一遍" },
    { id: "p1", threadId: "t", turnId: "a", type: "process", status: "running", argv: ["pytest"] },
  ];
  const live = toConversation(items, true);
  assert.deepEqual(live.messages.map((m) => [m.role, m.items?.map((item) => item.id) ?? null]),
    [["user", null], ["work", ["s1"]], ["assistant", null], ["work", ["p1"]]]);
  assert.equal(live.messages[3].live, true);
  assert.equal(live.messages[3].superseded, false);
  assert.equal(live.messages[1].superseded, true);
  const asked = toConversation([...items, { id: "ap", threadId: "t", turnId: "a", type: "approval", status: "pending" }], true);
  assert.deepEqual(asked.messages.map((m) => m.role), ["user", "work", "assistant", "work", "notice"]);
  assert.equal(asked.messages[3].superseded, true);
  const over = toConversation([...items, { id: "a1", threadId: "t", turnId: "a", type: "assistant_message", text: "都过了" }], false);
  assert.deepEqual(over.messages.map((m) => [m.role, Boolean(m.live), Boolean(m.superseded), Boolean(m.streaming)]),
    [["user", false, false, false], ["work", false, true, false], ["assistant", false, false, false]]);
});

test("the history card: newest first, the open one marked, time said briefly", () => {
  const at = (minutes) => new Date(now - minutes * 60000).toISOString();
  const rows = toHistory([
    { id: "a", title: "Old", updatedAt: at(3 * 1440) },
    { id: "b", title: "", updatedAt: at(0), live: true },
    { id: "c", title: "Mid", updatedAt: at(90) },
  ], "c", now, "zh");
  assert.deepEqual(rows.map((row) => row.id), ["b", "c", "a"]);
  assert.deepEqual(rows.map((row) => row.when), ["刚刚", "1 小时", "3 天"]);
  assert.equal(rows[0].title, "新对话");
  assert.equal(rows[0].live, true);
  assert.deepEqual(rows.map((row) => row.current), [false, true, false]);
  assert.equal(whenSaid(at(5), now, "en"), "5m");
  assert.equal(whenSaid("nope", now), "");
});

test("toConversation keeps the files on a message being sent, even one with no words", () => {
  const files = [{ path: "C:/tmp/shot.png", name: "shot.png" }];
  const { messages } = toConversation([
    { id: "p1", clientMessageId: "p1", threadId: "t", type: "user_message", text: "", optimisticAttachments: files },
    { id: "p2", threadId: "t", type: "user_message", text: "看看这个", optimisticAttachments: files },
    { id: "p3", threadId: "t", type: "user_message", text: "" },
  ], true);
  assert.deepEqual(messages.map((message) => [message.id, message.text, message.attachments]), [["p1", "", files], ["p2", "看看这个", files]]);
});

test("parallel whole-project reviewers name their share and put each of its tasks under review", () => {
  const shard = (id, taskIds, index) => ({ ...run(), id, teamOwnerId: "root", teamChildren: undefined, phase: "verifying",
    reviewShard: { sourceRunId: "root", candidateId: "c", taskIds, exclusive: false, index, of: 2 } });
  const plan = run().plan.map((task) => ({ ...task, state: "pending" }));
  const value = { ...overview({ phase: "verifying", teamChildren: [], plan }, [
    { id: "a1", runId: "s1", role: "reviewer", taskId: null, scope: "project", step: 1, activity: "", startedAt: null },
    { id: "a2", runId: "s2", role: "reviewer", taskId: null, scope: "project", step: 1, activity: "", startedAt: null },
  ]), children: [shard("s1", ["task-1", "task-3"], 1), shard("s2", ["task-2"], 2)] };
  const data = toFlowData(value, now, "en");
  assert.deepEqual(data.agents.map((agent) => agent.activity), ["Reviewing task-1, task-3", "Reviewing task-2"]);
  assert.deepEqual(data.tasks.map((task) => task.state), ["review", "review", "review"]);
  // Without the shards, the same agents would name no task (whole-project review).
  const plain = toFlowData({ ...value, children: [] }, now, "en");
  assert.deepEqual(plain.agents.map((agent) => agent.activity), ["Whole-project review", "Whole-project review"]);
  assert.ok(plain.tasks.every((task) => task.state !== "review"));
});

test("each parallel reviewer of the whole project has its own timeline lane, before the project review", () => {
  const shard = (id, index) => ({ ...run(), id, teamOwnerId: "root", teamChildren: undefined,
    reviewShard: { sourceRunId: "root", candidateId: "c", taskIds: [`task-${index}`], exclusive: false, index, of: 2 } });
  const at = now / 1000 - 600;
  const value = { ...overview(), children: [shard("s1", 1), shard("s2", 2)], timeline: [
    { id: "e1", runId: "s2", phase: "verifying", taskId: null, start: at, end: at + 100, tokens: 5, outcome: "episode_settled" },
    { id: "e2", runId: "s1", phase: "verifying", taskId: null, start: at, end: at + 120, tokens: 5, outcome: "episode_settled" },
    { id: "e3", runId: "root", phase: "verifying", taskId: null, start: at + 130, end: null, tokens: 0, outcome: "" },
  ] };
  const timeline = toTimeline(value, now, "zh");
  assert.deepEqual(timeline.lanes.map((lane) => lane.label), ["复审 1/2", "复审 2/2", "整体复审"]);
  assert.deepEqual(timeline.spans.map((span) => span.lane), ["shard:2", "shard:1", "project"]);
  const measured = toTimeline({ ...value, metrics: { span: 900, peak: 3, phases: {}, concurrency: {},
                                                     waits: { workerCap: 150, admission: 90 } } }, now, "zh");
  assert.equal(measured.stats.peak, 3);
  assert.equal(measured.stats.waitMinutes, 4);
});
