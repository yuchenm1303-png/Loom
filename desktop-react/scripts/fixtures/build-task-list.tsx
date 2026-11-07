/** A 建造 conversation with no project: the real ProjectMode, whose task-list branch reads the transcript.
 *  /scripts/fixtures/build-task-list.html?list=1 opens with a list; window.taskListFixture.arrive() brings it later,
 *  .step() moves it on and .uncovered() closes a step without doing it. Nothing connects to a Host.
 */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { ProjectMode } from "../../src/components/ProjectMode";
import { applyThemePreference } from "../../src/theme";
import type { TranscriptItem } from "../../src/types/loom";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
localStorage.setItem("loom.settings.language", "zh-CN");
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });
Object.assign(window, { loom: { queryModelUsage: async () => ({ status: "ok", provider: "m", checkedAt: Date.now(), balances: [], windows: [] }),
  async call(method: string) {
    if (method === "projectRun/overview") return { run: null, program: null, children: [], agents: [], events: [], workspace: { path: "C:/work/loom", branch: "main", status: "ready" } };
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
  const items = (step < 0 ? [] : [{ id: "plan", type: "task_plan", threadId: "t", turnId: "turn", result: { plan: plans[step] } }]) as unknown as TranscriptItem[];
  return (
    <div className="project-mode-stage" style={{ height: "100vh", background: "var(--loom-workspace-canvas)", color: "var(--loom-workspace-ink)" }}>
      <ProjectMode threadId="t" workspace="C:/work/loom" model="deepseek" items={items} chatting={step >= 0} />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<I18nProvider><Preview /></I18nProvider>);
