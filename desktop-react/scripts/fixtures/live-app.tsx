/**
 * Live app fixture: the real App and production stylesheet order over an
 * in-memory host that answers `turn/start` with a scripted run, emitted as the
 * same notifications the App Server sends (turn/started, item/started,
 * item/delta, item/completed, turn/completed, thread/updated). Sending from the
 * composer therefore exercises the whole live path: send bubble, run strip,
 * thinking, reasoning, tool rows, streaming answer, completion fold, sidebar
 * status and the composer's running state. No account, file or model request
 * can escape it.
 *
 *   open /scripts/fixtures/live-app.html
 *
 *   ?theme=dark|light      (default dark)
 *   ?lang=en               English UI and script
 *   ?scenario=code|approval|error|decision|quick   (default code)
 *   ?speed=0.5             slow the script (animations are not rescaled)
 *   ?empty=1               start on the home screen of an empty thread
 *   ?autosend=1            send the scenario prompt once the app is ready
 *
 * window.liveFixture.send(text?) types into the real composer and presses Enter.
 */
const params = new URLSearchParams(location.search);
const theme = params.get("theme") || "dark";
const english = params.get("lang") === "en";
const scenario = params.get("scenario") || "code";
const speed = Number(params.get("speed") || 1) || 1;
localStorage.removeItem("loom.inspector.open");
localStorage.setItem("loom.settings.language", english ? "en" : "zh-CN");
localStorage.setItem("loom.settings.desktop.v2", JSON.stringify({ appearance: {
  theme, scale: "100", density: "comfortable", reducedMotion: params.get("reduced") === "1",
} }));

type Item = Record<string, unknown> & { id: string; type: string };
type Listener = (event: { method: string; params: Record<string, unknown> }) => void;

const workspace = "C:/Workspace/Loom";
const historyAt = "2026-10-07T12:00:00Z";
const listeners = new Set<Listener>();
const emit = (method: string, payload: Record<string, unknown>) => listeners.forEach((listener) => listener({ method, params: payload }));
const now = () => new Date().toISOString();

const projects = [
  { id: "loom", name: "Loom", root: workspace, threadCount: 4 },
  { id: "relay", name: "termrelay", root: "C:/Workspace/relay", threadCount: 2 },
];
const titles = english
  ? ["Email validation on login", "Fix model panel alignment", "Image attachments", "Light theme audit", "Browser control", "Relay quick login"]
  : ["登录页邮箱校验", "修复模型面板卡片错位", "优化图片附件发送", "检查项目面板浅色主题", "排查浏览器控制", "中转站快捷登录"];
const threads = titles.map((title, index) => ({
  id: `live-${index}`, projectId: index < 4 ? "loom" : "relay", title, customTitle: true, workspace,
  permissionMode: "full-access", status: "idle", createdAt: historyAt, updatedAt: historyAt,
  usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
}));
const THREAD = threads[0].id;

// One completed turn of history so the live turn arrives below real content.
const history: Item[] = params.get("empty") === "1" ? [] : [
  { id: "h-user", type: "user_message", status: "completed", text: english ? "What does the login form validate today?" : "登录表单现在都校验了什么？" },
  { id: "h-a0", type: "assistant_message", phase: "commentary", status: "completed", text: english ? "Reading the form first." : "先看一下表单实现。" },
  { id: "h-read", type: "tool_call", toolName: "read_workspace_text", status: "completed", arguments: { path: "src/pages/Login.tsx" }, content: "export function Login() { … }" },
  { id: "h-a1", type: "assistant_message", phase: "final_answer", status: "completed",
    text: english ? "Only **required fields** are checked; the email format is not validated yet." : "目前只校验了**必填项**，邮箱格式还没有校验。" },
].map((item, index) => ({ ...item, threadId: THREAD, turnId: "turn-history", createdAt: new Date(Date.parse(historyAt) + index * 2000).toISOString() }));

const items: Item[] = [...history];
let thread = { ...threads[0] };
let turnSerial = 1;
let director: { token: number } = { token: 0 };

class Cancelled extends Error {}

function wait(ms: number, token: number): Promise<void> {
  return new Promise((resolve, reject) => window.setTimeout(() => {
    if (token !== director.token) reject(new Cancelled());
    else resolve();
  }, ms / speed));
}

function updateThread(patch: Record<string, unknown>) {
  thread = { ...thread, ...patch, updatedAt: now() };
  threads[0] = thread;
  emit("thread/updated", { thread: { ...thread } });
}

// The renderer keeps the objects it receives: every notification carries a
// copy, so the fixture's own record can change without mutating app state.
function start(turnId: string, item: Partial<Item> & { id: string; type: string }): Item {
  const full = { threadId: THREAD, turnId, createdAt: now(), status: "running", ...item } as Item;
  items.push(full);
  emit("item/started", { threadId: THREAD, item: { ...full } });
  return full;
}

function complete(item: Item, patch: Record<string, unknown> = {}) {
  Object.assign(item, { status: "completed", updatedAt: now() }, patch);
  emit("item/completed", { threadId: THREAD, item: { ...item } });
}

async function stream(item: Item, field: "text" | "reasoning", full: string, token: number, chunk = 4, every = 46) {
  const graphemes = Array.from(full);
  for (let index = 0; index < graphemes.length; index += chunk) {
    const delta = graphemes.slice(index, index + chunk).join("");
    item[field] = `${String(item[field] ?? "")}${delta}`;
    emit("item/delta", { threadId: THREAD, itemId: item.id, delta: { [field]: delta } });
    await wait(every, token);
  }
}

async function command(turnId: string, id: string, argv: string[], ms: number, stdout: string, token: number, ok = true) {
  // Runtime order: the exec wrapper starts ~30ms before its process item.
  const wrapper = start(turnId, { id: `${id}-exec`, type: "tool_call", toolName: "exec", arguments: { argv } });
  await wait(30, token);
  const process = start(turnId, { id, type: "process", argv });
  await wait(ms, token);
  complete(process, ok ? { stdout } : { status: "failed", stderr: stdout });
  complete(wrapper, ok ? { content: `exit=0\nstdout:\n${stdout}` } : { status: "failed", content: stdout });
}

async function tool(turnId: string, id: string, toolName: string, args: Record<string, unknown>, ms: number, token: number, content = "ok") {
  const call = start(turnId, { id, type: "tool_call", toolName, arguments: args });
  await wait(ms, token);
  complete(call, { content });
}

// Runtime order: the patch tool runs, then TURN_DIFF_UPDATED arrives as an
// already-completed file_edit carrying the whole turn's diff so far.
async function edit(turnId: string, id: string, path: string, diff: string, ms: number, token: number) {
  const call = start(turnId, { id: `${id}-patch`, type: "tool_call", toolName: "apply_patch", arguments: { patch: `*** Update File: ${path}` } });
  await wait(ms, token);
  const change = { id, threadId: THREAD, turnId, type: "file_edit", status: "completed", paths: [path], diff, createdAt: now(), updatedAt: now() } as Item;
  items.push(change);
  emit("item/completed", { threadId: THREAD, item: { ...change } });
  await wait(40, token);
  complete(call, { content: "applied" });
}

const COPY = english ? {
  prompt: "Add email format validation to the login page and cover it with a unit test.",
  r1: "The user wants email validation on login. Read the form and the shared validators before changing anything.",
  t1: "Reading the login form and the shared validators first.",
  t2: "`validators.ts` has no email rule yet. Adding one and wiring it into the form.",
  r2: "Keep the rule small: trim, one @, a dot in the domain. Show the error under the field.",
  t3: "Changes are in. Running the validator tests.",
  final: "Email validation is in place.\n\n### What changed\n\n- `src/utils/validators.ts` — new `isEmail()` rule (trims input, requires one `@` and a domain with a dot).\n- `src/pages/Login.tsx` — shows **请输入有效的邮箱** under the field and disables submit until it passes.\n- `src/utils/validators.test.ts` — 6 cases, all passing.\n\n```ts\nexport function isEmail(value: string): boolean {\n  return /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(value.trim());\n}\n```\n\nRun `npm test` any time to re-check.",
} : {
  prompt: "帮我给登录页加上邮箱格式校验，并补一个单元测试",
  r1: "用户要给登录页加邮箱校验。先读登录表单和公共校验工具，确认现有结构再动手。",
  t1: "先读一下登录表单和公共校验工具。",
  t2: "`validators.ts` 里还没有邮箱规则。我来补上，并接到表单里。",
  r2: "规则保持简单：去掉首尾空格，只有一个 @，域名里要有点。错误提示放在输入框下方。",
  t3: "改好了，跑一下校验相关的测试。",
  final: "邮箱校验已经加好了。\n\n### 改动\n\n- `src/utils/validators.ts`：新增 `isEmail()`，会先去掉首尾空格，再要求一个 `@` 和带点的域名。\n- `src/pages/Login.tsx`：输入不合法时在输入框下方提示 **请输入有效的邮箱**，并在通过前禁用登录按钮。\n- `src/utils/validators.test.ts`：6 个用例，全部通过。\n\n```ts\nexport function isEmail(value: string): boolean {\n  return /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(value.trim());\n}\n```\n\n之后随时可以运行 `npm test` 重新检查。",
};

const VALIDATOR_DIFF = "diff --git a/src/utils/validators.ts b/src/utils/validators.ts\n--- a/src/utils/validators.ts\n+++ b/src/utils/validators.ts\n@@ -1,3 +1,7 @@\n export function required(value: string) {\n   return value.trim().length > 0;\n }\n+\n+export function isEmail(value: string): boolean {\n+  return /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(value.trim());\n+}";
const LOGIN_DIFF = `${VALIDATOR_DIFF}\ndiff --git a/src/pages/Login.tsx b/src/pages/Login.tsx\n--- a/src/pages/Login.tsx\n+++ b/src/pages/Login.tsx\n@@ -12,6 +12,9 @@\n-  const valid = required(email) && required(password);\n+  const emailOk = isEmail(email);\n+  const valid = emailOk && required(password);\n+  const emailError = email && !emailOk ? "请输入有效的邮箱" : "";`;

async function runScript(turnId: string, token: number) {
  if (scenario === "quick") {
    await wait(900, token);
    const answer = start(turnId, { id: `${turnId}-a`, type: "assistant_message", phase: "final_answer", status: "streaming", text: "", reasoning: "" });
    await stream(answer, "text", english ? "Sure — the login form only checks required fields right now." : "好的，目前登录表单只检查了必填项。", token);
    complete(answer);
    return "completed";
  }

  await wait(700, token);
  const a1 = start(turnId, { id: `${turnId}-a1`, type: "assistant_message", phase: "commentary", status: "streaming", text: "", reasoning: "" });
  await wait(500, token);
  await stream(a1, "reasoning", COPY.r1, token);
  await wait(240, token);
  await stream(a1, "text", COPY.t1, token);
  complete(a1);
  await wait(260, token);

  await tool(turnId, `${turnId}-read1`, "read_workspace_text", { path: "src/pages/Login.tsx" }, 420, token, "export function Login() { … }");
  await wait(120, token);
  await tool(turnId, `${turnId}-read2`, "read_workspace_text", { path: "src/utils/validators.ts" }, 360, token, "export function required(value: string) { … }");
  await wait(140, token);
  await tool(turnId, `${turnId}-search`, "search_workspace_text", { query: "isEmail" }, 520, token, "No matches.");
  await wait(620, token);

  if (scenario === "approval") {
    const call = start(turnId, { id: `${turnId}-net`, type: "tool_call", toolName: "exec", status: "waiting_approval", arguments: { argv: ["npm", "install", "email-validator"] } });
    const approval = start(turnId, { id: `${turnId}-approval`, type: "approval", status: "waiting_approval", toolName: "exec", callId: `${turnId}-net`, reason: english ? "Installs a package from the network" : "需要联网安装依赖" });
    updateThread({ status: "waiting_approval" });
    emit("approval/requested", { approval: { requestId: "fixture", threadId: THREAD, turnId, itemId: call.id, approvalItemId: approval.id, callId: call.id,
      requestType: "toolExecution", approvalStage: "", retryReason: null, startedAtMs: Date.now(), toolName: "exec", arguments: call.arguments,
      effect: "", reason: String(approval.reason), permissionMode: null, networkApprovalContext: null, availableDecisions: ["accept", "decline"] } });
    await wait(4200, token);
    complete(approval, { status: "approved" });
    complete(call);
    updateThread({ status: "running" });
    await wait(400, token);
  }

  const a2 = start(turnId, { id: `${turnId}-a2`, type: "assistant_message", phase: "commentary", status: "streaming", text: "", reasoning: "" });
  await wait(380, token);
  await stream(a2, "reasoning", COPY.r2, token);
  await wait(200, token);
  await stream(a2, "text", COPY.t2, token);
  complete(a2);
  await wait(300, token);

  await edit(turnId, `${turnId}-edit1`, "src/utils/validators.ts", VALIDATOR_DIFF, 520, token);
  await wait(160, token);
  await edit(turnId, `${turnId}-edit2`, "src/pages/Login.tsx", LOGIN_DIFF, 640, token);
  await wait(420, token);

  const a3 = start(turnId, { id: `${turnId}-a3`, type: "assistant_message", phase: "commentary", status: "streaming", text: "" });
  await wait(300, token);
  await stream(a3, "text", COPY.t3, token);
  complete(a3);
  await wait(240, token);

  if (scenario === "error") {
    await command(turnId, `${turnId}-test`, ["npm", "test", "--", "validators"], 1300, "FAIL src/utils/validators.test.ts\n  ✕ rejects addresses without a domain dot (4 ms)", token, false);
    await wait(500, token);
    const failure = start(turnId, { id: `${turnId}-error`, type: "error", status: "failed", error: english ? "The model provider closed the connection." : "模型服务断开了连接，这一轮没有完成。" });
    complete(failure, { status: "failed" });
    return "failed";
  }

  await command(turnId, `${turnId}-test`, ["npm", "test", "--", "validators"], 1600,
    " PASS  src/utils/validators.test.ts\n  isEmail\n    ✓ accepts a plain address (2 ms)\n    ✓ trims surrounding spaces (1 ms)\n    ✓ rejects a missing @ (1 ms)\n    ✓ rejects two @ (1 ms)\n    ✓ rejects a domain without a dot\n    ✓ rejects whitespace inside\n\nTests: 6 passed, 6 total", token);
  await wait(700, token);

  const final = start(turnId, { id: `${turnId}-final`, type: "assistant_message", phase: "final_answer", status: "streaming", text: "" });
  await wait(260, token);
  if (scenario === "decision") {
    const decision = { id: "next", title: english ? "What next?" : "接下来做什么？", description: english ? "Validation is in. Pick a follow-up." : "校验已经加好，选一个后续方向。", allowCustomInput: true,
      options: [{ id: "a", title: english ? "Also validate the register page" : "注册页也加上同样的校验", recommended: true, description: english ? "Reuse isEmail()." : "复用 isEmail()。" },
        { id: "b", title: english ? "Stop here" : "先到这里", description: english ? "Review the diff first." : "我先审查一下改动。" }] };
    await stream(final, "text", `${english ? "Done." : "改好了。"}\n\n\`\`\`loom-decision\n${JSON.stringify(decision)}\n\`\`\``, token, 24, 30);
  } else {
    await stream(final, "text", COPY.final, token, 5, 40);
  }
  complete(final);
  return "completed";
}

async function play(text: string) {
  const token = ++director.token;
  const turnId = `turn-live-${turnSerial++}`;
  const startedAt = now();
  try {
    await wait(140, token);
    updateThread({ status: "running", currentTurnId: turnId });
    emit("turn/started", { threadId: THREAD, turn: { id: turnId, threadId: THREAD, status: "running", startedAt, items: [] } });
    start(turnId, { id: `${turnId}-user`, type: "user_message", status: "completed", text });
    const status = await runScript(turnId, token);
    await wait(180, token);
    const finalItemId = status === "completed" ? `${turnId}-${scenario === "quick" ? "a" : "final"}` : "";
    emit("turn/completed", { threadId: THREAD, turn: { id: turnId, threadId: THREAD, status, startedAt, completedAt: now(), finalItemId, items: [] } });
    updateThread({ status, currentTurnId: turnId, usage: { inputTokens: 21_400, outputTokens: 1_900, totalTokens: 23_300 } });
  } catch (error) {
    if (!(error instanceof Cancelled)) throw error;
  }
}

// Runtime order for a user stop: unfinished activity is projected as
// interrupted, TURN_CANCELLED becomes an error item ("cancelled by user"),
// then the turn and thread report cancelled.
function interrupt() {
  const turnId = String(thread.currentTurnId ?? "");
  director.token += 1;
  for (const item of items) {
    if (item.turnId === turnId && ["running", "streaming", "waiting_approval"].includes(String(item.status))) complete(item, { status: "interrupted" });
  }
  const stopped = { id: `${turnId}-cancelled`, threadId: THREAD, turnId, type: "error", status: "completed", error: "cancelled by user", createdAt: now() } as Item;
  items.push(stopped);
  emit("item/completed", { threadId: THREAD, item: { ...stopped } });
  emit("turn/completed", { threadId: THREAD, turn: { id: turnId, threadId: THREAD, status: "cancelled", items: [] } });
  updateThread({ status: "cancelled" });
}

const profile = { selection: "builtin:minimax", id: "minimax-m3", kind: "builtin", name: "MiniMax-M3",
  adapter: "openai-compatible", model: "MiniMax-M3", configured: true, vision: true };
const models = { primary: profile, profiles: [profile], activeModelId: profile.id,
  current: { ...profile, provider: profile.adapter }, recentModels: [] };

function snapshot(id: string) {
  const record = threads.find((entry) => entry.id === id) || threads[0];
  if (id !== THREAD) return { thread: record, turns: [] };
  const turnIds = [...new Set(items.map((item) => String(item.turnId)))];
  return { thread: record, turns: turnIds.map((turnId) => ({ id: turnId, threadId: id, status: "completed", startedAt: historyAt, completedAt: historyAt,
    finalItemId: turnId === "turn-history" ? "h-a1" : "", items: items.filter((item) => item.turnId === turnId) })) };
}

const handlers: Record<string, (args: Record<string, unknown>) => unknown> = {
  "project/workspace_status": (args) => ({ projectId: args.projectId, root: workspace, exists: true, isDirectory: true,
    git: { available: true, isRepo: false, branch: "", summary: "", changedCount: 0, changedFiles: [], truncated: false },
    tree: { entries: [], truncated: false, limit: 100, maxDepth: 2 } }),
  "profile/insights": () => ({ range: { startDate: "2025-10-01", endDate: "2026-10-07", totalTokens: 0 },
    totals: { activeDays: 0, modelCalls: 0 }, streaks: { current: 0 }, peakDay: null, days: [] }),
  "thread/list": () => ({ threads, counts: { active: threads.length, archived: 12, all: threads.length + 12 } }),
  "project/list": () => ({ projects, unfiledThreadCount: 0 }),
  "thread/read": (args) => snapshot(String(args.threadId)),
  "thread/context": () => ({ context: null }),
  "turn/start": (args) => {
    const text = String(args.input ?? "");
    window.setTimeout(() => void play(text), 0);
    return { turn: { id: `turn-live-${turnSerial}`, threadId: THREAD, status: "running", items: [] } };
  },
  "turn/interrupt": () => {
    window.setTimeout(interrupt, 220 / speed);
    return {};
  },
  "approval/respond": () => ({}),
};

const bridge: Record<string, unknown> = {
  connect: async () => ({ protocolVersion: 1, serverInfo: { name: "live-fixture", version: "0" }, capabilities: {},
    runtime: { model: profile.name, defaultWorkspace: workspace, defaultPermissionMode: "full-access",
      permissionModes: ["read-only", "approval", "workspace", "full-access"] } }),
  call: async (method: string, args: Record<string, unknown> = {}) => structuredClone(handlers[method]?.(args) || {}),
  listModels: async () => models,
  accountStatus: async () => ({ ok: true, snapshot: { configured: true, reachable: true, authenticated: true,
    user: { id: "live-user", name: "Loom Designer", email: "live@example.invalid" }, serviceUrl: "" } }),
  onNotification: (listener: Listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  setZoomFactor: (factor: number) => factor,
  setNativeTheme: async (source: string) => (source === "dark" ? "dark" : "light"),
  filePathFor: () => "",
  readLocalImage: async () => { throw new Error("fixture"); },
};
(window as unknown as { loom: unknown }).loom = new Proxy(bridge, {
  // A subscription (onSomething) hands back its unsubscribe function; anything else is an async call.
  get: (target, key: string) => (key in target ? target[key] : /^on[A-Z]/.test(key) ? () => () => {} : async () => ({})),
});

async function send(text = COPY.prompt) {
  const textarea = await new Promise<HTMLTextAreaElement>((resolve) => {
    const find = () => {
      const element = document.querySelector<HTMLTextAreaElement>(".composer textarea:not([disabled])");
      if (element) resolve(element);
      else window.setTimeout(find, 50);
    };
    find();
  });
  textarea.focus();
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  setter?.call(textarea, text);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  await new Promise((resolve) => window.setTimeout(resolve, 60));
  textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
}

Object.assign(window, { liveFixture: { send, emit, items, interrupt, prompt: COPY.prompt } });
if (params.get("autosend") === "1") window.setTimeout(() => void send(), 900);
void import("../../src/main");
