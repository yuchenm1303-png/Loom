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
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
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
];
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

// ?draft=1: nothing started yet; ?attention=1: stopped at the budget and waiting.
function variant(data: FlowData): FlowData {
  if (params.has("draft")) return { ...data, phase: "draft", objective: "", narration: { summary: "", text: "描述一个大目标，它会被拆成阶段和任务，并行开发、独立复审后集成。" },
    tasks: [], agents: [], scope: [], knowledge: { checks: 0, notes: 0, latest: "" }, controls: { pause: false, resume: false, cancel: false } };
  if (params.has("attention")) return { ...data, state: "needs_you", attention: { reason: "token 预算已用完。", action: "budget" },
    controls: { pause: false, resume: false, cancel: true }, agents: data.agents.filter((agent) => agent.done) };
  return data;
}

function Preview() {
  const frozen = params.has("at") ? Number(params.get("at")) : null;
  const [mode, setMode] = useState<"chat" | "project">(params.get("mode") === "chat" ? "chat" : "project");
  const [count, setCount] = useState(frozen ?? 0);
  const [workers, setWorkers] = useState(Number(params.get("workers") || 2));
  const [model, setModel] = useState(MODELS[0].selection);
  const [picks, setPicks] = useState(0);
  const current = MODELS.find((item) => item.selection === model)!;
  const models = { current: { ...current, reasoning: "高", vision: current.groupId !== "deepseek" },
                   options: MODELS.filter((item) => item !== current).slice(0, 3) };
  useEffect(() => {
    if (frozen != null) return;
    const timer = setInterval(() => setCount((value) => (value >= steps.length ? 0 : value + 1)), 2400);
    return () => clearInterval(timer);
  }, [frozen]);
  return (
    <div style={{ minHeight: "100vh", background: "var(--pl-surface)", color: "var(--pl-ink)" }}>
      <header style={{ display: "flex", alignItems: "center", gap: 12, height: 56, padding: "0 24px" }}>
        <span style={{ color: "var(--pl-ink)", fontSize: 13, fontWeight: 500 }}>ledger 记账工具</span>
        <span style={{ color: "var(--pl-faint)", fontSize: 12 }}>ledger</span>
        <span style={{ marginLeft: "auto" }}><ProjectModeToggle value={mode} onChange={setMode} /></span>
      </header>
      {mode === "project"
        ? <ProjectFlow data={{ ...variant(play(count, workers)), models, references: REFERENCES }}
                       actions={{ onWorkers: setWorkers, onModel: setModel, onAttach: () => {},
                                  onPickFiles: async () => { setPicks((value) => value + 1); return PICKS.slice(picks % 3, picks % 3 + 2); } }} />
        : <div style={{ display: "grid", placeItems: "center", height: "70vh", color: "var(--pl-faint)", fontSize: 13 }}>（这里是普通对话）</div>}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<Preview />);
