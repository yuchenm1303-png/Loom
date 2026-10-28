// Test-only bridge: this fixture cannot reach Host or modify a repository.
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { ProjectRunsPanel } from "../../src/components/ProjectRunsPanel";
import type { ProjectRun } from "../../src/components/projectRuns";
const base: ProjectRun = { id: "root-task", revision: 1, objective: "构建并验收离线项目管理工具", repository: "C:\\Projects\\Demo",
  sourceThreadId: "source", state: "created", control: "paused", phase: "planning", completionPolicy: "local_integrate",
  workflowControl: "paused", workflowStatus: "idle", tokensUsed: 0, tokenBudget: 100000, episodesUsed: 0, maxEpisodes: 100, plan: [] };
const params = new URL(location.href).searchParams;
localStorage.setItem("loom.settings.language", params.get("lang") || "zh-CN");
const state = { runs: params.has("empty") ? [] : [base], calls: [] as { method: string; params: Record<string, unknown> }[], failNext: false, offline: false };
Object.assign(window, { projectRunTest: state });
const receipts = new Map<string, ProjectRun>();
Object.assign(window, { loom: { async call(method: string, values: Record<string, unknown>) {
  state.calls.push({ method, params: structuredClone(values) });
  if (state.offline) throw new Error("Host disconnected");
  if (method === "thread/list") return { threads: [{ id: "source", title: "开发会话", workspace: base.repository }] };
  if (method === "projectRun/list") return { runs: structuredClone(state.runs), error: "" };
  if (method === "projectRun/get") return { run: { ...state.runs.find(item => item.id === values.runId), verificationFreshness: "current" } };
  if (method === "projectRun/assessments") return { assessments: [{ id: "evidence", verdict: "passed", scope: "project", summary: "真实执行的行为检查通过（测试数据）", checks: [{ criterionId: "task-1:1", eventId: "check-123" }], issues: [] }] };
  const key = String(values.operationId);
  if (receipts.has(key)) return { run: structuredClone(receipts.get(key)) };
  let run: ProjectRun;
  if (method === "projectRun/create") {
    run = { ...structuredClone(base), id: `created-${state.runs.length}`, objective: String(values.objective),
      tokenBudget: Number(values.tokenBudget), maxEpisodes: Number(values.maxEpisodes), completionPolicy: String(values.completionPolicy) };
    run.maxWorkers = Number(values.maxWorkers || 1);
    state.runs.push(run);
  } else {
    run = state.runs.find(item => item.id === values.runId)!;
    if (values.expectedRevision !== run.revision) throw new Error("stale project revision");
    run.revision++;
    if (method.endsWith("/start") || method.endsWith("/resume")) { run.state = "running"; run.control = "active"; run.workflowControl = "active"; run.workflowStatus = "active"; }
    if (method.endsWith("/pause")) { run.control = "paused"; run.workflowControl = "paused"; }
    if (method.endsWith("/cancel")) run.state = "cancelled";
  }
  receipts.set(key, structuredClone(run));
  if (state.failNext) { state.failNext = false; throw new Error("Response lost after server execution"); }
  return { run: structuredClone(run) };
} } });
createRoot(document.getElementById("root")!).render(<I18nProvider><ProjectRunsPanel threadId="source" onOpenThread={async id => { Object.assign(window, { openedProjectThread: id }); }} /></I18nProvider>);
