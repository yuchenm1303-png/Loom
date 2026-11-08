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
    if (method === "projectWeave/goal") { (window as unknown as { goalCalls: unknown[] }).goalCalls.push(params); return {}; }
    if (method === "projectRun/overview") return { goal: params2.get("goal") ? { objective: "研究 AI-RAN", status: "active", phase: "working", tokens: 1200000, tokenBudget: 8000000, turns: 3, reason: "", summary: "", checked: false,
      ...(params2.get("goal") === "agents" ? { agents: 2 } : params2.get("goal") === "deepen" ? { deepens: 1, hours: 8 } : params2.get("goal") === "retry" ? { retryAt: Date.now() / 1000 + 600 }
        : params2.get("goal") === "charter" ? { reviews: [{ round: 1, verdict: "passed", summary: "", major: 0, minor: 1 }], charter: {
            deliverable: "一份可以给审稿人评判的研究稿：问题与动机、相关工作与新意、方法、与公平基线的对比实验、局限，附复现代码", kind: "research",
            phases: ["文献与新意", "方向试点", "主实验", "写作与自审", "Revision round 1"], current: 4,
            criteria: [{ id: "C1", text: "相关工作对比至少覆盖五篇近年论文，并说明新意在哪里", status: "met", origin: "charter" },
                       { id: "C2", text: "主结果与两个公平基线对比，给出置信区间", status: "unmet", origin: "charter" },
                       { id: "C3", text: "在第三个信道模型下验证结论是否稳健", status: "open", origin: "review 1" },
                       { id: "C4", text: "一条命令复现文中所有数字", status: "dropped", origin: "charter" }] } } : {}) } : null, run: null, program: null, children: [], agents: [], events: [],
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
