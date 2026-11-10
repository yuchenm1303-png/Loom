/** A 建造 conversation with no project: the real ProjectMode, whose task-list branch reads the transcript.
 *  /scripts/fixtures/build-task-list.html?list=1 opens with a list; window.taskListFixture.arrive() brings it later,
 *  .step() moves it on and .uncovered() closes a step without doing it. Nothing connects to a Host.
 *  ?earlier=1: the list was written for an earlier request and the person has sent a new message.
 *  ?agents=conv: the conversation's own sub-agents (a goal's check, a line) running, no project.
 */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { ProjectMode } from "../../src/components/ProjectMode";
import { applyThemePreference } from "../../src/theme";
import type { TranscriptItem } from "../../src/types/loom";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
const params2 = params;
(window as unknown as { goalCalls: unknown[] }).goalCalls = [];
localStorage.setItem("loom.settings.language", "zh-CN");
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });
Object.assign(window, { loom: { queryModelUsage: async () => ({ status: "ok", provider: "m", checkedAt: Date.now(), balances: [], windows: [] }),
  async call(method: string, params?: unknown) {
    if (method === "projectAgent/peek") {
      const id = String((params as { agentId?: string })?.agentId);
      const running = id === "chk-1";
      const iso = (ms: number) => new Date(Date.now() - ms).toISOString();
      return { id, role: id === "chk-1" ? "goal-check" : "goal-line", state: running ? "running" : "completed", executionRunning: running, finished: !running,
        outcome: running ? "" : id === "fin-2" ? "failed" : "done", startedAt: iso(running ? 180000 : 5400000), endedAt: running ? null : iso(600000), tokens: running ? 410000 : 3613960,
        error: id === "fin-2" ? "模型服务额度已耗尽，请补充额度或切换可用模型后继续。" : "",
        rows: [{ kind: "say", text: "先读 README 和 sim.py，确认信道条件" }, { kind: "tool", text: "read_workspace_text src/sweep.py", running: false, ok: true },
               { kind: "say", text: "跑 4 个条件的主扫描，每点 512 个时隙" }, { kind: "tool", text: ".venv/Scripts/python.exe src/sweep.py --presets main --batch 128", running: false, ok: true },
               { kind: "tool", text: ".venv/Scripts/python.exe src/check_figures.py", running: false, ok: false }, { kind: "say", text: "图 3 的坐标轴单位缺失，补上后重跑" },
               { kind: "tool", text: "apply_patch src/make_figures.py", running, ok: running ? null : true }],
        result: running ? "" : id === "fin-2" ? "" : "## 报告\n跑完 4 个信道条件，吞吐增益 +6.1%（95% CI 3.2–9.0）。\n文件：results/sweep_main.json", observedAt: Date.now() / 1000 };
    }
    if (method === "projectAgent/history") {
      const iso = (ms: number) => new Date(Date.now() - ms).toISOString();
      const agents = [
        { id: "chk-1", parentId: "t", role: "goal-check", task: "独立核验", state: "running", outcome: "", finished: false, startedAt: iso(180000), endedAt: null, tokens: 410000, result: "", error: "" },
        { id: "fin-1", parentId: "t", role: "goal-line", task: "正式实验：P3 链路自适应", state: "completed", outcome: "done", finished: true, startedAt: iso(5400000), endedAt: iso(600000), tokens: 3613960, result: "## 报告\n跑完 4 个信道条件，吞吐增益 +6.1%", error: "" },
        { id: "fin-2", parentId: "t", role: "goal-line", task: "链路自适应线", state: "failed", outcome: "failed", finished: true, startedAt: iso(7200000), endedAt: iso(1800000), tokens: 5716618, result: "", error: "模型服务额度已耗尽" },
        { id: "old-1", parentId: "t", role: "investigator", task: "找出 MiniMax 窗口声明的位置", state: "completed", outcome: "done", finished: true, startedAt: iso(90000000), endedAt: iso(89900000), tokens: 120000, result: "窗口只在 loom_model_bridge.py 里声明。", error: "" },
        { id: "old-2", parentId: "t", role: "goal-line", task: "第一阶段：扫描与作图", state: "completed", outcome: "done", finished: true, startedAt: iso(180000000), endedAt: iso(176000000), tokens: 2400000, result: "五张图已生成。", error: "" }];
      const offset = Number((params as { offset?: number })?.offset || 0);
      return { agents: agents.slice(offset, offset + 3), total: agents.length, observedAt: Date.now() / 1000 };
    }
    if (method === "projectWeave/goal") { (window as unknown as { goalCalls: unknown[] }).goalCalls.push(params); return {}; }
    if (method === "projectRun/overview") return { goal: params2.get("goal") ? { objective: "研究 AI-RAN", status: "active", phase: "working", tokens: 1200000, tokenBudget: 8000000, turns: 3, reason: "", summary: "", checked: false,
      ...(params2.get("goal") === "settled" ? { lines: [{"title":"正式实验：P3 链路自适应","state":"done","check":"passed","tokens":3613960,"report":"跑完 4 个信道条件，每点 512 个时隙。\n吞吐增益 +6.1%（95% CI 3.2–9.0），检查通过。","changed":["src/sweep.py","src/make_figures.py","results/sweep_main.json","results/figures/paper/bler_main.pdf"]},{"title":"独立数学与文献审计","state":"done","check":"failed","tokens":699390,"report":"推导 (7) 的符号与引用的定理不一致；两篇相关工作的结论被引反。","changed":["paper/manuscript.md"]},{"title":"链路自适应线","state":"failed","check":"","tokens":5716618,"why":"额度耗尽"}] } : {}),
      ...(params2.get("goal") === "agents" ? { agents: 2 } : params2.get("goal") === "deepen" ? { deepens: 1, hours: 8 } : params2.get("goal") === "retry" ? { retryAt: Date.now() / 1000 + 600 }
        : params2.get("goal") === "charter" ? { reviews: [{ round: 1, verdict: "passed", summary: "", major: 0, minor: 1 }], charter: {
            deliverable: "一份可以给审稿人评判的研究稿：问题与动机、相关工作与新意、方法、与公平基线的对比实验、局限，附复现代码", kind: "research",
            phases: ["文献与新意", "方向试点", "主实验", "写作与自审", "Revision round 1"], current: 4,
            criteria: [{ id: "C1", text: "相关工作对比至少覆盖五篇近年论文，并说明新意在哪里", status: "met", origin: "charter" },
                       { id: "C2", text: "主结果与两个公平基线对比，给出置信区间", status: "unmet", origin: "charter" },
                       { id: "C3", text: "在第三个信道模型下验证结论是否稳健", status: "open", origin: "review 1" },
                       { id: "C4", text: "一条命令复现文中所有数字", status: "dropped", origin: "charter" }] } } : {}) } : null, run: null, program: null, children: [], agents: [], events: [],
      ...(params2.get("agents") === "finished" ? { conversationAgents: [{ id: "chk-1", parentId: "t", role: "goal-check", state: "running", executionRunning: true, queueDepth: 0, activity: "", step: 4, startedAt: new Date(Date.now() - 180000).toISOString() },{ id: "fin-1", parentId: "t", role: "goal-line", state: "completed", executionRunning: false, queueDepth: 0, activity: "", step: 0, finished: true, outcome: "done", startedAt: new Date(Date.now() - 5400000).toISOString(), endedAt: new Date(Date.now() - 600000).toISOString(), tokens: 3613960, result: "## 报告\n跑完 4 个信道条件，吞吐增益 +6.1%（95% CI 3.2–9.0）。\n文件：results/sweep_main.json" },{ id: "fin-2", parentId: "t", role: "goal-line", state: "failed", executionRunning: false, queueDepth: 0, activity: "", step: 0, finished: true, outcome: "failed", startedAt: new Date(Date.now() - 7200000).toISOString(), endedAt: new Date(Date.now() - 1800000).toISOString(), tokens: 5716618, result: "", error: "模型服务额度已耗尽，请补充额度或切换可用模型后继续。" },{ id: "fin-3", parentId: "t", role: "investigator", state: "completed", executionRunning: false, queueDepth: 0, activity: "", step: 0, finished: true, outcome: "done", startedAt: new Date(Date.now() - 900000).toISOString(), endedAt: new Date(Date.now() - 840000).toISOString(), tokens: 120000, result: "工作区里没有找到 MiniMax 的窗口声明。" }] } : {}),
      ...(params2.get("agents") === "conv" ? { conversationAgents: [
        { id: "chk-1", parentId: "t", role: "goal-check", state: "running", executionRunning: true, queueDepth: 0, activity: "", step: 4, startedAt: new Date(Date.now() - 180000).toISOString() },
        { id: "line-1", parentId: "t", role: "goal-line", state: "completed", executionRunning: false, queueDepth: 0, activity: "", step: 12 }] } : {}), workspace: { path: "C:/work/loom", branch: "main", status: "ready" } };
    throw new Error(`no ${method} here`);
  } } });

const plans = [
  [{ step: "看整体结构", status: "completed", outcome: "passed" }, { step: "派子代理读后端和前端", status: "in_progress" }, { step: "汇总架构要点", status: "pending" }],
  [{ step: "看整体结构", status: "completed", outcome: "passed" }, { step: "派子代理读后端和前端", status: "completed", outcome: "passed" }, { step: "汇总架构要点", status: "in_progress" }],
  [{ step: "看整体结构", status: "completed", outcome: "passed" }, { step: "派子代理读后端和前端", status: "completed", outcome: "not_covered" }, { step: "汇总架构要点", status: "completed", outcome: "passed" }],
];

function Preview() {
  const [step, setStep] = useState(params.get("list") ? 0 : -1);
  Object.assign(window, { taskListFixture: { arrive: () => setStep(0), step: () => setStep(1), uncovered: () => setStep(2) } });
  const earlier = params.get("earlier") === "1";
  const items = (step < 0 ? [] : [
    { id: "ask-1", type: "user_message", threadId: "t", turnId: "turn", source: "user", text: "修文档" },
    { id: "plan", type: "task_plan", threadId: "t", turnId: "turn", result: { plan: plans[step], request: "修文档", request_turn_id: "turn" } },
    ...(earlier ? [{ id: "ask-2", type: "user_message", threadId: "t", turnId: "turn-2", source: "user", text: "总结一下" }] : []),
  ]) as unknown as TranscriptItem[];
  return (
    <div className="project-mode-stage" style={{ height: "100vh", background: "var(--loom-workspace-canvas)", color: "var(--loom-workspace-ink)" }}>
      <ProjectMode threadId="t" workspace="C:/work/loom" model="deepseek" items={items} chatting={step >= 0} />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<I18nProvider><Preview /></I18nProvider>);
