/**
 * The phone page over a scripted native shell, so it can be checked in a browser.
 *
 *   npx vite --port 5199
 *   open http://127.0.0.1:5199/scripts/fixtures/mobile.html
 *
 *   ?unpaired=1    the pairing screen; window.__shell.pairing(progress) drives it
 *   ?offline=1     the computer is offline
 *   ?latency=600   each Host call answers after this many ms (a phone on a real network; default 10)
 *
 * window.__shell.calls records every invoke, window.__shell.state(patch) changes the
 * connection, window.__shell.biometric sets the fingerprint answer.
 */
import type { ProjectRecord, ThreadRecord } from "../../src/types/loom";
import type { ProjectOverview } from "../../src/components/projectOverview";
const params = new URLSearchParams(window.location.search);
const THREAD = "thread-long";
const LATENCY = Math.max(10, Number(params.get("latency")) || 10);
const at = new Date(Date.now() - 180_000).toISOString();
const LONG_LINE = `const items = useMemo(() => turns.flatMap((turn) => deriveItems(turn, approvals, presentation, modelActivity, optimisticSteers)), [turns, approvals, presentation]);`;
const ANSWER = `## 原因

每次流式更新都在重新计算整段对话，轮数越多越慢。\`deriveItems\` 是主要开销：

\`\`\`ts
${LONG_LINE}
\`\`\`

## 测量

| 轮数 | 修改前 | 修改后 | 说明说明说明说明 | 另一列很长很长很长很长很长 |
| --- | --- | --- | --- | --- |
| 200 | 18 ms | 4 ms | 帧耗时 | 完整显示，表格自己横向滚动 |

${"这一段用来确认长消息不会被截断。".repeat(30)}

结尾标记：MESSAGE-END`;

const threads: ThreadRecord[] = [
  { id: THREAD, title: "修复 Host git 挂起", status: "waiting_approval", workspace: "D:/work/Loom", permissionMode: "approval", createdAt: at, updatedAt: at, currentTurnId: "turn-1", active: true, weave: false },
  { id: "thread-run", title: "长对话滚动性能", status: "running", workspace: "D:/work/Loom", permissionMode: "workspace", createdAt: at, updatedAt: at, currentTurnId: "turn-9", active: true, weave: false },
  { id: "thread-build", title: "Loom 账号页重做", status: "running", workspace: "D:/work/Loom", permissionMode: "workspace", createdAt: at, updatedAt: at, currentTurnId: "turn-8", active: true, weave: true },
  { id: "thread-done", title: "升级 Electron 44", status: "completed", workspace: "D:/work/Loom", permissionMode: "approval", createdAt: at, updatedAt: at, active: true, weave: false },
];
threads.forEach((thread) => { thread.projectId = "p1"; });
if (params.has("proposal")) { threads[2].status = "completed"; threads[2].currentTurnId = null; }
if (params.has("readability")) { threads[0].weave = true; threads[0].status = "completed"; threads[0].active = false; }
if (params.has("decision")) { Object.assign(threads[0], { status: "completed", active: false, currentTurnId: null, weave: params.get("decision") === "build" }); }
threads.push({ ...threads[0], id: "agent-1", title: "执行 Agent", parentThreadId: "thread-build", status: "waiting_approval" });
const workspace = { path: "D:/work/Loom", status: "ready" as const, git: true, branch: "main", head: "123abcd", changeCount: 0, changes: [] };
const projects: ProjectRecord[] = [{ id: "p1", name: "Loom", root: workspace.path, instructions: "", threadCount: 4 }];
const buildOverview: ProjectOverview = {
  run: params.has("proposal") ? null : { id: "run-1", revision: 1, sourceThreadId: "thread-build", objective: "重做账号页并独立验收", repository: "D:/work/Loom", state: "running", control: "active", phase: "implementing", workflowControl: "active", workflowStatus: "running", completionPolicy: "local_integrate", tokensUsed: 15000, tokenBudget: 1000000, episodesUsed: 3, maxEpisodes: 400, maxWorkers: 2, permissionMode: "workspace", plan: [{ id: "task-1", instructions: "实现账号页布局", acceptance: ["窄屏无横向溢出", "测试通过"], state: "pending" }], knowledge: { acceptedChecks: 2, notes: ["保持现有登录流程"], recoveries: {} } },
  program: params.has("proposal") ? null : { id: "program-1", objective: "重做账号页并独立验收", state: "running", tokenBudget: 1000000, maxEpisodes: 400, maxWorkers: 2, permissionMode: "workspace", milestones: [], references: [] },
  proposal: params.has("proposal") ? { id: "proposal-1", objective: "重做账号页并独立验收", title: "账号页建造方案", tokenBudget: 500000, maxWorkers: 2 } : null,
  agents: params.has("proposal") ? [] : [{ id: "agent-1", runId: "run-1", role: "worker", taskId: "task-1", step: 3, activity: "正在检查布局", approval: { callId: "agent-call", requestId: "agent-request", tool: "exec", command: "npm test", reason: "检查测试结果", kind: "sandbox" } }],
  children: [], events: [], workspace,
};
if (params.has("longgoal")) {
  Object.assign(threads[2], { status: "completed", active: false, currentTurnId: null });
  Object.assign(buildOverview, { run: null, program: null, agents: [], goal: {
    objective: "继续研究并交付报告", status: "paused", phase: "working", tokens: params.has("spent") ? 4000000 : 2400000,
    tokenBudget: 4000000, turns: 3, reason: "Stopped by the person", summary: "", checked: false, startedAt: 123,
  } });
}
if (params.has("goalcharter")) {  // a running research goal with its charter, as the phone shows it (?goalcharter=1)
  Object.assign(threads[2], { status: "completed", active: false, currentTurnId: null });
  Object.assign(buildOverview, { run: null, program: null, agents: [], goal: {
    objective: "在 E:\\ai run 的 5G NR PUSCH 神经接收机研究基础上，新增 AI 信道估计器支路：用神经网络替代 LS 信道估计，送入 LMMSE 均衡器，paired 评估 BLER，isolating channel-estimation gain。同时继续深挖神经接收机（U-Net/注意力架构、per-SNR 损失）。所有实验可复现，更新 report。",
    status: "active", phase: "working", tokens: 179000, tokenBudget: 1000000000, turns: 2, reason: "", summary: "", checked: false,
    startedAt: Date.now() / 1000 - 40 * 60, hours: 6, reviews: params.has("reviewed") ? [{ round: 1, verdict: "needs_changes", summary: "", major: 2, minor: 1 }] : [],
    charter: { deliverable: "可复现的 AI-RAN 研究扩展：(1) AI 信道估计器（neural channel estimator 替代 LS，isolating CE gain）与 (2) 神经接收机架构消融（U-Net/注意力 + per-SNR 损失），paired BLER 评估 + report 更新。所有代码、checkpoint、eval JSON 在 E:\\ai run。",
      kind: "research", current: 0, phases: ["文献调研与 novelty 检查（AI 信道估计、神经接收机架构）", "AI 信道估计器实现与训练", "神经接收机架构消融", "paired BLER 评估", "report 更新与自审"],
      criteria: [
        { id: "C1", text: "AI 信道估计器：CNN/ResNet 输入 received grid + pilot mask，输出 complex 信道估计，MSE 优于 LS 且 paired BLER 有提升", status: "open", origin: "charter" },
        { id: "C2", text: "神经接收机 U-Net/注意力变体与基线 paired 对比，给出置信区间", status: params.has("reviewed") ? "unmet" : "open", origin: "charter" },
        { id: "C3", text: "per-SNR 损失的消融结果写进报告", status: "met", origin: "charter" },
        { id: "C4", text: "所有实验一条命令可复现", status: "open", origin: "charter" },
        { id: "C5", text: "report 更新并与 eval JSON 数字一致", status: "dropped", origin: "charter" },
        ...(params.has("reviewed") ? [{ id: "C6", text: "在 12–16 dB 高 SNR 区验证 error floor", status: "open" as const, origin: "review 1" }] : []),
      ] } } });
}
if (params.has("goalagents")) {
  Object.assign(threads[2], { status: "completed", active: false, currentTurnId: null });
  Object.assign(buildOverview, { run: null, program: null, agents: [], goal: {
    objective: "独立检查研究报告", status: "active", phase: "checking", tokens: 4700000, tokenBudget: 400000000,
    turns: 4, reason: "", summary: "", checked: false,
  }, conversationAgents: params.has("emptyagents") ? [] : [
    { id: "checker-1", parentId: "thread-build", role: "goal-check", state: "running", executionRunning: true, queueDepth: 0, activity: "读取 report/report.md", step: 3 },
    { id: "line-1", parentId: "thread-build", role: "goal-line", state: "completed", executionRunning: false, queueDepth: 0, activity: "检查结果已生成", step: 5 },
  ] });
  if (params.has("oldhost")) delete buildOverview.conversationAgents;
  if (params.has("agenterror")) { buildOverview.conversationAgents = []; buildOverview.conversationAgentsError = "模拟读取失败"; }
  threads.push({ ...threads[2], id: "checker-1", title: "独立检查执行记录", weave: false, parentThreadId: "thread-build" });
}
const pendingApproval = {
  requestId: "req-1", threadId: THREAD, turnId: "turn-1", itemId: "tool-1", approvalItemId: "approval:call-1", callId: "call-1",
  requestType: "toolExecution", approvalStage: "before", retryReason: null, startedAtMs: Date.now(), toolName: "exec",
  arguments: { command: "git push origin main" }, effect: "sensitive", reason: "git push origin main", permissionMode: "approval",
  networkApprovalContext: null, availableDecisions: ["accept", "acceptForSession", "acceptWithPrefixRule", "decline"],
  proposedPrefixRule: ["git", "push"], sessionScope: { kind: "prefix", prefix: ["git", "push"] }, ruleSandbox: "policy",
};
const items = [
  { id: "u1", threadId: THREAD, turnId: "turn-1", type: "user_message", text: "修掉这个挂起，然后推到 main。", status: "completed" },
  { id: "a1", threadId: THREAD, turnId: "turn-1", type: "assistant_message", text: ANSWER, status: "completed" },
  { id: "approval:call-1", threadId: THREAD, turnId: "turn-1", type: "approval", status: "waiting", callId: "call-1", toolName: "exec",
    effect: "sensitive", reason: "git push origin main", availableDecisions: pendingApproval.availableDecisions,
    proposedPrefixRule: ["git", "push"], sessionScope: pendingApproval.sessionScope, ruleSandbox: "policy" },
];
const profile = { selection: "builtin:minimax", id: "minimax", kind: "builtin", name: "MiniMax-M3", adapter: "openai", baseUrl: "", model: "MiniMax-M3", configured: true,
  reasoning: { kind: "openai-effort", value: "medium", defaultValue: "medium", source: "catalog", options: [{ value: "low", label: "低" }, { value: "medium", label: "中" }, { value: "high", label: "高" }] } };
const models = { primary: profile, profiles: [profile, { ...profile, selection: "builtin:ling", id: "ling", name: "Ling-3.0-flash", model: "Ling-3.0-flash", reasoning: null }], activeModelId: "minimax", current: { ...profile, provider: "openai" }, recentModels: [] };

/* The Host, scripted. Sending in any conversation plays a turn the way app_server.py plays one, event by
   event and with the same item shapes: a thinking gap, streamed reasoning and commentary, tool calls with
   streamed arguments, a command with live output, a diff, then a streamed Markdown answer. A short message
   ("你好") gets a short streamed reply with no tools.
     - A thread in approval mode asks before the command; allow runs it, decline skips it.
     - Steering while a turn runs lands as a "steering" user message at the next step boundary.
     - The stop button interrupts: open items end "interrupted", the turn and thread too.
     - "修复 Host git 挂起" resumes after its pending approval is answered; "长对话滚动性能" finishes its running
       turn a moment after it is opened (not under automation, so the browser tests keep a still page).
   ?live=1 adds the "首页加载变慢" conversation; ?live=approval puts it in approval mode.
   window.__shell.liveDone resolves when the latest played turn has ended. */
const LIVE = "thread-live";
const live = params.get("live");
const liveThread: ThreadRecord = { id: LIVE, title: "首页加载变慢", status: "completed", workspace: "D:/work/Loom", permissionMode: live === "approval" ? "approval" : "workspace", createdAt: at, updatedAt: new Date().toISOString(), active: true, weave: false, projectId: "p1" };
if (live) threads.push(liveThread);

type SimTurn = { id: string; threadId: string; status: string; startedAt: string; completedAt?: string; items: Record<string, unknown>[] };
type SimRun = { turn: SimTurn; stopped: boolean; steers: Array<{ text: string; inputId: string; submittedAt: string }>; decide: ((decision: string) => void) | null; approval: Record<string, unknown> | null };
const histories = new Map<string, SimTurn[]>();
const runs = new Map<string, SimRun>();
class Stopped extends Error {}
const now = () => new Date().toISOString();
const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
let seed = 7;
const chunk = () => { seed = (seed * 9301 + 49297) % 233280; return 1 + Math.floor((seed / 233280) * 7); };
const PERF_LINES = ["> loom-web@1.4.0 check", "> node scripts/check.mjs", "", "warming up (3 runs)…", "run 1  frame 18.2 ms  ok", "run 2  frame 17.9 ms  ok", "run 3  frame 18.4 ms  ok", "slowest: deriveItems 14.1 ms", "120 tests passed", "done in 6.4s"];

/** A thread's turns as thread/read returns them; turns played here are appended to the same list. */
function history(threadId: string): SimTurn[] {
  let list = histories.get(threadId);
  if (!list) {
    list = threadId === THREAD ? [longTurn] : ((showcase[threadId]?.() ?? []) as SimTurn[]);
    histories.set(threadId, list);
  }
  return list;
}
const recordOf = (threadId: string) => threads.find((entry) => entry.id === threadId) ?? (threadId === LIVE ? liveThread : undefined);
function notify(threadId: string, method: string, payload: Record<string, unknown>) {
  emit({ type: "notification", payload: { method, params: { threadId, ...payload } } });
}
function setThread(threadId: string, patch: Partial<ThreadRecord>) {
  const record = recordOf(threadId);
  if (!record) return;
  Object.assign(record, patch, { updatedAt: now() });
  notify(threadId, "thread/updated", { thread: { ...record } });
}
function upsert(turn: SimTurn, item: Record<string, unknown>, method: "item/started" | "item/completed") {
  const index = turn.items.findIndex((entry) => entry.id === item.id);
  const full = { threadId: turn.threadId, turnId: turn.id, createdAt: now(), ...(index >= 0 ? turn.items[index] : {}), ...item, updatedAt: now() };
  if (index >= 0) turn.items[index] = full; else turn.items.push(full);
  notify(turn.threadId, method, { item: full });
  return full;
}
async function wait(run: SimRun, ms: number) {
  await sleep(ms);
  if (run.stopped) throw new Stopped();
}
const activity = (run: SimRun, phase: string) => notify(run.turn.threadId, "turn/modelActivity", { turnId: run.turn.id, phase, active: false, contentGapSeconds: 0 });
async function stream(run: SimRun, itemId: string, field: "text" | "reasoning", text: string, pace = 30) {
  for (let cursor = 0; cursor < text.length;) {
    const piece = text.slice(cursor, cursor += chunk());
    const item = run.turn.items.find((entry) => entry.id === itemId)!;
    item[field] = `${String(item[field] ?? "")}${piece}`;
    notify(run.turn.threadId, "item/delta", { turnId: run.turn.id, itemId, delta: { [field]: piece } });
    await wait(run, pace + (seed % 3) * 12);
  }
}
let stepCount = 0;
async function say(run: SimRun, phase: "commentary" | "final_answer", text: string, reasoning = "") {
  const id = `assistant:${run.turn.id}:s${++stepCount}`;
  upsert(run.turn, { id, type: "assistant_message", status: "streaming" }, "item/started");
  if (reasoning) await stream(run, id, "reasoning", reasoning, 26);
  await stream(run, id, "text", text, phase === "final_answer" ? 22 : 30);
  upsert(run.turn, { id, type: "assistant_message", status: "completed", text, reasoning: reasoning || undefined, phase, stepId: id }, "item/completed");
}
/** Steering sent while the turn ran, delivered at a step boundary as the Host does. */
function deliverSteers(run: SimRun) {
  const delivered = run.steers.splice(0);
  for (const steer of delivered) {
    upsert(run.turn, { id: `user:${steer.inputId}`, type: "user_message", status: "completed", text: steer.text, source: "steering", inputId: steer.inputId, submittedAt: steer.submittedAt }, "item/completed");
  }
  return delivered.map((steer) => steer.text);
}
async function tool(run: SimRun, callId: string, toolName: string, args: Record<string, unknown>, result: () => Promise<Record<string, unknown>>) {
  const id = `tool:${callId}`;
  upsert(run.turn, { id, type: "tool_call", status: "streaming_arguments", callId, toolName }, "item/started");
  const json = JSON.stringify(args);
  for (let cursor = 0; cursor < json.length;) {
    const piece = json.slice(cursor, cursor += 6);
    notify(run.turn.threadId, "item/delta", { turnId: run.turn.id, itemId: id, delta: { kind: "tool_call_argument", callId, toolName, arguments: piece } });
    await wait(run, 24);
  }
  upsert(run.turn, { id, type: "tool_call", status: "running", callId, toolName, arguments: args }, "item/completed");
  activity(run, "running");
  const output = await result();
  upsert(run.turn, { id, type: "tool_call", status: "completed", callId, toolName, arguments: args, ok: true, ...output }, "item/completed");
}
async function runCommand(run: SimRun, command: string, lines: string[]) {
  const id = `process:${run.turn.id}:${++stepCount}`;
  upsert(run.turn, { id, type: "process", status: "running", processId: id, argv: command.split(" "), cwd: "D:/work/Loom", command, stdout: "" }, "item/started");
  let out = "";
  for (const line of lines) {
    out += `${line}\n`;
    upsert(run.turn, { id, stdout: out }, "item/completed");
    await wait(run, 240);
  }
  upsert(run.turn, { id, status: "completed", stdout: out, returncode: 0 }, "item/completed");
  return { content: out };
}
/** The approval card, then the person's answer. */
async function ask(run: SimRun, callId: string, command: string): Promise<boolean> {
  const decisions = ["accept", "acceptForSession", "acceptWithPrefixRule", "decline"];
  const prefix = command.split(" ").slice(0, 2);
  upsert(run.turn, { id: `tool:${callId}`, type: "tool_call", status: "started", callId, toolName: "exec", arguments: { command } }, "item/started");
  upsert(run.turn, { id: `approval:${callId}`, type: "approval", status: "waiting", callId, toolName: "exec", effect: "sensitive", reason: command, availableDecisions: decisions, proposedPrefixRule: prefix, sessionScope: { kind: "prefix", prefix }, ruleSandbox: "policy" }, "item/started");
  run.approval = { ...pendingApproval, requestId: `req-${callId}`, threadId: run.turn.threadId, turnId: run.turn.id, itemId: `tool:${callId}`, approvalItemId: `approval:${callId}`, callId, startedAtMs: Date.now(), arguments: { command }, reason: command, permissionMode: "approval", availableDecisions: decisions, proposedPrefixRule: prefix, sessionScope: { kind: "prefix", prefix } };
  setThread(run.turn.threadId, { status: "waiting_approval" });
  notify(run.turn.threadId, "approval/requested", { approval: run.approval });
  const decision = await new Promise<string>((resolve) => { run.decide = resolve; });
  run.decide = null; run.approval = null;
  if (run.stopped) throw new Stopped();
  const allowed = decision.startsWith("accept");
  upsert(run.turn, { id: `approval:${callId}`, status: allowed ? "approved" : "declined" }, "item/completed");
  setThread(run.turn.threadId, { status: "running" });
  if (!allowed) upsert(run.turn, { id: `tool:${callId}`, status: "completed", ok: false, error: "用户拒绝了这次执行" }, "item/completed");
  return allowed;
}
function finish(run: SimRun, status: "completed" | "interrupted") {
  const { turn } = run;
  if (status === "interrupted") {
    for (const item of turn.items) {
      if (/^(streaming|streaming_arguments|started|running|waiting)$/.test(String(item.status))) upsert(turn, { id: item.id, status: "interrupted", terminalReason: "interrupted" }, "item/completed");
    }
  }
  Object.assign(turn, { status, completedAt: now() });
  runs.delete(turn.threadId);
  setThread(turn.threadId, { status, currentTurnId: turn.id });
  notify(turn.threadId, "turn/completed", { turn: { id: turn.id, threadId: turn.threadId, status, startedAt: turn.startedAt, completedAt: turn.completedAt } });
  Reflect.set(shell, "liveCompletedAt", performance.now());
}
/** Run a script against a turn; stopping it at any await ends the turn "interrupted". */
async function drive(run: SimRun, script: (run: SimRun) => Promise<void>) {
  runs.set(run.turn.threadId, run);
  try { await script(run); finish(run, "completed"); } catch (cause) {
    if (!(cause instanceof Stopped)) throw cause;
    finish(run, "interrupted");
  }
}
const quote = (text: string) => (text.length > 18 ? `${text.slice(0, 18)}…` : text);

async function workScript(run: SimRun, input: string) {
  const record = recordOf(run.turn.threadId);
  activity(run, "waiting_model");
  await wait(run, 1100);
  if (input.replace(/\s/g, "").length <= 6) {
    await say(run, "final_answer", `你好！我在${state.deviceName}上，随时可以开始。想让我看哪个项目、改什么？`, "简短的问候，直接回应即可。");
    return;
  }
  await say(run, "commentary", `我先看一下相关代码，再跑一次检查拿到实际数字，确认之后再改。`,
    `用户要「${quote(input)}」。先确认问题在哪：读入口文件、搜一下调用点，再跑检查脚本拿数字。`);
  await tool(run, `read-${run.turn.id}`, "read_file", { path: "src/components/Transcript.tsx" }, async () => { await wait(run, 380); return { content: "export function Transcript({ turns }) { … }  (412 lines)" }; });
  await tool(run, `grep-${run.turn.id}`, "grep", { pattern: "deriveItems\\(", path: "src" }, async () => { await wait(run, 300); return { content: "src/state/useTranscript.ts:88\nsrc/state/useTranscript.ts:131\nsrc/components/Transcript.tsx:57" }; });
  const steered = deliverSteers(run);
  activity(run, "waiting_model");
  await wait(run, 800);
  if (steered.length) await say(run, "commentary", `收到，按你补充的「${quote(steered.join("；"))}」一起处理。`);
  const callId = `check-${run.turn.id}`;
  const command = "npm run check";
  let ran = true;
  if (record?.permissionMode === "approval") {
    ran = await ask(run, callId, command);
    if (ran) {
      upsert(run.turn, { id: `tool:${callId}`, status: "running" }, "item/completed");
      const output = await runCommand(run, command, PERF_LINES);
      upsert(run.turn, { id: `tool:${callId}`, status: "completed", ok: true, ...output }, "item/completed");
    }
  } else {
    await tool(run, callId, "exec", { command }, () => runCommand(run, command, PERF_LINES));
  }
  steered.push(...deliverSteers(run));
  activity(run, "waiting_model");
  await wait(run, 900);
  await tool(run, `patch-${run.turn.id}`, "apply_patch", { path: "src/state/useTranscript.ts" }, async () => { await wait(run, 260); return { content: "Updated 2 files" }; });
  upsert(run.turn, { id: `diff:${run.turn.id}`, type: "file_edit", status: "completed", paths: ["src/state/useTranscript.ts", "src/components/Transcript.tsx"],
    diff: "diff --git a/src/state/useTranscript.ts b/src/state/useTranscript.ts\n--- a/src/state/useTranscript.ts\n+++ b/src/state/useTranscript.ts\n@@ -85,3 +85,4 @@ export function useTranscript(turns: Turn[]) {\n-  const items = turns.flatMap(deriveItems);\n+  const done = useMemo(() => finished.map(deriveTurn), [finished]);\n+  const items = [...done.flat(), ...deriveTurn(live)];\n   return items;\ndiff --git a/src/components/Transcript.tsx b/src/components/Transcript.tsx\n--- a/src/components/Transcript.tsx\n+++ b/src/components/Transcript.tsx\n@@ -57,1 +57,1 @@\n-  const rows = useTranscript(turns, { subscribe: \"all\" });\n+  const rows = useTranscript(turns, { subscribe: \"live\" });\n" }, "item/completed");
  activity(run, "waiting_model");
  await wait(run, 900);
  await say(run, "final_answer", `## 结论

「${quote(input)}」已处理。问题出在**每次流式更新都重新整理整段对话**，轮数越多越慢。

### 改动

1. \`useTranscript.ts\`：已完成的轮次只算一次，结果缓存。
2. \`Transcript.tsx\`：只有正在进行的那一轮订阅增量。

\`\`\`ts
const done = useMemo(() => finished.map(deriveTurn), [finished]);
\`\`\`

| 指标 | 修改前 | 修改后 |
| --- | --- | --- |
| 每帧耗时 | 18 ms | 4 ms |
| 检查 | ${ran ? "通过" : "未运行"} | ${ran ? "通过" : "—"} |
${steered.length ? `\n也按你中途补充的「${quote(steered.join("；"))}」做了调整。\n` : ""}${ran ? "" : "\n> 你拒绝了运行检查，所以改动还没有验证过。\n"}`);
}

function startTurn(threadId: string, input: string) {
  const turn: SimTurn = { id: `turn-${threadId}-${history(threadId).length + 1}`, threadId, status: "running", startedAt: now(), items: [] };
  history(threadId).push(turn);
  const run: SimRun = { turn, stopped: false, steers: [], decide: null, approval: null };
  setThread(threadId, { status: "running", currentTurnId: turn.id, paused: false });
  const done = (async () => {
    await sleep(60);
    notify(threadId, "turn/started", { turn: { id: turn.id, threadId, status: "running", startedAt: turn.startedAt } });
    upsert(turn, { id: `user:${turn.id}`, type: "user_message", status: "completed", text: input, source: "user" }, "item/started");
    await drive(run, (current) => workScript(current, input));
  })();
  Reflect.set(shell, "liveDone", done);
  return turn;
}

/* "修复 Host git 挂起" waits on its push; answering the approval finishes it. */
const longTurn: SimTurn = { id: "turn-1", threadId: THREAD, status: params.has("decision") || params.has("readability") ? "completed" : "running", startedAt: at, items };
function resumeLongTurn(decision: string) {
  const run: SimRun = { turn: longTurn, stopped: false, steers: [], decide: null, approval: null };
  const allowed = decision.startsWith("accept");
  void drive(run, async () => {
    upsert(longTurn, { id: "approval:call-1", status: allowed ? "approved" : "declined" }, "item/completed");
    setThread(THREAD, { status: "running" });
    if (allowed) {
      await tool(run, "call-1", "exec", { command: "git push origin main" }, () => runCommand(run, "git push origin main", ["Enumerating objects: 9, done.", "Writing objects: 100% (5/5), 1.21 KiB, done.", "To github.com:loom/loom.git", "   b8befa23..0ce8e2bf  main -> main"]));
    } else {
      upsert(longTurn, { id: "tool:call-1", type: "tool_call", toolName: "exec", callId: "call-1", status: "completed", ok: false, arguments: { command: "git push origin main" }, error: "用户拒绝了这次执行" }, "item/completed");
    }
    activity(run, "waiting_model");
    await wait(run, 700);
    await say(run, "final_answer", allowed ? "已推到 `main`（`b8befa23..0ce8e2bf`）。挂起的原因是 Host 的 git 子进程继承了 stdin，现在统一用 `DEVNULL`。" : "好的，没有推送。修改已经在本地提交，需要时再告诉我推送。");
  });
}

/* "长对话滚动性能" is mid-turn: once opened, its test run finishes and the answer streams in. */
let runResumed = navigator.webdriver;
function resumeRunTurn() {
  if (runResumed) return;
  runResumed = true;
  const turn = history("thread-run").at(-1);
  if (!turn || turn.status !== "running") return;
  const run: SimRun = { turn, stopped: false, steers: [], decide: null, approval: null };
  void drive(run, async () => {
    await wait(run, 1500);
    await runCommand(run, "npm test -- transcript", ["PASS src/state/useTranscript.test.ts", "PASS src/components/Transcript.test.tsx", "", "Tests: 24 passed, 24 total", "Time: 3.1 s"]);
    upsert(turn, { id: `${turn.id}:run`, status: "completed", ok: true, content: "24 passed" }, "item/completed");
    deliverSteers(run);
    activity(run, "waiting_model");
    await wait(run, 900);
    await say(run, "final_answer", "图片预览改成**滚出视口后保留解码结果**，回到视口不再重新解码。\n\n- 滚动 200 轮对话时，图片解码从 46 次降到 0 次。\n- `Transcript` 相关的 24 个测试全部通过。", "测试已经过了，总结改动和数字。");
  });
}

/* A full record for the plain conversations, so every message kind the phone draws has
   something to look at: the person's message, reasoning, commentary, file reads, a search,
   a command with output, a failed command, a diff, and a Markdown answer. "长对话滚动性能"
   ends in a turn still running; "升级 Electron 44" is all finished. */
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const SHOWCASE_ANSWER = `## 结论

长对话卡顿来自**每次流式更新都重排整段对话**。改成按轮缓存之后，200 轮的对话每帧从 18 ms 降到 4 ms。

### 改动

1. \`useTranscript.ts\`：已完成的轮次只算一次，结果缓存。
2. \`Transcript.tsx\`：只有正在进行的那一轮订阅增量。
3. 新增 \`perf/long-thread.mjs\`，回归时直接跑。

\`\`\`ts
const done = useMemo(() => finished.map(deriveTurn), [finished]);
\`\`\`

| 轮数 | 修改前 | 修改后 |
| --- | --- | --- |
| 50 | 6 ms | 3 ms |
| 200 | 18 ms | 4 ms |

> 还有一处没动：图片预览在滚动时会重新解码，下次再看。

测试全部通过。`;
const ELECTRON_ANSWER = `已升级到 **Electron 44**，打包和启动都验证过了。

- \`package.json\`：electron 36 → 44，electron-builder 同步升级。
- CSP 改为主进程响应头，渲染层不再写 meta。
- 关掉了 \`RunAsNode\` 和 \`EnableNodeCliInspectArguments\` 两个 fuse。

安装包体积从 92 MB 变为 96 MB。`;
function showcaseTurn(threadId: string, turnId: string, minutes: number, ask: string, answer: string, running = false) {
  const base = { threadId, turnId, createdAt: ago(minutes), updatedAt: ago(minutes) };
  const items: Record<string, unknown>[] = [
    { ...base, id: `${turnId}:u`, type: "user_message", status: "completed", text: ask, source: "user" },
    { ...base, id: `${turnId}:s1`, type: "assistant_message", status: "completed", phase: "commentary", stepId: "s1",
      reasoning: "先确认慢在哪：是渲染、布局还是数据整理。读一下对话组件，再用性能脚本拿数字。", text: "我先看对话组件怎么整理每一轮，再跑一次性能脚本拿实际数字。" },
    { ...base, id: `${turnId}:read`, type: "tool_call", status: "completed", ok: true, toolName: "read_file", callId: `${turnId}-read`,
      arguments: { path: "src/components/Transcript.tsx" }, content: "export function Transcript({ turns }) { … }  (412 lines)" },
    { ...base, id: `${turnId}:grep`, type: "tool_call", status: "completed", ok: true, toolName: "grep", callId: `${turnId}-grep`,
      arguments: { pattern: "deriveItems\\(", path: "src" }, content: "src/state/useTranscript.ts:88\nsrc/state/useTranscript.ts:131\nsrc/components/Transcript.tsx:57" },
    { ...base, id: `${turnId}:fail`, type: "tool_call", status: "completed", ok: false, toolName: "exec", callId: `${turnId}-fail`,
      arguments: { command: "npm run perf:long" }, error: "npm ERR! Missing script: \"perf:long\"" },
    { ...base, id: `${turnId}:proc`, type: "process", status: "completed", processId: `${turnId}-p`, argv: ["node", "perf/long-thread.mjs"], cwd: "D:/work/Loom",
      command: "node perf/long-thread.mjs --turns 200", returncode: 0,
      stdout: "warming up (3 runs)…\nturns 50   frame 6.1 ms\nturns 200  frame 18.4 ms\nslowest: deriveItems 14.2 ms\ndone in 6.2s\n" },
    { ...base, id: `${turnId}:s2`, type: "assistant_message", status: "completed", phase: "commentary", stepId: "s2",
      text: "数字确认了：200 轮时每帧 18 ms，其中 14 ms 在 `deriveItems`。开始改。" },
    { ...base, id: `${turnId}:patch`, type: "tool_call", status: "completed", ok: true, toolName: "apply_patch", callId: `${turnId}-patch`,
      arguments: { path: "src/state/useTranscript.ts" }, content: "Updated 2 files" },
    { ...base, id: `${turnId}:diff`, type: "file_edit", status: "completed", paths: ["src/state/useTranscript.ts", "src/components/Transcript.tsx"],
      diff: "diff --git a/src/state/useTranscript.ts b/src/state/useTranscript.ts\n--- a/src/state/useTranscript.ts\n+++ b/src/state/useTranscript.ts\n@@ -85,4 +85,5 @@\n-  const items = turns.flatMap(deriveItems);\n+  const done = useMemo(() => finished.map(deriveTurn), [finished]);\n+  const items = [...done.flat(), ...deriveTurn(live)];\n   return items;\n@@ -131,3 +132,3 @@ export function useLiveTurn(turn: Turn) {\n   const live = turns.at(-1);\n-  return deriveItems(live, approvals);\n+  return deriveTurn(live);\n }\ndiff --git a/src/components/Transcript.tsx b/src/components/Transcript.tsx\n--- a/src/components/Transcript.tsx\n+++ b/src/components/Transcript.tsx\n@@ -57,2 +57,2 @@\n-  const rows = useTranscript(turns, { subscribe: \"all\" });\n+  const rows = useTranscript(turns, { subscribe: \"live\" });\n" },
  ];
  if (running) {
    items.push({ ...base, id: `${turnId}:run`, type: "tool_call", status: "running", toolName: "exec", callId: `${turnId}-run`, arguments: { command: "npm test -- transcript" } });
    return { id: turnId, threadId, status: "running", startedAt: ago(minutes), items };
  }
  const changes = params.get("changes");  // the changed-files card with one file, or a list long enough to fold (?changes=one|many)
  if (changes) Object.assign(items.find((item) => item.type === "file_edit")!, changes === "one" ? ONE_CHANGE : MANY_CHANGES);
  items.push({ ...base, id: `${turnId}:final`, type: "assistant_message", status: "completed", phase: "final_answer", stepId: "s3", text: answer });
  return { id: turnId, threadId, status: "completed", startedAt: ago(minutes), completedAt: ago(minutes - 2), items };
}
// In the runtime's own shape: `---`/`+++` pairs, never a `diff --git` line.
const plainDiff = (path: string, adds: number, dels: number) => `--- a/${path}\n+++ b/${path}\n@@ -10,${dels + 1} +10,${adds + 1} @@\n${Array.from({ length: dels }, (_, line) => `-  const removed${line} = oldValue;`).join("\n")}${dels ? "\n" : ""}${Array.from({ length: adds }, (_, line) => `+  const added${line} = nextValue;`).join("\n")}${adds ? "\n" : ""}   return value;`;
const ONE_CHANGE = {
  paths: ["desktop-react/src/components/settings/AccountDevicesPanel.tsx"],
  diff: `--- /dev/null\n+++ b/desktop-react/src/components/settings/AccountDevicesPanel.tsx\n@@ -0,0 +1,3 @@\n+export function AccountDevicesPanel() {\n+  return <section className="devices" aria-label="已连接的设备，这一行故意写得很长，用来看窄屏下代码怎么折行" />;\n+}`,
};
const MANY_NAMES = ["src/state/useTranscript.ts", "src/components/Transcript.tsx", "src/components/a-very-long-component-file-name-that-will-not-fit-on-a-phone.tsx", "services/gateway/prod/deeply/nested/folder/structure/settings.ts", "src/a.ts", "src/b.ts", "src/c.ts", "src/d.ts"];
const MANY_CHANGES = {
  paths: [...MANY_NAMES, "docs/no-diff-recorded.md"],
  diff: MANY_NAMES.map((path, index) => plainDiff(path, 12 - index, index % 3)).join("\n"),
};
const showcase: Record<string, () => unknown[]> = {
  "thread-run": () => [
    showcaseTurn("thread-run", "turn-8", 40, "长对话滚动很卡，200 轮以上就掉帧，帮我查一下原因并修好。", SHOWCASE_ANSWER),
    showcaseTurn("thread-run", "turn-9", 3, "再把图片预览滚动时重新解码的问题也处理一下。", "", true),
  ],
  "thread-done": () => [
    showcaseTurn("thread-done", "turn-e1", 30, "把 Electron 升到 44，顺便把 CSP 和 fuse 理一下。", ELECTRON_ANSWER),
  ],
};

const answers: Record<string, (args: Record<string, unknown>) => unknown> = {
  "thread/list": (args) => ({ threads: threads.filter((item) => !item.parentThreadId && (args.view === "all" || Boolean(item.archived) === (args.view === "archived")) && (!args.projectId || item.projectId === args.projectId)), counts: { active: threads.filter((item) => !item.archived).length, archived: threads.filter((item) => item.archived).length, all: threads.length } }),
  "project/list": () => { if (shell.failProjects) throw new Error("项目读取暂时失败"); return { projects, unfiledThreadCount: 0 }; },
  "project/rename": (args) => { const project = projects.find((item) => item.id === args.projectId)!; project.name = String(args.name); return { project }; },
  "project/set_instructions": (args) => { const project = projects.find((item) => item.id === args.projectId)!; project.instructions = String(args.instructions); return { project }; },
  "project/create": (args) => { const project = { id: `p${projects.length + 1}`, name: String(args.name || args.root), root: String(args.root), instructions: "", threadCount: 0 }; projects.push(project); return { project }; },
  "projectRun/overview": (args) => args.threadId === "thread-build" ? buildOverview : { run: null, program: null, children: [], agents: [], events: [], workspace },
  "projectAgent/peek": (args) => {
    const running = Boolean(buildOverview.conversationAgents?.find((item) => item.id === args.agentId)?.executionRunning);
    return { id: args.agentId, role: running ? "goal-check" : "goal-line", state: running ? "running" : "completed", executionRunning: running, finished: !running,
      outcome: running ? "" : "done", startedAt: new Date(Date.now() - 600000).toISOString(), endedAt: running ? null : new Date(Date.now() - 60000).toISOString(), tokens: 1200000, error: "",
      rows: [{ kind: "say", text: "先读报告的方法与结果两节" }, { kind: "tool", text: "read_workspace_text report/report.md", running: false, ok: true },
        { kind: "say", text: "核对表 2 与 results/eval.json 的数字" }, { kind: "tool", text: "python scripts/check_numbers.py", running, ok: running ? null : true }],
      result: running ? "" : "检查结果已生成：表 2 的 3 个数字与 JSON 一致。", observedAt: Date.now() / 1000 };
  },
  "projectAgent/history": () => ({ total: 2, observedAt: Date.now() / 1000, agents: [
    { id: "line-1", parentId: "thread-build", role: "goal-line", task: "正式实验", state: "completed", outcome: "done", finished: true, startedAt: new Date(Date.now() - 7200000).toISOString(), endedAt: new Date(Date.now() - 3600000).toISOString(), tokens: 3600000, result: "跑完 4 个信道条件，吞吐增益 +6.1%", error: "" },
    { id: "old-1", parentId: "thread-build", role: "investigator", task: "找窗口声明", state: "completed", outcome: "done", finished: true, startedAt: new Date(Date.now() - 90000000).toISOString(), endedAt: new Date(Date.now() - 89900000).toISOString(), tokens: 120000, result: "只在 loom_model_bridge.py 里声明。", error: "" }] }),
  "projectWeave/start": (args) => { buildOverview.run = { id: "run-1", revision: 1, sourceThreadId: "thread-build", objective: "重做账号页并独立验收", repository: "D:/work/Loom", state: "running", control: "active", phase: "planning", workflowControl: "active", completionPolicy: "local_integrate", tokensUsed: 0, tokenBudget: Number(args.tokenBudget), episodesUsed: 0, maxEpisodes: Number(args.maxEpisodes), maxWorkers: Number(args.maxWorkers), plan: [], permissionMode: String(args.permissionMode) }; buildOverview.proposal = null; return { program: {} }; },
  "projectWeave/dismiss": () => { buildOverview.proposal = null; return {}; },
  "projectWeave/goal": (args) => {
    const goal = buildOverview.goal!;
    if (args.action === "resume") throw new Error("Legacy resume must not be used");
    if (args.tokenBudget !== undefined) {
      if (Number(args.tokenBudget) <= goal.tokens) throw new Error("Budget exhausted");
      goal.tokenBudget = Number(args.tokenBudget);
    }
    if (args.action === "resume_with_budget") goal.status = "active";
    if (args.action === "pause") goal.status = "paused";
    return { goal };
  },
  "projectRun/pause": () => { if (buildOverview.run) { buildOverview.run.workflowControl = "paused"; buildOverview.run.control = "paused"; buildOverview.run.revision++; } return { run: buildOverview.run }; },
  "projectRun/resume": (args) => { if (buildOverview.run) { buildOverview.run.workflowControl = "active"; buildOverview.run.control = "active"; if (args.tokenBudget) buildOverview.run.tokenBudget = Number(args.tokenBudget); if (args.maxEpisodes) buildOverview.run.maxEpisodes = Number(args.maxEpisodes); buildOverview.run.revision++; } return { run: buildOverview.run }; },
  "projectRun/approval": () => { buildOverview.agents.forEach((agent) => { agent.approval = null; }); return {}; },
  "thread/start": (args) => { const record: ThreadRecord = { ...threads[3], id: `new-${threads.length}`, title: "新对话", projectId: String(args.projectId || ""), workspace: String(args.workspace || workspace.path), status: "idle", weave: false }; threads.push(record); return { thread: record }; },
  "turn/start": (args) => {
    const threadId = String(args.threadId);
    if (runs.has(threadId)) throw new Error("这个对话正在运行");
    const turn = startTurn(threadId, String(args.input ?? ""));
    return { turn: { id: turn.id, threadId, status: "running", items: [] } };
  },
  "turn/interrupt": (args) => {
    const threadId = String(args.threadId);
    const run = runs.get(threadId);
    if (run) { run.stopped = true; run.decide?.("stopped"); }
    else {
      const turn = history(threadId).find((entry) => entry.id === args.turnId && /^(running|waiting)/.test(entry.status));
      if (!turn) return { requested: false, threadId, turnId: args.turnId ?? null };
      finish({ turn, stopped: true, steers: [], decide: null, approval: null }, "interrupted");
    }
    return { requested: true, threadId, turnId: args.turnId, status: "interrupted" };
  },
  "turn/steer": (args) => {
    const threadId = String(args.threadId);
    const run = runs.get(threadId);
    if (!run || run.turn.id !== args.turnId) throw new Error("turnId does not match the thread's current turn");
    const steer = { text: String(args.input ?? "").trim(), inputId: String(args.clientInputId || `steer-${Date.now()}`), submittedAt: now() };
    run.steers.push(steer);
    return { threadId, turnId: run.turn.id, accepted: true, inputId: steer.inputId, duplicate: false, delivery: "next_safe_boundary", submittedAt: steer.submittedAt, applied: false, displayText: steer.text, attachments: [] };
  },
  "thread/rename": (args) => { const record = threads.find((item) => item.id === args.threadId)!; record.title = String(args.title); return { thread: record }; },
  "thread/archive": (args) => { const record = threads.find((item) => item.id === args.threadId)!; record.archived = Boolean(args.archived); return { thread: record }; },
  "thread/read": (args) => {
    const record = threads.find((entry) => entry.id === args.threadId) ?? threads[0];
    if (record.id === "thread-run") resumeRunTurn();
    if (record.id !== THREAD || histories.has(THREAD)) {
      const approval = runs.get(record.id)?.approval ?? (record.id === THREAD && longTurn.status === "running" && !runs.has(THREAD) ? pendingApproval : null);
      return { thread: record, turns: history(record.id), pendingApproval: approval, hasMoreTurns: false };
    }
    if (params.has("decision")) {  // a finished turn ending in a choice card (?decision=1)
      const card = JSON.stringify({ title: "后续研究路线", options: [{ id: "a", title: "深挖神经接收机", description: "单支路，证据连续" },
        { id: "b", title: "两条线并行", description: "共享 harness" }] });
      const ask = { id: "decision-final", threadId: THREAD, turnId: "turn-1", type: "assistant_message", phase: "final_answer",
                    status: "completed", text: `选好后我立即开始。\n\n\`\`\`loom-decision\n${card}\n\`\`\`` };
      return { thread: { ...record, status: "completed" }, hasMoreTurns: false, pendingApproval: null,
               turns: [{ id: "turn-1", threadId: THREAD, status: "completed", startedAt: at, completedAt: at, items: [items[0], ask] }] };
    }
    if (params.has("readability")) {
      const readableItems = [items[0],
        { id: "note-1", threadId: THREAD, turnId: "turn-1", type: "assistant_message", phase: "commentary", status: "completed", text: "五个冒烟测试全部通过，8 个源码模块的校验义务清零，报告里的自相矛盾也修掉了。现在把进度收尾。" },
        { id: "tool-read", threadId: THREAD, turnId: "turn-1", type: "tool_call", toolName: "exec", callId: "read-1", status: "completed", arguments: { command: "npm test" }, result: "All checks passed" },
        { id: "note-2", threadId: THREAD, turnId: "turn-1", type: "assistant_message", phase: "commentary", status: "completed", text: "这是一段需要展开阅读的过程说明。窄屏上不应该为过程层级反复缩进，消息应当保持同一条清晰的阅读边缘。" },
        { id: "readable-final", threadId: THREAD, turnId: "turn-1", type: "assistant_message", phase: "final_answer", status: "completed", text: ANSWER },
      ];
      return { thread: { ...record, status: "completed", weave: true }, hasMoreTurns: false, pendingApproval: null, turns: [{ id: "turn-1", threadId: THREAD, status: "completed", startedAt: at, completedAt: at, items: readableItems }] };
    }
    return { thread: record, pendingApproval, hasMoreTurns: false, turns: history(THREAD) };
  },
  "thread/set_permission_mode": (args) => {
    const record = threads.find((entry) => entry.id === args.threadId)!;
    record.permissionMode = String(args.permissionMode);
    return { thread: record };
  },
  "approval/respond": (args) => {
    const threadId = String(args.threadId ?? "");
    const decision = String(args.decision ?? "decline");
    const run = runs.get(threadId);
    if (run?.decide) { const decide = run.decide; window.setTimeout(() => decide(decision), 120); return {}; }
    if (threadId === THREAD && longTurn.status === "running") { window.setTimeout(() => resumeLongTurn(decision), 120); return {}; }
    throw new Error("This approval is no longer pending.");
  },
  "tools/catalog": () => ({ tools: [] }),
  "thread/handoff_status": () => ({ incoming: null, outgoing: [] }),
};

const state = {
  paired: !params.has("unpaired"),
  deviceId: "desk-1",
  deviceName: "书房台式机",
  socket: params.has("offline") ? "closed" : "open",
  online: !params.has("offline"),
  devices: [{ id: "desk-1", name: "书房台式机" }, { id: "laptop", name: "轻薄本" }],
};
const emit = (event: unknown) => window.__loomShellEvent?.(JSON.stringify(event));
const shell = {
  agentState(id: string, state: string, executionRunning: boolean) { const agent = buildOverview.conversationAgents?.find(item => item.id === id); if (agent) Object.assign(agent, { state, executionRunning }); },
  calls: [] as Array<{ operation: string; args: unknown[] }>,
  biometric: true,
  failProjects: false,
  loseNextControlReply: false,
  biometricReasons: [] as string[],
  state(patch: Partial<typeof state>) { Object.assign(state, patch); emit({ type: "state", state }); },
  pairing(progress: unknown) { emit({ type: "pairing", progress }); },
};
Reflect.set(window, "__shell", shell);
const controlReceipts = new Map<string, unknown>();
window.LoomShell = {
  state: () => JSON.stringify(state),
  invoke(id, operation, argsJson) {
    const args = JSON.parse(argsJson) as unknown[];
    shell.calls.push({ operation, args });
    window.setTimeout(() => {
      try {
      let result: unknown = {};
      if (operation === "connect") result = { protocolVersion: 1, serverInfo: { name: "fixture", version: "0" }, capabilities: {}, runtime: { model: "MiniMax-M3", defaultWorkspace: "D:/work/Loom", defaultPermissionMode: "approval", permissionModes: ["read-only", "approval", "workspace", "full-access"] } };
      else if (operation === "listModels") result = models;
      else if (operation === "queryModelUsage") result = { status: "ok", provider: "模拟供应商", checkedAt: Date.now(), windows: [{ label: "Weekly", remainingPercent: 68 }], balances: [], scope: "api-key" };
      else if (operation === "setReasoning") {
        const value = String(args.at(-1));
        const record = threads.find((entry) => entry.id === args[0]) ?? threads[0];
        Object.assign(record, { reasoning: { kind: String(args.at(-2)), value } });
        profile.reasoning.value = value;
        result = { thread: record, models };
      }
      else if (operation === "call") {
        const method = String(args[0]), callParams = (args[1] ?? {}) as Record<string, unknown>;
        const operationId = String(callParams.operationId || "");
        const control = /^projectRun\/(pause|resume|cancel|recover)$/.test(method);
        result = control && controlReceipts.has(operationId) ? controlReceipts.get(operationId) : answers[method]?.(callParams) ?? {};
        if (control) {
          controlReceipts.set(operationId, result);
          if (shell.loseNextControlReply) { shell.loseNextControlReply = false; emit({ type: "invoke_result", id, error: { code: "HOST_OFFLINE", message: "操作已执行，但回复丢失" } }); return; }
        }
      }
      emit({ type: "invoke_result", id, result });
      } catch (cause) { emit({ type: "invoke_result", id, error: { code: "RPC_ERROR", message: cause instanceof Error ? cause.message : String(cause) } }); }
    }, operation === "call" ? LATENCY : 10);
  },
  startPairing: () => shell.pairing({ state: "scanning" }),
  cancelPairing: () => shell.pairing({ state: "cancelled" }),
  selectDevice: (deviceId) => shell.state({ deviceId }),
  unpair: () => shell.state({ paired: false }),
  confirmBiometric(id, reason) { shell.biometricReasons.push(reason); window.setTimeout(() => emit({ type: "biometric", id, ok: shell.biometric }), 0); },
  openUrl: () => {},
  openAttachment: (id) => emit({ type: "invoke_result", id, result: true }),
  setBusyThreads: (idsJson) => { Reflect.set(shell, "busy", JSON.parse(idsJson)); },
  fetchDevices(id) {
    const hours = (count: number) => Math.floor(Date.now() / 1000) - count * 3600;
    emit({ type: "devices", id, devices: {
      computers: [{ id: "tablet", name: "旧工作站", platform: "win32", version: "0.1.150", last_seen_at: hours(72), online: false }],
      phones: [
        { id: "s1", name: "HUAWEI Mate 50", created_at: hours(30), last_used_at: hours(0), online: true, current: true },
        { id: "s2", name: "备用手机", created_at: hours(400), last_used_at: hours(50), online: false, current: false },
      ],
      history: [{ id: "s0", name: "HUAWEI P40", created_at: hours(2000), removed_at: hours(300) }],
    } });
  },
  connectionLog: () => JSON.stringify([
    { at: Date.now() - 600_000, event: "connected", detail: "" },
    { at: Date.now() - 300_000, event: "closed", detail: "1012 service restart" },
    { at: Date.now() - 296_000, event: "connected", detail: "" },
    { at: Date.now() - 60_000, event: "failed", detail: "SocketTimeoutException" },
    { at: Date.now() - 50_000, event: "connected", detail: "" },
  ]),
};

await import("../../src/mobile/main");
export {};
