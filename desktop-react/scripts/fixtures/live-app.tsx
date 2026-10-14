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
 *   ?scenario=code|approval|error|decision|quick|long|burst   (default code)
 *       long: a thinking pause, then one long Markdown answer; burst: a dozen quick reads/searches, one note, a
 *       command, then the long answer. ?cadence=real delivers their text like a provider does (clumps and gaps);
 *       ?repeat=N makes the answer N times as long.
 *   ?scenario=fuzz&seed=N   a seeded random run: stages of tool work, notes, approvals, parallel calls, failures, and
 *       an ending (answer, error, a stop, a bare reply). The same seed plays the same run.
 *   ?speed=0.5             slow the script (animations are not rescaled)
 *   ?empty=1               start on the home screen of an empty thread
 *   ?autosend=1            send the scenario prompt once the app is ready
 *   ?latency=1500          the Host takes this long (ms) to answer `turn/start` (and `thread/start`, for a message sent from
 *                          the home screen of a conversation that does not exist yet) and to start the run, as a real one does:
 *                          account check + request preparation measured 0.5-3 s on a slow route (docs/send-flight-2026-10-14.md)
 *
 * window.liveFixture.send(text?) types into the real composer and presses Enter.
 */
const params = new URLSearchParams(location.search);
const theme = params.get("theme") || "dark";
const english = params.get("lang") === "en";
const scenario = params.get("scenario") || "code";
const speed = Number(params.get("speed") || 1) || 1;
const latency = Math.max(0, Number(params.get("latency") || 0) || 0);
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

// A provider does not deliver text on a metronome: tokens come in clumps of one to a handful of glyphs, with gaps
// from a few ms to a few hundred. ?cadence=real replays that shape (seeded, so two runs of one scenario match).
const realCadence = params.get("cadence") === "real";
let seed = 20261011;
const random = () => {
  seed = (seed * 1664525 + 1013904223) >>> 0;
  return seed / 0x100000000;
};

async function streamReal(item: Item, field: "text" | "reasoning", full: string, token: number) {
  const graphemes = Array.from(full);
  for (let index = 0; index < graphemes.length;) {
    const size = 1 + Math.floor(random() * random() * 7);
    const delta = graphemes.slice(index, index + size).join("");
    index += size;
    item[field] = `${String(item[field] ?? "")}${delta}`;
    emit("item/delta", { threadId: THREAD, itemId: item.id, delta: { [field]: delta } });
    await wait(random() < 0.06 ? 120 + random() * 240 : 12 + random() * 52, token);
  }
}

const LONG_ANSWER = english
  ? "I went through the whole login flow. The short version: email validation is missing in three places, listed here from most to least harmful.\n\n## What I found\n\n1. **The form only checks required fields.** `valid` in `Login.tsx` looks at whether two fields are empty, so a malformed address goes all the way to the backend, which answers with a vague 400 and the user only sees \"sign-in failed\".\n2. **The shared validators have no email rule.** `validators.ts` exports `required()` and nothing else; the register and reset pages each carry an inline regex of their own, and the two have already drifted apart.\n3. **The error sits in the wrong place.** It is rendered in a global area at the bottom of the form, so fixing the field does not visibly clear it and nobody can tell which field it belongs to.\n\n## Options\n\n| Option | Scope | Risk | Note |\n| --- | --- | --- | --- |\n| Inline regex in Login.tsx | 1 file | low | quickest, keeps the duplication |\n| Extract `isEmail()` and reuse it | 3 files | low | recommended: one rule everywhere |\n| Add a validation library | many | medium | too heavy for a single rule |\n\nI would take the second option. The rule itself stays deliberately small:\n\n```ts\nexport function isEmail(value: string): boolean {\n  const text = value.trim();\n  const at = text.indexOf(\"@\");\n  return at > 0 && at === text.lastIndexOf(\"@\") && text.indexOf(\".\", at) > at + 1 && !/\\s/.test(text);\n}\n```\n\n## Next\n\n- change `validators.ts` and add the unit tests;\n- wire the login page to it and move the message under the field;\n- make the register and reset pages reuse the same function and delete the duplicated regexes.\n\n> Do not reach for the full RFC 5322 expression on the client: rejecting a real address costs far more than letting one bad address through.\n\nIf that direction works for you I will start with the first step."
  : "我把整个登录流程梳理了一遍，结论先放在前面：邮箱校验的缺口主要在三个地方，下面按影响从大到小说明。\n\n## 发现的问题\n\n1. **前端只校验了必填项。** `Login.tsx` 里的 `valid` 只看两个字段是不是空，格式不对的邮箱可以一路提交到后端，后端返回一个笼统的 400，用户看到的只有“登录失败”。\n2. **公共校验工具缺少邮箱规则。** `validators.ts` 里只有 `required()`，注册页和找回密码页各自写了一份内联正则，两份规则已经不一致。\n3. **错误提示位置不对。** 提示放在表单底部的全局区域，改完字段后看不到它消失，也分不清是哪个字段的问题。\n\n## 方案对比\n\n| 方案 | 改动范围 | 风险 | 备注 |\n| --- | --- | --- | --- |\n| 在 Login.tsx 内联正则 | 1 个文件 | 低 | 最快，但会继续重复 |\n| 抽出 `isEmail()` 并复用 | 3 个文件 | 低 | 推荐，一次统一三处规则 |\n| 引入第三方校验库 | 多处 | 中 | 对一条规则来说过重 |\n\n我倾向于第二种。规则本身刻意保持很小：\n\n```ts\nexport function isEmail(value: string): boolean {\n  const text = value.trim();\n  const at = text.indexOf(\"@\");\n  return at > 0 && at === text.lastIndexOf(\"@\") && text.indexOf(\".\", at) > at + 1 && !/\\s/.test(text);\n}\n```\n\n## 接下来\n\n- 先改 `validators.ts` 并补单元测试；\n- 再把登录页接上，把提示挪到输入框下方；\n- 最后让注册页和找回密码页复用同一个函数，删除重复的正则。\n\n> 注意：不要在前端放完整的 RFC 5322 正则，误杀一个合法地址的代价，比放过一个坏地址高得多。\n\n如果你同意这个方向，我就从第一步开始。";

// ?repeat=N makes the long answer N times as long (a report, a long file), to see how rendering scales with length.
const repeat = Math.max(1, Math.min(12, Number(params.get("repeat") || 1) || 1));
const LONG_TEXT = repeat === 1 ? LONG_ANSWER : Array.from({ length: repeat }, () => LONG_ANSWER).join(String.fromCharCode(10, 10) + "---" + String.fromCharCode(10, 10));

const BURST_STEPS: Array<[string, Record<string, unknown>, string]> = [
  ["read_workspace_text", { path: "src/pages/Login.tsx" }, "export function Login() { … }"],
  ["read_workspace_text", { path: "src/pages/Register.tsx" }, "export function Register() { … }"],
  ["read_workspace_text", { path: "src/pages/Reset.tsx" }, "export function Reset() { … }"],
  ["read_workspace_text", { path: "src/utils/validators.ts" }, "export function required(value: string) { … }"],
  ["search_workspace_text", { query: "isEmail" }, "No matches."],
  ["search_workspace_text", { query: "emailPattern" }, "src/pages/Register.tsx:14\nsrc/pages/Reset.tsx:9"],
  ["read_workspace_text", { path: "src/components/Field.tsx" }, "export function Field() { … }"],
  ["read_workspace_text", { path: "src/components/FormError.tsx" }, "export function FormError() { … }"],
  ["search_workspace_text", { query: "FormError" }, "src/pages/Login.tsx:31"],
  ["read_workspace_text", { path: "src/i18n/zh-CN.ts" }, "export default { … }"],
  ["read_workspace_text", { path: "src/i18n/en.ts" }, "export default { … }"],
  ["search_workspace_text", { query: "login.error" }, "src/i18n/zh-CN.ts:88"],
];

async function runLongScript(turnId: string, token: number) {
  const say = realCadence ? streamReal : (item: Item, field: "text" | "reasoning", full: string, at: number) => stream(item, field, full, at, 3, 38);
  await wait(500, token);
  const a1 = start(turnId, { id: `${turnId}-a1`, type: "assistant_message", status: "streaming", text: "", reasoning: "" });
  await wait(420, token);
  await say(a1, "reasoning", COPY.r1, token);
  await wait(200, token);
  await say(a1, "text", COPY.t1, token);
  complete(a1, { phase: "commentary" });
  await wait(240, token);
  if (scenario === "burst") {
    for (const [index, [toolName, args, content]] of BURST_STEPS.entries()) {
      await tool(turnId, `${turnId}-b${index}`, toolName, args, 160 + Math.floor(random() * 520), token, content);
      await wait(40 + Math.floor(random() * 160), token);
      if (index === 5) {
        const note = start(turnId, { id: `${turnId}-n${index}`, type: "assistant_message", status: "streaming", text: "" });
        await wait(260, token);
        await say(note, "text", COPY.t2, token);
        complete(note, { phase: "commentary" });
        await wait(200, token);
      }
    }
    await command(turnId, `${turnId}-test`, ["npm", "test"], 1400, " PASS  src/utils/validators.test.ts\n\nTests: 6 passed, 6 total", token);
    await wait(500, token);
  } else {
    await tool(turnId, `${turnId}-read1`, "read_workspace_text", { path: "src/pages/Login.tsx" }, 420, token, "export function Login() { … }");
    await wait(420, token);
  }
  const final = start(turnId, { id: `${turnId}-final`, type: "assistant_message", status: "streaming", text: "" });
  await wait(300, token);
  await say(final, "text", LONG_TEXT, token);
  complete(final, { phase: "final_answer" });
  return "completed";
}

// ?scenario=fuzz&seed=N: a seeded random run, for finding what the scripted scenarios do not. Every choice (how many
// stages, which tools, which fail, whether an approval stops it, how it ends) comes from one generator, so a run that
// misbehaves can be replayed by its seed.
function generator(seedValue: number) {
  let state = (seedValue * 2654435761) >>> 0 || 1;
  const next = () => {
    state ^= state << 13; state >>>= 0;
    state ^= state >>> 17;
    state ^= state << 5; state >>>= 0;
    return state / 0x100000000;
  };
  return {
    next,
    chance: (p: number) => next() < p,
    between: (low: number, high: number) => low + next() * (high - low),
    int: (low: number, high: number) => Math.floor(low + next() * (high - low + 1)),
    pick: <T,>(list: readonly T[]) => list[Math.floor(next() * list.length)],
  };
}

const FUZZ_PATHS = ["src/pages/Login.tsx", "src/pages/Register.tsx", "src/utils/validators.ts", "src/components/Field.tsx", "src/i18n/zh-CN.ts", "package.json", "src/api/client.ts", "README.md"];
const FUZZ_NOTES = english
  ? ["Reading the form first.", "That file has no validator yet; checking where the rules live.", "Found it. Wiring the rule in.", "One more place uses the old regex.", "Running the tests now."]
  : ["先看一下表单实现。", "这个文件里还没有校验，看看规则放在哪里。", "找到了，把规则接进去。", "还有一处用的是旧正则。", "现在跑测试。"];
const FUZZ_PARAS = english
  ? ["The form only checks required fields, so a malformed address reaches the backend.", "The shared validators had no email rule; each page carried its own regex.", "I moved the rule into one helper and pointed all three pages at it.", "Tests cover the plain address, trimming, a missing @ and a domain without a dot."]
  : ["表单只校验了必填项，格式不对的邮箱会一路提交到后端。", "公共校验工具里没有邮箱规则，每个页面各写了一份正则。", "我把规则收进同一个函数，三个页面都改成使用它。", "测试覆盖了普通地址、首尾空格、缺少 @ 和域名里没有点的情况。"];

const FUZZ_DECISION = {
  id: "next", title: english ? "What next?" : "接下来做什么？", allowCustomInput: true,
  options: [
    { id: "a", title: english ? "Also validate the register page" : "注册页也加上同样的校验", recommended: true, description: english ? "Reuse isEmail()." : "复用 isEmail()。" },
    { id: "b", title: english ? "Stop here" : "先到这里", description: english ? "Review the diff first." : "我先审查一下改动。" },
  ],
};

function fuzzAnswer(random: ReturnType<typeof generator>): string {
  const parts: string[] = [random.pick(FUZZ_PARAS)];
  const blocks = random.int(1, 6);
  for (let index = 0; index < blocks; index += 1) {
    const kind = random.pick(["p", "p", "list", "code", "table", "heading", "quote"] as const);
    if (kind === "p") parts.push(random.pick(FUZZ_PARAS) + (random.chance(0.4) ? " " + random.pick(FUZZ_PARAS) : ""));
    else if (kind === "list") parts.push(Array.from({ length: random.int(2, 5) }, (_, item) => `${random.chance(0.5) ? "- " : `${item + 1}. `}${random.pick(FUZZ_PARAS)}`).join("\n"));
    else if (kind === "code") parts.push("```ts\n" + Array.from({ length: random.int(2, 9) }, (_, line) => `  const value${line} = compute(${line});`).join("\n") + "\n```");
    else if (kind === "table") parts.push("| 文件 | 改动 | 结果 |\n| --- | --- | --- |\n" + Array.from({ length: random.int(2, 5) }, () => `| ${random.pick(FUZZ_PATHS)} | ${random.int(1, 40)} 行 | ${random.pick(["通过", "已更新", "新增"])} |`).join("\n"));
    else if (kind === "heading") parts.push(`## ${random.pick(["改动", "验证", "后续", "风险"])}`);
    else parts.push("> " + random.pick(FUZZ_PARAS));
  }
  // Now and then the answer ends by asking: a decision card follows its text.
  if (random.chance(0.2)) parts.push("```loom-decision\n" + JSON.stringify(FUZZ_DECISION) + "\n```");
  return parts.join("\n\n");
}

async function runFuzzScript(turnId: string, token: number): Promise<string> {
  const random = generator(Number(params.get("seed") || 1) || 1);
  let counter = 0;
  const id = (name: string) => `${turnId}-${name}${counter++}`;
  const say = async (kind: "commentary" | "final_answer", text: string, withReasoning = false) => {
    const message = start(turnId, { id: id("m"), type: "assistant_message", status: "streaming", text: "", reasoning: "" });
    await wait(random.between(120, 520), token);
    if (withReasoning) { await stream(message, "reasoning", COPY.r1, token, random.int(2, 6), random.between(20, 50)); await wait(random.between(60, 240), token); }
    await stream(message, "text", text, token, random.int(2, 7), random.between(16, 46));
    complete(message, { phase: kind });
  };

  await wait(random.between(300, 900), token);
  if (random.chance(0.6)) await say("commentary", random.pick(FUZZ_NOTES), random.chance(0.5));

  const stages = random.int(1, 4);
  for (let stage = 0; stage < stages; stage += 1) {
    const steps = random.int(1, 9);
    for (let step = 0; step < steps; step += 1) {
      const kind = random.pick(["read", "read", "search", "command", "command", "edit", "parallel"] as const);
      if (kind === "read" || kind === "search") {
        await tool(turnId, id("t"), kind === "read" ? "read_workspace_text" : "search_workspace_text",
          kind === "read" ? { path: random.pick(FUZZ_PATHS) } : { query: random.pick(["isEmail", "validate", "required"]) }, random.between(120, 900), token, "ok");
      } else if (kind === "command") {
        const fails = random.chance(0.15);
        await command(turnId, id("c"), random.pick([["npm", "test"], ["git", "status"], ["node", "scripts/check.mjs"]]), random.between(300, 2200),
          fails ? "FAIL src/utils/validators.test.ts\n  ✕ rejects addresses without a domain dot (4 ms)" : "ok\nTests: 6 passed, 6 total", token, !fails);
      } else if (kind === "edit") {
        await edit(turnId, id("e"), random.pick(FUZZ_PATHS), VALIDATOR_DIFF, random.between(200, 900), token);
      } else {
        const first = start(turnId, { id: id("p"), type: "tool_call", toolName: "read_workspace_text", arguments: { path: random.pick(FUZZ_PATHS) } });
        const second = start(turnId, { id: id("p"), type: "tool_call", toolName: "search_workspace_text", arguments: { query: "isEmail" } });
        await wait(random.between(150, 700), token);
        const [early, late] = random.chance(0.5) ? [first, second] : [second, first];
        complete(early, { content: "ok" });
        await wait(random.between(30, 400), token);
        complete(late, { content: "ok" });
      }
      await wait(random.between(20, 260), token);
    }
    if (random.chance(0.12)) {
      const call = start(turnId, { id: id("n"), type: "tool_call", toolName: "exec", status: "waiting_approval", arguments: { argv: ["npm", "install", "left-pad"] } });
      const approval = start(turnId, { id: id("a"), type: "approval", status: "waiting_approval", toolName: "exec", callId: call.id, reason: english ? "Installs a package" : "需要联网安装依赖" });
      updateThread({ status: "waiting_approval" });
      await wait(random.between(500, 2500), token);
      complete(approval, { status: random.chance(0.8) ? "approved" : "denied" });
      complete(call);
      updateThread({ status: "running" });
      await wait(random.between(100, 500), token);
    }
    if (stage < stages - 1 && random.chance(0.6)) await say("commentary", random.pick(FUZZ_NOTES), random.chance(0.25));
  }

  const ending = random.next();
  if (ending < 0.12) {
    const failure = start(turnId, { id: id("x"), type: "error", status: "failed", error: english ? "The model provider closed the connection." : "模型服务断开了连接，这一轮没有完成。" });
    complete(failure, { status: "failed" });
    return "failed";
  }
  if (ending < 0.22) {
    // The reader stops it mid-reply.
    const message = start(turnId, { id: id("m"), type: "assistant_message", status: "streaming", text: "", reasoning: "" });
    window.setTimeout(interrupt, random.between(400, 1400) / speed);
    await stream(message, "text", fuzzAnswer(random), token, 3, 40);
    return "completed";
  }
  await say("final_answer", fuzzAnswer(random));
  return "completed";
}

async function runScript(turnId: string, token: number) {
  if (scenario === "long" || scenario === "burst") return runLongScript(turnId, token);
  if (scenario === "fuzz") return runFuzzScript(turnId, token);
  if (scenario === "quick") {
    await wait(900, token);
    const answer = start(turnId, { id: `${turnId}-a`, type: "assistant_message", status: "streaming", text: "", reasoning: "" });
    await stream(answer, "text", english ? "Sure — the login form only checks required fields right now." : "好的，目前登录表单只检查了必填项。", token);
    complete(answer, { phase: "final_answer" });
    return "completed";
  }

  await wait(700, token);
  const a1 = start(turnId, { id: `${turnId}-a1`, type: "assistant_message", status: "streaming", text: "", reasoning: "" });
  await wait(500, token);
  await stream(a1, "reasoning", COPY.r1, token);
  await wait(240, token);
  await stream(a1, "text", COPY.t1, token);
  complete(a1, { phase: "commentary" });
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

  const a2 = start(turnId, { id: `${turnId}-a2`, type: "assistant_message", status: "streaming", text: "", reasoning: "" });
  await wait(380, token);
  await stream(a2, "reasoning", COPY.r2, token);
  await wait(200, token);
  await stream(a2, "text", COPY.t2, token);
  complete(a2, { phase: "commentary" });
  await wait(300, token);

  await edit(turnId, `${turnId}-edit1`, "src/utils/validators.ts", VALIDATOR_DIFF, 520, token);
  await wait(160, token);
  await edit(turnId, `${turnId}-edit2`, "src/pages/Login.tsx", LOGIN_DIFF, 640, token);
  await wait(420, token);

  const a3 = start(turnId, { id: `${turnId}-a3`, type: "assistant_message", status: "streaming", text: "" });
  await wait(300, token);
  await stream(a3, "text", COPY.t3, token);
  complete(a3, { phase: "commentary" });
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

  const final = start(turnId, { id: `${turnId}-final`, type: "assistant_message", status: "streaming", text: "" });
  await wait(260, token);
  if (scenario === "decision") {
    const decision = { id: "next", title: english ? "What next?" : "接下来做什么？", description: english ? "Validation is in. Pick a follow-up." : "校验已经加好，选一个后续方向。", allowCustomInput: true,
      options: [{ id: "a", title: english ? "Also validate the register page" : "注册页也加上同样的校验", recommended: true, description: english ? "Reuse isEmail()." : "复用 isEmail()。" },
        { id: "b", title: english ? "Stop here" : "先到这里", description: english ? "Review the diff first." : "我先审查一下改动。" }] };
    await stream(final, "text", `${english ? "Done." : "改好了。"}\n\n\`\`\`loom-decision\n${JSON.stringify(decision)}\n\`\`\``, token, 24, 30);
  } else {
    await stream(final, "text", COPY.final, token, 5, 40);
  }
  complete(final, { phase: "final_answer" });
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
    const finalItemId = status === "completed" && scenario !== "fuzz" ? `${turnId}-${scenario === "quick" ? "a" : "final"}` : "";
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
  // The whole shape the profile page reads (a page that is handed less than this throws while it renders).
  "profile/insights": () => ({ generatedAt: "2026-10-07T08:00:00Z", range: { startDate: "2025-10-01", endDate: "2026-10-07", totalTokens: 3_820_000 },
    totals: { inputTokens: 3_100_000, outputTokens: 720_000, totalTokens: 3_820_000, sessions: 31, turns: 96, modelCalls: 412, toolCalls: 733, subAgentRuns: 6, activeDays: 19 },
    streaks: { current: 3, longest: 7, activeToday: true }, peakDay: { date: "2026-09-28", totalTokens: 520_000 }, longestTurnSeconds: 412, longestTurnDate: "2026-09-28",
    activeHour: { hour: 21, turns: 14 }, hours: Array.from({ length: 24 }, (_, hour) => (hour > 8 && hour < 23 ? 2 + (hour % 5) : 0)),
    firstActivityDate: "2026-08-12", lastActivityDate: "2026-10-07",
    models: [{ name: "MiniMax-M3", calls: 331, tokens: 3_000_000 }, { name: "Qwen3-Coder", calls: 81, tokens: 820_000 }], modelKinds: 2,
    tools: [{ name: "exec", calls: 210 }, { name: "read_workspace_text", calls: 188 }, { name: "apply_patch", calls: 96 }], toolKinds: 3,
    days: Array.from({ length: 40 }, (_, index) => { const date = new Date(Date.UTC(2026, 7, 29 + index)).toISOString().slice(0, 10); const total = (index * 37 % 11) * 40_000;
      return { date, inputTokens: Math.round(total * 0.8), outputTokens: Math.round(total * 0.2), totalTokens: total, modelCalls: total / 10_000, turns: total ? 1 + index % 4 : 0, toolCalls: total / 5_000, sessions: total ? 1 : 0 }; }) }),
  "thread/list": () => ({ threads, counts: { active: threads.length, archived: 12, all: threads.length + 12 } }),
  "project/list": () => ({ projects, unfiledThreadCount: 0 }),
  "thread/read": (args) => snapshot(String(args.threadId)),
  "thread/context": () => ({ context: null }),
  // A conversation created from the home screen (the sidebar's "New conversation", then a send): the fixture has one thread.
  "thread/start": () => ({ thread: { ...thread, status: "idle", currentTurnId: undefined } }),
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
  call: async (method: string, args: Record<string, unknown> = {}) => {
    // The Host answers a send only after its account check and the runtime's request preparation.
    if (latency && (method === "turn/start" || method === "thread/start")) await new Promise((resolve) => window.setTimeout(resolve, latency));
    return structuredClone(handlers[method]?.(args) || {});
  },
  listModels: async () => models,
  // The quota ring reads `windows[0]` and `balances.map`, so the catch-all `{}` below would crash the composer.
  queryModelUsage: async () => ({ status: "unsupported", provider: "", windows: [], balances: [], checkedAt: Date.now(), message: "" }),
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
