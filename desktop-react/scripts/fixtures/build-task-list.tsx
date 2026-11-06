/** A 建造 conversation with no project: the real ProjectMode, its plan card fed by the transcript's task list.
 *  /scripts/fixtures/build-task-list.html?list=1 (leave it out for a conversation without a list); nothing connects to a Host.
 */
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { ProjectMode } from "../../src/components/ProjectMode";
import { applyThemePreference } from "../../src/theme";
import type { TranscriptItem } from "../../src/types/loom";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
localStorage.setItem("loom.settings.language", "zh-CN");
applyThemePreference("dark", { animate: false, syncNative: false });
Object.assign(window, { loom: { queryModelUsage: async () => ({ status: "ok", provider: "m", checkedAt: Date.now(), balances: [], windows: [] }),
  async call(method: string) {
    if (method === "projectRun/overview") return { run: null, program: null, children: [], agents: [], events: [], workspace: { path: "C:/work/loom", branch: "main", status: "ready" } };
    throw new Error(`no ${method} here`);
  } } });

const plan = [{ step: "看整体结构", status: "completed", outcome: "passed" }, { step: "派子代理读后端和前端", status: "in_progress" },
              { step: "汇总架构要点", status: "pending" }];
const items = (params.get("list") ? [{ id: "plan", type: "task_plan", threadId: "t", turnId: "turn", result: { plan } }] : []) as unknown as TranscriptItem[];

createRoot(document.getElementById("root")!).render(
  <I18nProvider>
    <div className="project-mode-stage" style={{ height: "100vh", background: "var(--loom-workspace-canvas)", color: "var(--loom-workspace-ink)" }}>
      <ProjectMode threadId="t" workspace="C:/work/loom" model="deepseek" items={items} chatting={Boolean(params.get("list"))} />
    </div>
  </I18nProvider>,
);
