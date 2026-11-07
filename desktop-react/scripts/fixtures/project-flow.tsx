/** Persistent-project mode preview: the real ProjectFlow and toggle with production styles.
 *  /scripts/fixtures/project-flow.html?theme=dark|light
 *    &at=N     freeze the simulation at step N for screenshots; default plays one event every 2.4 s
 *    &mode=chat  start with the switch on "对话"
 *  The data follows a real qualification run (chaos-ledger1, 2026-10-29); nothing connects to a Host.
 */
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectFlow, type FlowAgent, type FlowData, type ModelOption } from "../../src/components/ProjectFlow";
import { ProjectModeToggle } from "../../src/components/ProjectModeToggle";
import { toConversation } from "../../src/components/projectOverview";
import type { TranscriptItem } from "../../src/types/loom";
import { applyThemePreference } from "../../src/theme";
import { I18nProvider } from "../../src/i18n";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
// The quota ring beside the model chip asks for usage; answer as a plan with a 5-hour and a weekly window.
const loomBridge = ((window as unknown as { loom?: Record<string, unknown> }).loom ??= {});
loomBridge.queryModelUsage ??= async (selection: string) => ({
  status: "ok", provider: selection.split(":")[0] || selection, checkedAt: Date.now(), balances: [],
  windows: [{ label: "5h", remainingPercent: 64, resetsAt: Date.now() + 2.4 * 3_600_000 }, { label: "Weekly", remainingPercent: 87, resetsAt: Date.now() + 4 * 86_400_000 }],
});
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });

const start: FlowData = {
  phase: "live",
  objective: "做一个只用标准库的 Python 记账汇总工具：解析 CSV，按账户精确汇总，非法行报行号并以 2 退出，附测试和说明。",
  narration: { summary: "已工作 41 分钟 · 11 轮", text: "前三个任务已复审并集成。测试套件和 README 可以并行，两个开发 agent 正在做，复审会在它们提交后接手。" },
  milestone: 1,
  state: "running",
  model: "deepseek-v4.1-flash",
  repository: { name: "ledger", commit: "0a1d215", subject: "Integrate task-3 CLI", branch: "main" },
  scope: [{ path: "ledger/", count: 3 }, { path: "tests/", count: 1 }, { path: "README.md", count: 1 }],
  knowledge: { checks: 11, notes: 38, latest: "调用 python 用绝对路径，不要经 cmd /c。" },
  tasks: [
    { id: "task-1", title: "解析器与逐行校验", state: "integrated" },
    { id: "task-2", title: "按账户汇总（Decimal）", state: "integrated" },
    { id: "task-3", title: "命令行 python -m ledger", state: "integrated" },
    { id: "task-4", title: "unittest 测试套件", state: "working" },
    { id: "task-5", title: "README 与使用说明", state: "working" },
  ],
  budget: { tokens: 2_310_000, tokenLimit: 8_000_000 },
  workers: 2,
  maxWorkers: 2,
  controls: { pause: true, resume: false, cancel: true },
  agents: [
    { id: "planner", role: "planner", name: "Agent 1", activity: "阶段 1：5 个任务", step: 1, elapsed: "1 分", done: true },
    { id: "w1", role: "worker", name: "Agent 2", activity: "编辑 tests/test_cli.py", step: 7, elapsed: "5 分" },
    { id: "w2", role: "worker", name: "Agent 3", activity: "核对 README 中的命令", step: 4, elapsed: "3 分" },
  ],
};

// The milestone's clock, minutes since it started: a planner, two workers in parallel, a review
// rejected once (task-2 reworked), the last two tasks still running at minute 41.
const woven: [string, "plan" | "work" | "review", number, number | null, boolean?][] = [
  ["plan", "plan", 0, 2], ["task:task-1", "work", 2, 9], ["task:task-1", "review", 9, 12],
  ["task:task-2", "work", 2, 11], ["task:task-2", "review", 12, 14, true], ["task:task-2", "work", 14, 18], ["task:task-2", "review", 18, 20],
  ["task:task-3", "work", 12, 21], ["task:task-3", "review", 21, 24], ["task:task-4", "work", 24, null], ["task:task-5", "work", 25, 37],
  ["task:task-5", "review", 37, null],
];
function timeline(minute: number): FlowData["timeline"] {
  const lanes = [{ key: "plan", label: "规划" }, ...start.tasks.map((item, index) => ({ key: `task:${item.id}`, label: `任务 ${index + 1}`, taskId: item.id }))];
  const spans = woven.map(([lane, phase, from, to, trouble], index) => ({ id: `s${index}`, lane, phase, from: from / minute,
    to: (to ?? minute) / minute, live: to == null, trouble: !!trouble, tokens: Math.round(((to ?? minute) - from) * 52_000), minutes: (to ?? minute) - from }));
  return { lanes, spans, live: true, ticks: [10, 20, 30, 40].filter((at) => at < minute).map((at) => ({ at: at / minute, label: `${at}m` })),
           stats: { episodes: spans.length, trouble: 1, perHour: 3_380_000 } };
}

// The model card's choices, as the app's snapshot gives them (ids pick the provider marks).
const MODELS: ModelOption[] = [
  { selection: "opencode-go:deepseek-v4.1-flash", name: "deepseek-v4.1-flash", group: "OpenCode Go", groupId: "opencode-go" },
  { selection: "deepseek:deepseek-v4.1", name: "deepseek-v4.1", group: "DeepSeek", groupId: "deepseek" },
  { selection: "minimax:MiniMax-M3", name: "MiniMax-M3", group: "MiniMax", groupId: "minimax" },
  { selection: "kimi:kimi-k3", name: "kimi-k3", group: "Kimi", groupId: "kimi" },
  // ?many=1: a long catalogue, so the card searches and its list scrolls.
  ...(params.has("many") ? ["qwen3.6-plus", "glm-5.1", "kimi-k3-turbo", "MiniMax-M3-fast", "gpt-oss-240b", "deepseek-v4.1-pro", "mimo-v3", "ling-2"]
    .map((name) => ({ selection: `opencode-go:${name}`, name, group: "OpenCode Go", groupId: "opencode-go" })) : []),
];
const LEVELS = ["low", "medium", "high", "xhigh"].map((value) => ({ value, label: value, description: "", advanced: value === "xhigh" }));
const REFERENCES = [{ name: "ledger-spec.md", bytes: 18_400 }, { name: "sample-2026-01.csv", bytes: 4_210 }, { name: "budgets.json", bytes: 612 }];
// Pretend file dialog: ?files=1 starts the draft with picks.
const PICKS = ["C:/Users/me/Desktop/ledger-spec.md", "C:/Users/me/Desktop/sample-2026-01.csv", "C:/Users/me/Desktop/screens/flow.png"];

type Step = (data: FlowData) => FlowData;
const agent = (d: FlowData, id: string, change: Partial<FlowAgent>) => ({ ...d, agents: d.agents.map((a) => a.id === id ? { ...a, ...change } : a) });
const task = (d: FlowData, id: string, state: FlowData["tasks"][number]["state"]) => ({ ...d, tasks: d.tasks.map((t) => t.id === id ? { ...t, state } : t) });
const spend = (d: FlowData, tokens: number) => ({ ...d, budget: { ...d.budget, tokens: d.budget.tokens + tokens } });

// Each step is one thing that happens in a real run.
const steps: Step[] = [
  (d) => spend(agent(d, "w1", { activity: "运行 python -m unittest", step: 8 }), 60_000),
  (d) => spend(agent(d, "w2", { activity: "写入退出码一节", step: 5 }), 40_000),
  (d) => ({ ...spend(agent(task(d, "task-5", "review"), "w2", { id: "rv", role: "reviewer", activity: "逐条执行文档命令", step: 1, elapsed: "刚刚" }), 90_000),
    narration: { summary: "已工作 44 分钟 · 12 轮", text: "README 已提交候选，复审在独立检出里逐条执行文档中的命令。" } }),
  (d) => spend(agent(d, "w1", { activity: "补充行号回归测试", step: 9 }), 50_000),
  (d) => spend(agent(d, "rv", { activity: "检查 7 条命令的输出", step: 2 }), 70_000),
  (d) => ({ ...task(d, "task-5", "integrated"), agents: d.agents.filter((a) => a.id !== "rv"),
    repository: { ...d.repository, commit: "7c41e09", subject: "Integrate task-5 README" },
    narration: { summary: "已工作 47 分钟 · 13 轮", text: "README 复审通过并已集成。只剩测试套件，完成后进入整体复审。" } }),
  (d) => spend(agent(d, "w1", { activity: "运行 python -m unittest", step: 10 }), 40_000),
];

function play(count: number, workers: number) {
  const data = steps.slice(0, count).reduce((value, step) => step(value), start);
  // The stepper adds an idle agent slot or removes the last idle one.
  const extra: FlowAgent[] = Array.from({ length: Math.max(0, workers - 2) }, (_, i) =>
    ({ id: `extra-${i}`, role: "worker", name: `Agent ${data.agents.length + 1 + i}`, activity: "等待可并行的任务", step: 0, elapsed: "", done: true }));
  return { ...data, workers, agents: [...data.agents, ...extra], timeline: timeline(41 + count) };
}

// &chat=1: the conversation above the hub (Weave is chat first); &chatting=1: the agent is answering now.
const CHAT: FlowData["conversation"] = [
  { id: "u1", role: "user", text: "你好" },
  { id: "a1", role: "assistant", text: "你好！在建造模式里可以直接聊天，也可以交给我一个要持续好几天的大目标。想做点什么？" },
  { id: "u2", role: "user", text: "帮我把 ledger 做成一个能导入银行流水、按月出报表的记账工具，要有测试" },
  { id: "a2", role: "assistant", text: "明白。我先看了仓库：已有 `ledger/parser.py` 和基础 CLI。我把目标整理成了下面的提案，**确认后点「开始建造」**。" },
];
const PROPOSAL = { id: "p1", title: "ledger 记账工具", tokenBudget: 4_000_000, maxWorkers: 2,
  objective: "把 ledger 做成完整的记账工具：\n- 导入 CSV 银行流水（招行、工行两种格式），去重\n- 按月汇总收支，输出 Markdown 报表\n"
    + "- CLI：`ledger import`、`ledger report --month 2026-10`\n完成标准：pytest 全部通过，两个样例流水导入后报表数字与手算一致。" };
function talk(data: FlowData): FlowData {
  if (!params.has("chat")) return data;
  return { ...data, conversation: CHAT, chatting: params.has("chatting"), activity: params.has("chatting") ? "读取 ledger/parser.py" : "" };
}

// &turn=1: the same conversation as the Host's transcript items, through the real mapping (toConversation): the
// agent read the repository before it answered. &live=1 adds a turn that plays: it thinks, runs steps, then writes
// its answer (&hold=N stops at scene N); &ask=1 ends that turn on an approval instead.
const T = "t1";
const at = (s: number) => new Date(Date.UTC(2026, 10, 1, 9, 0, s)).toISOString();
const HISTORY: TranscriptItem[] = [
  { id: "u1", threadId: T, turnId: "a", type: "user_message", text: "你好" },
  { id: "a1", threadId: T, turnId: "a", type: "assistant_message", text: "你好！在建造模式里可以直接聊天，也可以交给我一个要持续好几天的大目标。想做点什么？" },
  { id: "u2", threadId: T, turnId: "b", type: "user_message", text: "帮我把 ledger 做成一个能导入银行流水、按月出报表的记账工具，要有测试" },
  { id: "b-c1", threadId: T, turnId: "b", type: "assistant_message", phase: "commentary", text: "先看看仓库里已有什么" },
  { id: "b-r1", threadId: T, turnId: "b", type: "tool_call", toolName: "read_workspace_text", status: "completed", arguments: { path: "ledger/parser.py" }, createdAt: at(1), updatedAt: at(2) },
  { id: "b-r2", threadId: T, turnId: "b", type: "tool_call", toolName: "read_workspace_text", status: "completed", arguments: { path: "ledger/cli.py" }, createdAt: at(2), updatedAt: at(3) },
  { id: "b-p1", threadId: T, turnId: "b", type: "process", status: "completed", argv: ["git", "log", "--oneline", "-5"], stdout: "0a1d215 Integrate task-3 CLI", createdAt: at(3), updatedAt: at(4) },
  { id: "a2", threadId: T, turnId: "b", type: "assistant_message", text: "明白。我先看了仓库：已有 `ledger/parser.py` 和基础 CLI。我把目标整理成了下面的提案，**确认后点「开始建造」**。" },
];
const ANSWER = "测试现在**能过**：`pytest -q` 共 14 项全部通过，用时 0.8 秒。\n\n不过有两处值得注意：\n\n1. `parser.py` 遇到空行会直接跳过，没有计入行号，报错时行号会偏一行。\n2. 汇总用的是 `float`，金额多了会有分位误差，建议换成 `Decimal`。\n\n要我把这两处也写进提案吗？";
function liveTurn(scene: number): { items: TranscriptItem[]; chatting: boolean } {
  const turn = "c";
  const items: TranscriptItem[] = [{ id: "u3", threadId: T, turnId: turn, type: "user_message", text: "跑一下测试，看看现在能不能过" }];
  const step = (id: string, done: boolean, extra: Partial<TranscriptItem>) =>
    items.push({ id, threadId: T, turnId: turn, status: done ? "completed" : "running", createdAt: done ? at(10) : undefined, updatedAt: done ? at(12) : undefined, ...extra } as TranscriptItem);
  if (scene >= 1) items.push({ id: "c-c1", threadId: T, turnId: turn, type: "assistant_message", phase: "commentary", text: "先找到测试入口，再跑一遍" });
  if (scene >= 2) step("c-s1", scene >= 3, { type: "tool_call", toolName: "search_workspace", arguments: { query: "def test_", path: "tests" } });
  if (scene >= 3) step("c-p1", scene >= 5, { type: "process", argv: ["python", "-m", "pytest", "-q"], stdout: scene >= 4 ? "..............                       [100%]\n14 passed in 0.81s" : "collecting ..." });
  if (scene >= 5 && params.has("ask")) {
    items.push({ id: "c-ap", threadId: T, turnId: turn, type: "approval", status: "pending", toolName: "exec", reason: "pip install -e . 需要联网",
                 availableDecisions: ["accept", "decline"] } as TranscriptItem);
    return { items, chatting: true };
  }
  if (scene >= 6) step("c-r1", scene >= 7, { type: "tool_call", toolName: "read_workspace_text", arguments: { path: "ledger/parser.py" } });
  if (scene >= 8) {
    // The answer arrives a few characters at a time, as a stream does.
    const shown = Math.min(ANSWER.length, (scene - 8) * 9);
    items.push({ id: "c-a", threadId: T, turnId: turn, type: "assistant_message", text: ANSWER.slice(0, shown) });
    return { items, chatting: shown < ANSWER.length };
  }
  return { items, chatting: true };
}
const SCENES = 8 + Math.ceil(ANSWER.length / 9) + 1;
function useScene(): number {
  const hold = params.has("hold") ? Number(params.get("hold")) : null;
  const [scene, setScene] = useState(hold ?? 0);
  useEffect(() => {
    if (hold != null || !params.has("live")) return;
    // A step takes about a second; the answer streams nine characters a frame of 60 ms.
    const timer = setTimeout(() => setScene((value) => (value >= SCENES + 6 ? 0 : value + 1)), scene < 8 ? 1100 : 60);
    return () => clearTimeout(timer);
  }, [scene, hold]);
  return scene;
}
// &stopped=1: two turns the person stopped mid-work, as a real 建造 conversation had them: the agent narrated every step,
// its steps failed (a sandbox that never started a process), and neither turn reached an answer.
function stoppedTurns(): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  const add = (turn: string, id: string, extra: Partial<TranscriptItem>, s = 0) =>
    items.push({ id, threadId: T, turnId: turn, createdAt: at(20 + s), updatedAt: at(22 + s), ...extra } as TranscriptItem);
  const timedOut = (turn: string, id: string, argv: string[], s: number) =>
    add(turn, id, { type: "process", status: "failed", argv, stdout: "", stderr: "process timed out", exitCode: 1 }, s);
  add("s1", "s1-u", { type: "user_message", text: "你可以帮我清理c盘吗" });
  add("s1", "s1-c1", { type: "assistant_message", phase: "commentary", text: "我先只读地查看一下 C 盘空间和常见可清理目录的大小，再给你清理方案（删东西前会先跟你确认）。" });
  add("s1", "s1-f", { type: "file_edit", status: "completed", path: "scan.ps1", diff: "+Get-PSDrive C" }, 1);
  timedOut("s1", "s1-p", ["powershell.exe", "-NoProfile", "-File", "scan.ps1"], 2);
  add("s1", "s1-e", { type: "error", text: "turn_cancelled", error: "turn_cancelled" }, 30);
  add("s2", "s2-u", { type: "user_message", text: "请继续吧" }, 31);
  add("s2", "s2-c1", { type: "assistant_message", phase: "commentary", text: "上次的全盘递归统计太慢被中断了，我改用轻量方式：先看磁盘剩余空间，再单独统计几个常见缓存目录。" }, 31);
  timedOut("s2", "s2-p1", ["powershell.exe", "-NoProfile", "-Command", "Get-PSDrive C"], 32);
  add("s2", "s2-c2", { type: "assistant_message", phase: "commentary", text: "PowerShell 在沙箱里启动就超时了，我换成 cmd 自带命令试试。" }, 40);
  timedOut("s2", "s2-p2", ["cmd", "/c", "fsutil volume diskfree C:"], 41);
  add("s2", "s2-c3", { type: "assistant_message", phase: "commentary", text: "连 `cmd /c echo ok` 都在 20 秒内无输出，确认是 exec 通道在这个沙箱下无法启动进程，和命令本身无关。我试一下 PTY 通道，同时看看桌面操作工具是否可用。" }, 50);
  timedOut("s2", "s2-p3", ["cmd", "/c", "echo ok"], 51);
  timedOut("s2", "s2-p4", ["cmd.exe"], 60);
  add("s2", "s2-k1", { type: "tool_call", toolName: "computer_status", status: "completed", arguments: {} }, 70);
  add("s2", "s2-k2", { type: "tool_call", toolName: "computer_screenshot", status: "completed", arguments: {} }, 71);
  add("s2", "s2-c4", { type: "assistant_message", phase: "commentary", text: "确认了：沙箱里的进程创建被完全卡住（连 `echo` 都不返回），所以我没法用命令行读磁盘数据或清理。剩下的路只有桌面操作。我先试着切到「此电脑」窗口（只读操作），看桌面通道是否可用。" }, 80);
  add("s2", "s2-k3", { type: "tool_call", toolName: "computer_action", status: "failed", arguments: { action: "key", text: "win+e" } }, 81);
  add("s2", "s2-e", { type: "error", text: "turn_cancelled", error: "turn_cancelled" }, 90);
  return items;
}
function turnTalk(data: FlowData, scene: number): FlowData {
  if (!params.has("turn")) return data;
  const live = params.has("live") || params.has("hold") ? liveTurn(scene) : { items: [], chatting: false };
  const history = params.has("stopped") ? [HISTORY[0], HISTORY[1], ...stoppedTurns()] : HISTORY;
  const { messages, activity } = toConversation([...history, ...live.items], live.chatting);
  return { ...data, conversation: messages, chatting: live.chatting, activity };
}

// ?draft=1: nothing started yet (&proposal=1: the agent proposed a project); ?attention=1: stopped at the budget and waiting.
function variant(data: FlowData): FlowData {
  // &full=1: the run was started with full access.
  return talk({ ...variantOf(data), fullAccess: params.has("full") });
}
function variantOf(data: FlowData): FlowData {
  if (params.has("draft")) return { ...data, phase: "draft", objective: "", narration: { summary: "", text: "描述一个大目标，它会被拆成阶段和任务，并行开发、独立复审后集成。" },
    tasks: [], agents: [], scope: [], timeline: undefined, knowledge: { checks: 0, notes: 0, latest: "" }, controls: { pause: false, resume: false, cancel: false },
    // &dirty=1: the repository has uncommitted changes, shown as a reminder; the start still goes.
    workspace: params.has("dirty") ? { changeCount: 11, head: "0a1d215", branch: "main",
      changes: [{ status: "M", path: "src/relay/server.py" }, { status: "M", path: "src/relay/client.py" }, { status: "A", path: "src/relay/auth.py" },
              { status: "D", path: "docs/old-setup.md" }, { status: "??", path: "scratch/" }, { status: "M", path: "README.md" },
              { status: "M", path: "pyproject.toml" }, { status: "??", path: "notes 笔记.txt" }, { status: "M", path: "tests/test_relay.py" }] } : undefined,
    proposal: params.has("proposal") ? PROPOSAL : undefined };
  if (params.has("attention")) return { ...data, state: "needs_you", attention: { reason: "token 预算已用完。", action: "budget" },
    controls: { pause: false, resume: false, cancel: true }, agents: data.agents.filter((agent) => agent.done) };
  // &noplan=1: the milestone has started but its plan isn't made yet (the plan card shows its empty line).
  if (params.has("noplan")) return { ...data, tasks: [] };
  return data;
}

function Preview() {
  const [conversationAccess, setConversationAccess] = useState<string | undefined>(params.get("access") || undefined);
  const frozen = params.has("at") ? Number(params.get("at")) : null;
  const [mode, setMode] = useState<"chat" | "project">(params.get("mode") === "chat" ? "chat" : "project");
  const [count, setCount] = useState(frozen ?? 0);
  const scene = useScene();
  const [workers, setWorkers] = useState(Number(params.get("workers") || 2));
  const [model, setModel] = useState(MODELS[0].selection);
  const [picks, setPicks] = useState(0);
  const [draftProposal, setDraftProposal] = useState<typeof PROPOSAL | null>(null);
  const [activated, setActivated] = useState(false);
  useEffect(() => { Object.assign(window, { projectControlsFixture: { propose: () => setDraftProposal({ ...PROPOSAL, id: crypto.randomUUID() }),
    activate: () => setActivated(true), clear: () => setActivated(false) } }); }, []);
  const current = MODELS.find((item) => item.selection === model)!;
  const [effort, setEffort] = useState("high");
  const [recent, setRecent] = useState<string[]>([MODELS[2].selection]);
  const groups = [...new Set(MODELS.map((item) => item.groupId))]
    .map((id) => ({ id, name: MODELS.find((item) => item.groupId === id)!.group, options: MODELS.filter((item) => item.groupId === id) }));
  const models = { current: { ...current, vision: current.groupId !== "deepseek",
                              reasoning: { kind: "openai-effort", value: effort, defaultValue: "medium", source: "", options: LEVELS } },
                   recent: recent.filter((item) => item !== model).map((item) => MODELS.find((option) => option.selection === item)!), groups };
  const switchModel = (selection: string) => setTimeout(() => { setRecent((list) => [model, ...list.filter((item) => item !== model)].slice(0, 4)); setModel(selection); }, 500);
  useEffect(() => {
    if (frozen != null) return;
    const timer = setInterval(() => setCount((value) => (value >= steps.length ? 0 : value + 1)), 2400);
    return () => clearInterval(timer);
  }, [frozen]);
  return (
    // The app's shape: a header row, then the stage takes exactly what is left of the window.
    <div style={{ display: "grid", gridTemplateRows: "56px minmax(0, 1fr)", height: "100vh", background: "var(--pl-surface)", color: "var(--pl-ink)" }}>
      <header style={{ display: "flex", alignItems: "center", gap: 12, height: 56, padding: "0 24px" }}>
        <span style={{ color: "var(--pl-ink)", fontSize: 13, fontWeight: 500 }}>ledger 记账工具</span>
        <span style={{ color: "var(--pl-faint)", fontSize: 12 }}>ledger</span>
        <span style={{ marginLeft: "auto" }}><ProjectModeToggle value={mode} onChange={setMode} /></span>
      </header>
      {mode === "project"
        ? <div className="project-mode-stage" style={{ gridRow: "2" }}>
            <ProjectFlow data={{ ...turnTalk(activated ? play(count, workers) : variant(play(count, workers)), scene), ...(draftProposal ? { proposal: draftProposal } : {}), models, references: REFERENCES, history: THREADS, conversationPermissionMode: conversationAccess }}
                         actions={{ onChat: (...args) => { (window as unknown as { sent: unknown }).sent = args; }, onInterrupt: () => {}, onApproval: () => {}, onStart: (...args) => { (window as unknown as { started: unknown }).started = args; }, onDismiss: () => {}, onWorkers: setWorkers, onModel: switchModel, onReasoning: (_kind, value) => setEffort(value), onAttach: () => {}, onOpenThread: (id) => { (window as unknown as { opened: unknown }).opened = id; }, onNewThread: () => {},
                                    onPermissionModeChange: conversationAccess ? async value => setConversationAccess(value) : undefined,
                                    error: params.has("error") ? "Lost the Host; reconnecting…" : undefined,
                                    onPickFiles: async () => { setPicks((value) => value + 1); return PICKS.slice(picks % 3, picks % 3 + 2); } }} />
          </div>
        : <div style={{ display: "grid", placeItems: "center", height: "70vh", color: "var(--pl-faint)", fontSize: 13 }}>（这里是普通对话）</div>}
    </div>
  );
}

// ?history=0: no other 建造 conversations yet.
const THREADS = params.get("history") === "0" ? [] : [
  { id: "t1", title: "账本解析和导入", when: "刚刚", current: true, live: false },
  { id: "t2", title: "电力报价系统第二阶段", when: "12 分钟", current: false, live: true },
  { id: "t3", title: "把设置页拆成独立模块，并补齐所有交互测试", when: "3 小时", current: false, live: false },
  { id: "t4", title: "Loom 贴纸商店", when: "2 天", current: false, live: false },
  { id: "t5", title: "termrelay 服务器迁移", when: "10月28日", current: false, live: false },
];

createRoot(document.getElementById("root")!).render(<I18nProvider><Preview /></I18nProvider>);
