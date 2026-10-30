// Persistent-project mode's reading of projectRun/overview (src/components/projectOverview.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/projectOverview.ts", import.meta.url), "utf8");
const module = { exports: {} };
new Function("module", "exports", "require", ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText)(
  module, module.exports, () => ({}));
const { toFlowData, eventText, activityText, humanReason } = module.exports;

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
