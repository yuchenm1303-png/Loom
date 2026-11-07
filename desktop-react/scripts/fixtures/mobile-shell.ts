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

const threads = [
  { id: THREAD, title: "修复 Host git 挂起", status: "waiting_approval", workspace: "D:/work/Loom", permissionMode: "approval", createdAt: at, updatedAt: at, currentTurnId: "turn-1", active: true, weave: false },
  { id: "thread-run", title: "长对话滚动性能", status: "running", workspace: "D:/work/Loom", permissionMode: "workspace", createdAt: at, updatedAt: at, currentTurnId: "turn-9", active: true, weave: false },
  { id: "thread-build", title: "Loom 账号页重做", status: "running", workspace: "D:/work/Loom", permissionMode: "workspace", createdAt: at, updatedAt: at, currentTurnId: "turn-8", active: true, weave: true },
  { id: "thread-done", title: "升级 Electron 44", status: "completed", workspace: "D:/work/Loom", permissionMode: "approval", createdAt: at, updatedAt: at, active: true, weave: false },
];
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
  "thread/list": () => ({ threads, counts: { active: threads.length, archived: 0, all: threads.length } }),
  "project/list": () => ({ projects: [{ id: "p1", name: "Loom", root: "D:/work/Loom", threadCount: 4 }], unfiledThreadCount: 0 }),
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
  biometricReasons: [] as string[],
  state(patch: Partial<typeof state>) { Object.assign(state, patch); emit({ type: "state", state }); },
  pairing(progress: unknown) { emit({ type: "pairing", progress }); },
};
Reflect.set(window, "__shell", shell);
window.LoomShell = {
  state: () => JSON.stringify(state),
  invoke(id, operation, argsJson) {
    const args = JSON.parse(argsJson) as unknown[];
    shell.calls.push({ operation, args });
    window.setTimeout(() => {
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
      else if (operation === "call") result = answers[String(args[0])]?.((args[1] ?? {}) as Record<string, unknown>) ?? {};
      emit({ type: "invoke_result", id, result });
    }, 10);
  },
  startPairing: () => shell.pairing({ state: "scanning" }),
  cancelPairing: () => shell.pairing({ state: "cancelled" }),
  selectDevice: (deviceId) => shell.state({ deviceId }),
  unpair: () => shell.state({ paired: false }),
  confirmBiometric(id, reason) { shell.biometricReasons.push(reason); window.setTimeout(() => emit({ type: "biometric", id, ok: shell.biometric }), 0); },
  openUrl: () => {},
  setBusyThreads: (idsJson) => { Reflect.set(shell, "busy", JSON.parse(idsJson)); },
};

await import("../../src/mobile/main");
export {};
