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

const answers: Record<string, (args: Record<string, unknown>) => unknown> = {
  "thread/list": (args) => ({ threads: threads.filter((item) => !item.parentThreadId && (args.view === "all" || Boolean(item.archived) === (args.view === "archived")) && (!args.projectId || item.projectId === args.projectId)), counts: { active: threads.filter((item) => !item.archived).length, archived: threads.filter((item) => item.archived).length, all: threads.length } }),
  "project/list": () => { if (shell.failProjects) throw new Error("项目读取暂时失败"); return { projects, unfiledThreadCount: 0 }; },
  "project/rename": (args) => { const project = projects.find((item) => item.id === args.projectId)!; project.name = String(args.name); return { project }; },
  "project/set_instructions": (args) => { const project = projects.find((item) => item.id === args.projectId)!; project.instructions = String(args.instructions); return { project }; },
  "project/create": (args) => { const project = { id: `p${projects.length + 1}`, name: String(args.name || args.root), root: String(args.root), instructions: "", threadCount: 0 }; projects.push(project); return { project }; },
  "projectRun/overview": (args) => args.threadId === "thread-build" ? buildOverview : { run: null, program: null, children: [], agents: [], events: [], workspace },
  "projectWeave/start": (args) => { buildOverview.run = { id: "run-1", revision: 1, sourceThreadId: "thread-build", objective: "重做账号页并独立验收", repository: "D:/work/Loom", state: "running", control: "active", phase: "planning", workflowControl: "active", completionPolicy: "local_integrate", tokensUsed: 0, tokenBudget: Number(args.tokenBudget), episodesUsed: 0, maxEpisodes: Number(args.maxEpisodes), maxWorkers: Number(args.maxWorkers), plan: [], permissionMode: String(args.permissionMode) }; buildOverview.proposal = null; return { program: {} }; },
  "projectWeave/dismiss": () => { buildOverview.proposal = null; return {}; },
  "projectRun/pause": () => { if (buildOverview.run) { buildOverview.run.workflowControl = "paused"; buildOverview.run.control = "paused"; buildOverview.run.revision++; } return { run: buildOverview.run }; },
  "projectRun/resume": (args) => { if (buildOverview.run) { buildOverview.run.workflowControl = "active"; buildOverview.run.control = "active"; if (args.tokenBudget) buildOverview.run.tokenBudget = Number(args.tokenBudget); if (args.maxEpisodes) buildOverview.run.maxEpisodes = Number(args.maxEpisodes); buildOverview.run.revision++; } return { run: buildOverview.run }; },
  "projectRun/approval": () => { buildOverview.agents.forEach((agent) => { agent.approval = null; }); return {}; },
  "thread/start": (args) => { const record: ThreadRecord = { ...threads[3], id: `new-${threads.length}`, title: "新对话", projectId: String(args.projectId || ""), workspace: String(args.workspace || workspace.path), status: "idle", weave: false }; threads.push(record); return { thread: record }; },
  "turn/start": () => ({ turn: { id: `turn-${Date.now()}`, status: "running", items: [] } }),
  "turn/steer": () => ({}),
  "thread/rename": (args) => { const record = threads.find((item) => item.id === args.threadId)!; record.title = String(args.title); return { thread: record }; },
  "thread/archive": (args) => { const record = threads.find((item) => item.id === args.threadId)!; record.archived = Boolean(args.archived); return { thread: record }; },
  "thread/read": (args) => {
    const record = threads.find((entry) => entry.id === args.threadId) ?? threads[0];
    if (record.id !== THREAD) return { thread: record, turns: [], hasMoreTurns: false };
    return { thread: record, pendingApproval, hasMoreTurns: false, turns: [{ id: "turn-1", threadId: THREAD, status: "running", startedAt: at, items }] };
  },
  "thread/set_permission_mode": (args) => {
    const record = threads.find((entry) => entry.id === args.threadId)!;
    record.permissionMode = String(args.permissionMode);
    return { thread: record };
  },
  "approval/respond": () => ({}),
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
