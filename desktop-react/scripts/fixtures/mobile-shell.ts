/**
 * The phone page over a scripted native shell, so it can be checked in a browser.
 *
 *   npx vite --port 5199
 *   open http://127.0.0.1:5199/scripts/fixtures/mobile.html
 *
 *   ?unpaired=1    the pairing screen; window.__shell.pairing(progress) drives it
 *   ?offline=1     the computer is offline
 *
 * window.__shell.calls records every invoke, window.__shell.state(patch) changes the
 * connection, window.__shell.biometric sets the fingerprint answer.
 */
import type { ProjectRecord, ThreadRecord } from "../../src/types/loom";
import type { ProjectOverview } from "../../src/components/projectOverview";
const params = new URLSearchParams(window.location.search);
const THREAD = "thread-long";
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

/* ?live=1 (or ?live=approval): a conversation whose next turn is played as the Host plays one,
   event by event, with the same item shapes app_server.py emits: a thinking gap, streamed reasoning
   and commentary, tool calls with streamed arguments, a process with live output, a diff, an
   optional approval, then a streamed Markdown answer. Send any message in "首页加载变慢" to start it;
   window.__shell.liveDone resolves when the turn has completed. */
const LIVE = "thread-live";
const live = params.get("live");
const liveItems: Record<string, unknown>[] = [];
let liveTurns: Array<{ id: string; threadId: string; status: string; startedAt: string; completedAt?: string; items: Record<string, unknown>[] }> = [];
let approvalGate: (() => void) | null = null;
const liveThread: ThreadRecord = { id: LIVE, title: "首页加载变慢", status: "completed", workspace: "D:/work/Loom", permissionMode: "approval", createdAt: at, updatedAt: new Date().toISOString(), active: true, weave: false, projectId: "p1" };
if (live) threads.push(liveThread);
const LIVE_REASONING = "用户说首页变慢了。先确认是哪一段慢：接口、打包体积还是渲染。先读入口文件，再跑一次性能脚本拿数字。";
const LIVE_NOTE = "我先看一下首页的入口和数据请求，再跑一次性能脚本，拿到实际数字再改。";
const LIVE_ANSWER = `## 结论

首页慢在**首屏同时发了 6 个请求**，其中 \`/api/feed\` 平均 1.8 秒，并且阻塞了渲染。

### 改动

1. \`HomePage.tsx\`：feed 改为首屏之后再取，先渲染骨架。
2. \`api/client.ts\`：给 \`/api/profile\` 加了 60 秒缓存。

\`\`\`ts
const feed = useDeferredQuery("/api/feed", { after: "first-paint" });
\`\`\`

| 指标 | 修改前 | 修改后 |
| --- | --- | --- |
| 首屏时间 | 2.9 s | 1.1 s |
| 请求数 | 6 | 3 |

性能脚本和单元测试都已通过。`;
const PERF_LINES = ["> loom-web@1.4.0 perf", "> node scripts/perf.mjs --route /", "", "warming up (3 runs)…", "run 1  first paint 2.91s  requests 6", "run 2  first paint 2.87s  requests 6", "run 3  first paint 2.94s  requests 6", "slowest: GET /api/feed 1.82s (blocking)", "slowest: GET /api/profile 0.64s", "done in 9.4s"];
const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
let seed = 7;
const chunk = () => { seed = (seed * 9301 + 49297) % 233280; return 1 + Math.floor((seed / 233280) * 7); };
function liveNotify(method: string, params: Record<string, unknown>) {
  emit({ type: "notification", payload: { method, params: { threadId: LIVE, ...params } } });
}
function liveUpsert(item: Record<string, unknown>, method: "item/started" | "item/completed") {
  const full = { threadId: LIVE, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), ...item };
  const index = liveItems.findIndex((entry) => entry.id === full.id);
  if (index >= 0) liveItems[index] = { ...liveItems[index], ...full }; else liveItems.push(full);
  liveNotify(method, { item: full });
}
async function liveStream(itemId: string, turnId: string, field: "text" | "reasoning", text: string, pace = 34) {
  for (let at = 0; at < text.length;) {
    const piece = text.slice(at, at += chunk());
    const index = liveItems.findIndex((entry) => entry.id === itemId);
    liveItems[index] = { ...liveItems[index], [field]: `${String(liveItems[index][field] ?? "")}${piece}` };
    liveNotify("item/delta", { turnId, itemId, delta: { [field]: piece } });
    await sleep(pace + (seed % 3) * 14);
  }
}
async function liveTool(turnId: string, callId: string, toolName: string, args: Record<string, unknown>, run: () => Promise<Record<string, unknown>>) {
  const id = `tool:${callId}`;
  liveUpsert({ id, turnId, type: "tool_call", status: "streaming_arguments", callId, toolName }, "item/started");
  const json = JSON.stringify(args);
  for (let at = 0; at < json.length;) {
    const piece = json.slice(at, at += 6);
    liveNotify("item/delta", { turnId, itemId: id, delta: { kind: "tool_call_argument", callId, toolName, arguments: piece } });
    await sleep(28);
  }
  liveUpsert({ id, turnId, type: "tool_call", status: "started", callId, toolName, arguments: args }, "item/completed");
  await sleep(90);
  liveUpsert({ id, turnId, type: "tool_call", status: "running", callId, toolName, arguments: args }, "item/completed");
  liveNotify("turn/modelActivity", { turnId, phase: "running", active: false, contentGapSeconds: 0 });
  const result = await run();
  liveUpsert({ id, turnId, type: "tool_call", status: "completed", callId, toolName, arguments: args, ok: true, ...result }, "item/completed");
}
async function playLiveTurn(text: string) {
  const turnId = `turn-live-${liveTurns.length + 1}`;
  const startedAt = new Date().toISOString();
  liveTurns.push({ id: turnId, threadId: LIVE, status: "running", startedAt, items: liveItems });
  Object.assign(liveThread, { status: "running", currentTurnId: turnId });
  await sleep(60);
  liveNotify("turn/started", { turn: { id: turnId, threadId: LIVE, status: "running", startedAt } });
  liveUpsert({ id: `user:${turnId}`, turnId, type: "user_message", status: "completed", text, source: "user" }, "item/started");
  liveNotify("turn/modelActivity", { turnId, phase: "waiting_model", active: false, contentGapSeconds: 0 });
  await sleep(1300);
  // Step 1: reasoning, then a sentence of commentary, then tools.
  const step1 = `assistant:${turnId}:s1`;
  liveUpsert({ id: step1, turnId, type: "assistant_message", status: "streaming" }, "item/started");
  await liveStream(step1, turnId, "reasoning", LIVE_REASONING, 30);
  await liveStream(step1, turnId, "text", LIVE_NOTE);
  liveUpsert({ id: step1, turnId, type: "assistant_message", status: "completed", text: LIVE_NOTE, reasoning: LIVE_REASONING, phase: "commentary", stepId: "s1" }, "item/completed");
  await liveTool(turnId, "read-1", "read_file", { path: "src/pages/HomePage.tsx" }, async () => { await sleep(380); return { content: "export function HomePage() { … }  (212 lines)" }; });
  await liveTool(turnId, "grep-1", "grep", { pattern: "useQuery\\(", path: "src" }, async () => { await sleep(300); return { content: "src/pages/HomePage.tsx:41\nsrc/pages/HomePage.tsx:58\nsrc/api/client.ts:12" }; });
  liveNotify("turn/modelActivity", { turnId, phase: "waiting_model", active: false, contentGapSeconds: 0 });
  await sleep(900);
  const runPerf = async () => {
    const pid = `process:p-${turnId}`;
    liveUpsert({ id: pid, turnId, type: "process", status: "running", processId: "p1", argv: ["npm", "run", "perf"], cwd: "D:/work/Loom", command: "npm run perf", stdout: "" }, "item/started");
    let out = "";
    for (const line of PERF_LINES) { out += `${line}\n`; liveUpsert({ id: pid, turnId, type: "process", status: "running", command: "npm run perf", stdout: out }, "item/completed"); await sleep(260); }
    liveUpsert({ id: pid, turnId, type: "process", status: "completed", command: "npm run perf", stdout: out, returncode: 0 }, "item/completed");
    return { content: out };
  };
  if (live === "approval") {
    const callId = "perf-1";
    liveUpsert({ id: `tool:${callId}`, turnId, type: "tool_call", status: "started", callId, toolName: "exec", arguments: { command: "npm run perf" } }, "item/started");
    liveUpsert({ id: `approval:${callId}`, turnId, type: "approval", status: "waiting", callId, toolName: "exec", arguments: { command: "npm run perf" }, effect: "sensitive", reason: "npm run perf", availableDecisions: ["accept", "acceptForSession", "decline"], sessionScope: { kind: "prefix", prefix: ["npm", "run"] } }, "item/started");
    Object.assign(liveThread, { status: "waiting_approval" });
    liveNotify("approval/requested", { approval: { ...pendingApproval, threadId: LIVE, turnId, itemId: `tool:${callId}`, approvalItemId: `approval:${callId}`, callId, requestId: "req-live", arguments: { command: "npm run perf" }, reason: "npm run perf", availableDecisions: ["accept", "acceptForSession", "decline"] } });
    await new Promise<void>((resolve) => { approvalGate = resolve; });
    Object.assign(liveThread, { status: "running" });
    liveUpsert({ id: `approval:${callId}`, turnId, type: "approval", status: "approved", callId, toolName: "exec" }, "item/completed");
    liveUpsert({ id: `tool:${callId}`, turnId, type: "tool_call", status: "running", callId, toolName: "exec", arguments: { command: "npm run perf" } }, "item/completed");
    const result = await runPerf();
    liveUpsert({ id: `tool:${callId}`, turnId, type: "tool_call", status: "completed", callId, toolName: "exec", arguments: { command: "npm run perf" }, ok: true, ...result }, "item/completed");
  } else {
    await liveTool(turnId, "perf-1", "exec", { command: "npm run perf" }, runPerf);
  }
  liveNotify("turn/modelActivity", { turnId, phase: "waiting_model", active: false, contentGapSeconds: 0 });
  await sleep(1100);
  await liveTool(turnId, "patch-1", "apply_patch", { path: "src/pages/HomePage.tsx" }, async () => { await sleep(260); return { content: "Updated 2 files" }; });
  liveUpsert({ id: `diff:${turnId}`, turnId, type: "file_edit", status: "completed", paths: ["src/pages/HomePage.tsx", "src/api/client.ts"], diff: "diff --git a/src/pages/HomePage.tsx b/src/pages/HomePage.tsx\n--- a/src/pages/HomePage.tsx\n+++ b/src/pages/HomePage.tsx\n@@ -41,3 +41,3 @@\n-  const feed = useQuery(\"/api/feed\");\n+  const feed = useDeferredQuery(\"/api/feed\", { after: \"first-paint\" });\n" }, "item/completed");
  liveNotify("turn/modelActivity", { turnId, phase: "waiting_model", active: false, contentGapSeconds: 0 });
  await sleep(1000);
  // Step 2: the answer.
  const step2 = `assistant:${turnId}:s2`;
  liveUpsert({ id: step2, turnId, type: "assistant_message", status: "streaming" }, "item/started");
  await liveStream(step2, turnId, "text", LIVE_ANSWER, 26);
  liveUpsert({ id: step2, turnId, type: "assistant_message", status: "completed", text: LIVE_ANSWER, phase: "final_answer", stepId: "s2" }, "item/completed");
  await sleep(120);
  const completedAt = new Date().toISOString();
  liveTurns = liveTurns.map((turn) => turn.id === turnId ? { ...turn, status: "completed", completedAt } : turn);
  Object.assign(liveThread, { status: "completed", currentTurnId: turnId });
  liveNotify("turn/completed", { turn: { id: turnId, threadId: LIVE, status: "completed", startedAt, completedAt } });
  Reflect.set(shell, "liveCompletedAt", performance.now());
}

const answers: Record<string, (args: Record<string, unknown>) => unknown> = {
  "thread/list": (args) => ({ threads: threads.filter((item) => !item.parentThreadId && (args.view === "all" || Boolean(item.archived) === (args.view === "archived")) && (!args.projectId || item.projectId === args.projectId)), counts: { active: threads.filter((item) => !item.archived).length, archived: threads.filter((item) => item.archived).length, all: threads.length } }),
  "project/list": () => { if (shell.failProjects) throw new Error("项目读取暂时失败"); return { projects, unfiledThreadCount: 0 }; },
  "project/rename": (args) => { const project = projects.find((item) => item.id === args.projectId)!; project.name = String(args.name); return { project }; },
  "project/set_instructions": (args) => { const project = projects.find((item) => item.id === args.projectId)!; project.instructions = String(args.instructions); return { project }; },
  "project/create": (args) => { const project = { id: `p${projects.length + 1}`, name: String(args.name || args.root), root: String(args.root), instructions: "", threadCount: 0 }; projects.push(project); return { project }; },
  "projectRun/overview": (args) => args.threadId === "thread-build" ? buildOverview : { run: null, program: null, children: [], agents: [], events: [], workspace },
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
    if (args.threadId === LIVE) { const id = `turn-live-${liveTurns.length + 1}`; Reflect.set(shell, "liveDone", playLiveTurn(String(args.input ?? ""))); return { turn: { id, threadId: LIVE, status: "running", items: [] } }; }
    return { turn: { id: `turn-${Date.now()}`, status: "running", items: [] } };
  },
  "turn/steer": () => ({}),
  "thread/rename": (args) => { const record = threads.find((item) => item.id === args.threadId)!; record.title = String(args.title); return { thread: record }; },
  "thread/archive": (args) => { const record = threads.find((item) => item.id === args.threadId)!; record.archived = Boolean(args.archived); return { thread: record }; },
  "thread/read": (args) => {
    const record = threads.find((entry) => entry.id === args.threadId) ?? threads[0];
    if (record.id !== THREAD) return { thread: record, turns: [], hasMoreTurns: false };
    if (params.has("readability")) {
      const readableItems = [items[0],
        { id: "note-1", threadId: THREAD, turnId: "turn-1", type: "assistant_message", phase: "commentary", status: "completed", text: "五个冒烟测试全部通过，8 个源码模块的校验义务清零，报告里的自相矛盾也修掉了。现在把进度收尾。" },
        { id: "tool-read", threadId: THREAD, turnId: "turn-1", type: "tool_call", toolName: "exec", callId: "read-1", status: "completed", arguments: { command: "npm test" }, result: "All checks passed" },
        { id: "note-2", threadId: THREAD, turnId: "turn-1", type: "assistant_message", phase: "commentary", status: "completed", text: "这是一段需要展开阅读的过程说明。窄屏上不应该为过程层级反复缩进，消息应当保持同一条清晰的阅读边缘。" },
        { id: "readable-final", threadId: THREAD, turnId: "turn-1", type: "assistant_message", phase: "final_answer", status: "completed", text: ANSWER },
      ];
      return { thread: { ...record, status: "completed", weave: true }, hasMoreTurns: false, pendingApproval: null, turns: [{ id: "turn-1", threadId: THREAD, status: "completed", startedAt: at, completedAt: at, items: readableItems }] };
    }
    return { thread: record, pendingApproval, hasMoreTurns: false, turns: [{ id: "turn-1", threadId: THREAD, status: "running", startedAt: at, items }] };
  },
  "thread/set_permission_mode": (args) => {
    const record = threads.find((entry) => entry.id === args.threadId)!;
    record.permissionMode = String(args.permissionMode);
    return { thread: record };
  },
  "approval/respond": () => { const gate = approvalGate; approvalGate = null; window.setTimeout(() => gate?.(), 120); return {}; },
  "tools/catalog": () => ({ tools: [] }),
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
    }, 10);
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
