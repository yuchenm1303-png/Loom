// Real App, production stylesheet order and an isolated in-memory host that
// serves a deterministic long agent conversation. Item shapes and sizes follow
// real Loom sessions (see app/app_server.py `_apply_event_to_item`): roughly one
// short commentary message per tool call, exec tools paired with a `process`
// item, edits recorded as cumulative `file_edit` diff snapshots.
//
//   ?turns=30        completed turns in the thread (default 30)
//   ?tools=30        average tool cycles per completed turn (default 30)
//   ?edits=0.3       share of cycles that write a file (default: the usual mix);
//   ?editLines=40    lines per write, for diff-heavy histories
//   ?threads=60      sidebar conversations (default 60)
//   ?motion=0        reduced motion (default: animations on, like a real user)
//   ?theme=light|dark
//   ?real=<url>      replay a JSON file of TurnRecord[] (the thread/read shape)
//
// Drive it from a test through window.perfFixture: startLive({ tools, deltaMs,
// toolMs, finalChars, plan, hold }) streams a turn (plan: the first tool is
// update_plan so the task dock shows; hold: leave the turn running for static
// visual checks), emit(method, params) sends any host notification, and
// progress() reports the live turn's completed tool cycles.
const params = new URLSearchParams(location.search);
const num = (name: string, fallback: number) => {
  const value = Number(params.get(name));
  return Number.isFinite(value) && params.has(name) ? value : fallback;
};
const theme = params.get("theme") || "light";
localStorage.removeItem("loom.inspector.open");
localStorage.setItem("loom.settings.language", params.get("lang") || "zh-CN");
localStorage.setItem("loom.settings.desktop.v2", JSON.stringify({ appearance: {
  theme, scale: "100", density: "comfortable", reducedMotion: params.get("motion") === "0",
} }));

const workspace = "C:/Workspace/Loom";
let seed = num("seed", 20261008);
const rnd = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
const int = (min: number, max: number) => min + Math.floor(rnd() * (max - min + 1));
const pick = <T,>(values: readonly T[]): T => values[Math.floor(rnd() * values.length)];
const iso = (offsetMs: number) => new Date(Date.parse("2026-10-08T01:00:00Z") + offsetMs).toISOString();

const commentary = [
  "我先看一下当前页面的状态，确认入口在哪里。",
  "页面已经打开了，接下来点击登录按钮，看看会跳转到哪一步。",
  "这里返回的是 401，说明会话没有带上凭据。我再检查一下请求头。",
  "配置文件已经读到了，回调地址和预期不一致，需要改成部署域名。",
  "测试通过了，我继续验证另一条路径，避免只修好其中一个入口。",
  "构建输出里有一个类型错误，定位到对应文件，马上修正。",
  "先不动现有逻辑，只补一个最小的判断，然后重新运行相关测试。",
  "这一步需要确认服务端日志，看看请求是否真正到达了网关。",
  "已经确认问题出在缓存键上：不同账号共用了同一个键。下面把账号维度加进去。",
  "Checking the deployment output before touching the configuration again.",
  "The request succeeded, so I'm moving on to the next verification step.",
];
const toolShapes = [
  { name: "browser_click", weight: 22, args: () => ({ selector: `button[data-id="${int(1, 400)}"]`, timeout_ms: 5000 }), content: 78, result: 677 },
  { name: "browser_eval", weight: 12, args: () => ({ expression: "document.querySelector('main').innerText.slice(0, 400)".repeat(int(1, 3)) }), content: 109, result: 224 },
  { name: "exec", weight: 18, args: () => ({ command: `npm run test -- --grep case-${int(1, 90)}`, cwd: workspace, timeout_seconds: 120 }), content: 1500, result: 2400 },
  { name: "update_plan", weight: 5, args: () => ({ plan: [] }), content: 83, result: 796 },
  { name: "browser_navigate", weight: 7, args: () => ({ url: `https://example.invalid/page/${int(1, 200)}` }), content: 66, result: 611 },
  { name: "browser_state", weight: 6, args: () => ({}), content: 135, result: 560 },
  { name: "tool_search", weight: 5, args: () => ({ query: "workspace file edit" }), content: 79, result: 744 },
  { name: "browser_type", weight: 5, args: () => ({ selector: "input[name=email]", text: "someone@example.invalid" }), content: 29, result: 707 },
  { name: "read_workspace_text", weight: 6, args: () => ({ path: `src/module-${int(1, 60)}.ts` }), content: 1800, result: 79 },
  { name: "write_workspace_text", weight: 6, args: () => ({ path: `src/module-${int(1, 60)}.ts`, content: "export const value = 1;\n".repeat(int(20, 80)) }), content: 78, result: 80 },
  { name: "search_workspace_text", weight: 4, args: () => ({ query: "TODO", glob: "src/**/*.ts" }), content: 900, result: 533 },
] as const;
const totalWeight = toolShapes.reduce((sum, shape) => sum + shape.weight, 0);
const editRate = num("edits", -1);
const editLines = num("editLines", 8);
function pickShape() {
  if (editRate >= 0 && rnd() < editRate) return toolShapes.find(shape => shape.name === "write_workspace_text")!;
  let roll = rnd() * totalWeight;
  for (const shape of toolShapes) if ((roll -= shape.weight) <= 0) return shape;
  return toolShapes[0];
}
const filler = (length: number) => {
  const chunk = "stdout line: build step finished in 120ms with 0 warnings\n";
  return chunk.repeat(Math.ceil(length / chunk.length)).slice(0, length);
};
const blob = (length: number) => ({ summary: "x".repeat(Math.max(0, length - 40)), ok: true });

function finalAnswer(turn: number, long = false, forced = 0) {
  const sections = forced || (long ? 6 : int(1, 3));
  return Array.from({ length: sections }, (_, section) => `## 第 ${turn}.${section} 部分：检查结果

已经完成这一轮检查，下面是需要你知道的结论。${"详细说明了原因、影响范围以及验证方式。".repeat(int(2, 6))}

- 登录回调地址已经改成部署域名，\`AUTH_REDIRECT_URL\` 需要同步更新。
- 邮件验证走的是 **同一条** 队列，失败会自动重试三次。
- 新增的测试覆盖了 \`session.refresh()\` 的过期分支。

| 项目 | 状态 | 备注 |
| --- | --- | --- |
| 回调地址 | 已完成 | 已验证 |
| 邮件验证 | 已完成 | 已验证 |
| 缓存键 | 已完成 | 加入账号维度 |

\`\`\`typescript
export function cacheKey(account: string, path: string): string {
  return \`\${account}:\${path.toLowerCase()}\`;
}
\`\`\`
`).join("\n") + `\n结束标记 ${turn}`;
}

type Item = Record<string, unknown> & { id: string; type: string };
type Turn = { id: string; threadId: string; status: string; startedAt: string; completedAt: string | null;
  finalItemId?: string; items: Item[] };

function buildTurn(threadId: string, index: number, cycles: number, clock: { t: number }, long = false): Turn {
  const turnId = `turn-${index}`;
  const stamp = () => iso((clock.t += 1400));
  const base = (id: string, type: string, extra: Record<string, unknown>): Item => {
    const at = stamp();
    return { id, threadId, turnId, type, status: "completed", createdAt: at, updatedAt: at, ...extra };
  };
  const items: Item[] = [base(`${turnId}-user`, "user_message", { text: `请继续检查第 ${index} 项配置，并把结果同步给我。`, source: "user" })];
  let diff = "";
  for (let cycle = 0; cycle < cycles; cycle++) {
    const text = pick(commentary);
    items.push(base(`${turnId}-a-${cycle}`, "assistant_message", { text, rawText: text, reasoning: "", phase: "commentary",
      stepId: `${turnId}-step-${cycle}`, runtimeAuthored: false, finishReason: "tool_calls" }));
    const shape = pickShape();
    const callId = `${turnId}-call-${cycle}`;
    const result = shape.name === "update_plan"
      ? { plan: ["检查配置", "修复缓存键", "验证登录"].map((step, order) => ({ step, status: order < 1 ? "completed" : "pending" })) }
      : blob(shape.result);
    items.push(base(`tool-${callId}`, "tool_call", { callId, toolName: shape.name, arguments: shape.args(), nested: false,
      ok: true, content: filler(shape.content), result }));
    if (shape.name === "exec") {
      items.push(base(`proc-${callId}`, "process", { processId: callId, argv: ["npm", "run", "test"], cwd: workspace,
        background: false, sandbox: {}, stdout: filler(shape.content), stderr: "", returncode: 0, timedOut: false }));
    }
    if (shape.name === "write_workspace_text") {
      const path = `src/module-${cycle}.ts`;
      diff += `diff --git a/${path} b/${path}\n--- a/${path}\n+++ b/${path}\n@@ -1,3 +1,${int(4, 20)} @@\n${"+export const value = 1;\n".repeat(int(Math.max(3, Math.floor(editLines / 2)), Math.max(4, editLines)))}`;
      items.push(base(`diff-${callId}`, "file_edit", { revision: cycle, paths: [path], diff, truncated: false }));
    }
  }
  const finalId = `${turnId}-final`;
  items.push(base(finalId, "assistant_message", { text: finalAnswer(index, long), rawText: "", reasoning: "", phase: "final_answer",
    stepId: `${turnId}-step-final`, finishReason: "stop" }));
  return { id: turnId, threadId, status: "completed", startedAt: items[0].createdAt as string,
    completedAt: items.at(-1)!.updatedAt as string, finalItemId: finalId, items };
}

const clock = { t: 0 };
const mainThreadId = "perf-0";
const realSource = params.get("real");
const realTurns: Turn[] | null = realSource ? await (await fetch(realSource)).json() : null;
if (realTurns) for (const turn of realTurns) { turn.threadId = mainThreadId; for (const item of turn.items) item.threadId = mainThreadId; }
const turnCount = realTurns ? realTurns.length : num("turns", 30);
const avgTools = num("tools", 30);
const turns: Turn[] = realTurns ?? Array.from({ length: turnCount }, (_, index) =>
  buildTurn(mainThreadId, index, Math.max(0, Math.round(avgTools * (0.3 + rnd() * 1.4))), clock));

const threadTitles = ["配置第三方登录与邮件验证", "修复模型面板卡片错位", "优化图片附件发送", "检查项目面板浅色主题", "优化审查面板", "确认 Computer Use 能力"];
const threads = Array.from({ length: num("threads", 60) }, (_, index) => ({
  id: index === 0 ? mainThreadId : `perf-${index}`, projectId: index % 3 ? "loom" : "agent",
  title: `${threadTitles[index % threadTitles.length]} ${index}`, customTitle: true, workspace,
  permissionMode: "full-access", status: "idle", createdAt: iso(0), updatedAt: iso(index * 1000),
  currentTurnId: null as string | null,
}));
const projects = [
  { id: "loom", name: "Loom", root: workspace, threadCount: 40 },
  { id: "agent", name: "loom-agent-test", root: "C:/Workspace/agent", threadCount: 20 },
];

const listeners = new Set<(event: unknown) => void>();
const emit = (method: string, params: Record<string, unknown>) => listeners.forEach(listener => listener({ method, params }));

// ---- live turn -----------------------------------------------------------
interface LiveOptions { tools?: number; deltaMs?: number; toolMs?: number; finalChars?: number; finalSections?: number; context?: boolean; text?: string; plan?: boolean; hold?: boolean }
let liveTurnSerial = 0;
let liveProgress = 0;
let liveDone: Promise<void> = Promise.resolve();
function startLive(options: LiveOptions = {}): Promise<void> {
  const { tools = 20, deltaMs = 24, toolMs = 160, finalChars = 1800, context = true } = options;
  const index = turnCount + liveTurnSerial++;
  const turnId = `turn-live-${index}`;
  const thread = threads[0];
  const at = () => iso((clock.t += 1200));
  const item = (id: string, type: string, extra: Record<string, unknown>) => ({ id, threadId: mainThreadId, turnId, type,
    status: "started", createdAt: at(), updatedAt: at(), ...extra });
  const finalText = finalAnswer(index, true, options.finalSections ?? 0).slice(0, finalChars);
  const collected: Item[] = [];
  const wait = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
  const trackCompleted = (completed: Item) => {
    collected.push(completed);
    emit("item/completed", { threadId: mainThreadId, item: completed });
  };
  liveDone = (async () => {
    const running = { ...thread, status: "running", currentTurnId: turnId };
    emit("thread/updated", { thread: running });
    emit("turn/started", { threadId: mainThreadId, turn: { id: turnId, threadId: mainThreadId, status: "running", startedAt: at(), items: [] } });
    const user = item(`${turnId}-user`, "user_message", { status: "completed", text: options.text ?? "继续", source: "user" });
    emit("item/started", { threadId: mainThreadId, item: user });
    collected.push(user as Item);
    liveProgress = 0;
    for (let cycle = 0; cycle < tools; cycle++) {
      liveProgress = cycle;
      const text = pick(commentary);
      const messageId = `${turnId}-a-${cycle}`;
      emit("item/started", { threadId: mainThreadId, item: item(messageId, "assistant_message", { status: "streaming", text: "", phase: "commentary", stepId: `${turnId}-s${cycle}` }) });
      for (let at2 = 0; at2 < text.length; at2 += 6) {
        emit("item/delta", { threadId: mainThreadId, itemId: messageId, delta: { text: text.slice(at2, at2 + 6) } });
        await wait(deltaMs);
      }
      trackCompleted(item(messageId, "assistant_message", { status: "completed", text, rawText: text, phase: "commentary", stepId: `${turnId}-s${cycle}` }) as Item);
      const shape = options.plan && cycle === 0 ? toolShapes.find(entry => entry.name === "update_plan")! : pickShape();
      const callId = `${turnId}-call-${cycle}`;
      const args = shape.args();
      emit("item/started", { threadId: mainThreadId, item: item(`tool-${callId}`, "tool_call", { callId, toolName: shape.name, arguments: args, nested: false }) });
      await wait(toolMs);
      if (shape.name === "exec") {
        const processId = `proc-${callId}`;
        emit("item/started", { threadId: mainThreadId, item: item(processId, "process", { processId: callId, argv: ["npm", "run", "test"], cwd: workspace, status: "running", stdout: "", stderr: "" }) });
        for (let chunk = 0; chunk < 6; chunk++) {
          emit("item/delta", { threadId: mainThreadId, itemId: processId, delta: { stdout: filler(240) } });
          await wait(deltaMs * 2);
        }
        trackCompleted(item(processId, "process", { processId: callId, argv: ["npm", "run", "test"], cwd: workspace, status: "completed", stdout: filler(1440), stderr: "", returncode: 0 }) as Item);
      }
      trackCompleted(item(`tool-${callId}`, "tool_call", { callId, toolName: shape.name, arguments: args, status: "completed", ok: true,
        content: filler(shape.content), result: shape.name === "update_plan"
          ? { plan: ["检查配置", "修复缓存键", "验证登录"].map((step, order) => ({ step, status: order <= cycle % 3 ? "completed" : "pending" })) }
          : blob(shape.result) }) as Item);
      if (context) emit("context/updated", { threadId: mainThreadId, context: contextReport(cycle) });
    }
    // Leave the turn running (status, plan dock, composer) for static visual checks.
    if (options.hold) await new Promise<never>(() => {});
    const finalId = `${turnId}-final`;
    emit("item/started", { threadId: mainThreadId, item: item(finalId, "assistant_message", { status: "streaming", text: "", phase: "final_answer", stepId: `${turnId}-sf` }) });
    for (let at2 = 0; at2 < finalText.length; at2 += 8) {
      emit("item/delta", { threadId: mainThreadId, itemId: finalId, delta: { text: finalText.slice(at2, at2 + 8) } });
      await wait(deltaMs);
    }
    trackCompleted(item(finalId, "assistant_message", { status: "completed", text: finalText, rawText: finalText, phase: "final_answer", stepId: `${turnId}-sf` }) as Item);
    const done = { id: turnId, threadId: mainThreadId, status: "completed", startedAt: collected[0].createdAt as string,
      completedAt: at(), finalItemId: finalId, items: collected };
    turns.push(done as Turn);
    thread.status = "idle";
    thread.currentTurnId = turnId;
    emit("turn/completed", { threadId: mainThreadId, turn: { ...done, items: [] } });
    emit("thread/updated", { thread: { ...thread } });
  })();
  return liveDone;
}
function contextReport(step: number) {
  const used = 90000 + step * 700;
  return { windowTokens: 512000, effectiveWindowTokens: 482000, inputBudgetTokens: 482000, outputReserveTokens: 30000,
    autoCompactTokens: 460000, toolOutputTokenLimit: 4000, windowKnown: true, limitsSource: "fixture", usedTokens: used,
    usedPercent: Math.round(used / 4820) / 1, freeTokens: 482000 - used, accounting: "fixture", messageCount: turnCount * 4,
    compactions: 0, lastCompactedAt: "", measuredAt: "fixture", segments: [
      { key: "conversation", tokens: used - 14000 }, { key: "toolSchemas", tokens: 14000 }, { key: "free", tokens: 482000 - used }],
    pressure: { blinded: false, toolOutputsReduced: 0, toolOutputsCollapsed: 0, userMessagesTruncated: 0, toolsOmitted: [] } };
}

const liveDefaults: LiveOptions = {};
Object.assign(window, { perfFixture: {
  startLive, emit: (method: string, params: Record<string, unknown>) => emit(method, params), turns: () => turns.length, setLiveDefaults: (options: LiveOptions) => Object.assign(liveDefaults, options),
  whenLiveDone: () => liveDone,
  progress: () => liveProgress,
  listenerCount: () => listeners.size,
  reads: [] as string[],
} });

// ---- bridge ----------------------------------------------------------------
const profile = { selection: "builtin:minimax", id: "minimax-m3", kind: "builtin", name: "MiniMax-M3",
  adapter: "openai-compatible", model: "MiniMax-M3", configured: true, vision: true };
const models = { primary: profile, profiles: [profile], activeModelId: profile.id,
  current: { ...profile, provider: profile.adapter }, recentModels: [] };

function threadRead(args: Record<string, unknown>) {
  const id = String(args.threadId);
  (window as unknown as { perfFixture: { reads: string[] } }).perfFixture.reads.push(id);
  const thread = threads.find(entry => entry.id === id) || threads[0];
  if (id !== mainThreadId) return { thread, turns: [], hasMoreTurns: false, oldestTurnId: null };
  if (args.turnLimit === 1) return { thread, turns: turns.slice(-1), hasMoreTurns: turns.length > 1 };
  const limit = Number(args.turnLimit) || 20;
  const before = String(args.beforeTurnId || "");
  const end = before ? turns.findIndex(turn => turn.id === before) : turns.length;
  const start = Math.max(0, end - limit);
  const slice = turns.slice(start, end);
  return { thread, turns: slice, hasMoreTurns: start > 0, oldestTurnId: slice[0]?.id ?? null };
}
const handlers: Record<string, (args: Record<string, unknown>) => unknown> = {
  "project/workspace_status": args => ({ projectId: args.projectId, root: workspace, exists: true, isDirectory: true,
    git: { available: true, isRepo: false, branch: "", summary: "", changedCount: 0, changedFiles: [], truncated: false },
    tree: { entries: [], truncated: false, limit: 100, maxDepth: 2 } }),
  "profile/insights": () => ({ range: { startDate: "2025-10-01", endDate: "2026-10-07", totalTokens: 0 },
    totals: { activeDays: 0, modelCalls: 0 }, streaks: { current: 0 }, peakDay: null, days: [] }),
  "thread/list": () => ({ threads, counts: { active: threads.length, archived: 0, all: threads.length } }),
  "project/list": () => ({ projects, unfiledThreadCount: 0 }),
  "thread/read": threadRead,
  "thread/context": () => ({ context: contextReport(0) }),
  "turn/start": args => {
    const turnId = `turn-live-${turnCount + liveTurnSerial}`;
    setTimeout(() => void startLive({ ...liveDefaults, text: String(args.input || "") }), 40);
    return { turn: { id: turnId, threadId: mainThreadId, status: "running", items: [] } };
  },
};
const bridge: Record<string, unknown> = {
  connect: async () => ({ protocolVersion: 1, serverInfo: { name: "perf-fixture", version: "0" }, capabilities: {},
    runtime: { model: profile.name, defaultWorkspace: workspace, defaultPermissionMode: "full-access",
      permissionModes: ["read-only", "approval", "workspace", "full-access"] } }),
  call: async (method: string, args: Record<string, unknown> = {}) => handlers[method]?.(args) || {},
  listModels: async () => models,
  accountStatus: async () => ({ ok: true, snapshot: { configured: true, reachable: true, authenticated: true,
    user: { id: "perf-user", name: "Loom Perf", email: "perf@example.invalid" }, serviceUrl: "" } }),
  onNotification: (listener: (event: unknown) => void) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  onUpdateStatus: () => () => {},
  getUpdateStatus: async () => ({ enabled: false, phase: "disabled", currentVersion: "0.1.0-fixture" }),
  setZoomFactor: (factor: number) => factor,
  setNativeTheme: async (source: string) => source === "dark" ? "dark" : "light",
  filePathFor: () => "",
};
(window as unknown as { loom: unknown }).loom = new Proxy(bridge, {
  get: (target, key: string) => key in target ? target[key] : async () => ({}),
});
void import("../../src/main");
