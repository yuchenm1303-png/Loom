import React, { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { RightToolDock } from "../../src/components/RightToolDock";
import { TaskProgressPanel } from "../../src/components/TaskProgressPanel";
import { I18nProvider } from "../../src/i18n";
import type { TaskMilestone } from "../../src/components/liveTaskProgress";
import "../../src/styles.css";
import "../../src/theme.css";
import "../../src/components/semantic-colors.css";
import "../../src/components/panel-language.css";
import "../../src/components/right-tool-dock.css";
import "../../src/components/task-progress-panel.css";
import "../../src/components/workspace-panels.css";

localStorage.setItem("loom.settings.language", "zh-CN");

interface FixtureState {
  width: number;
  completed: boolean;
  nextTurn: boolean;
  open: boolean;
  archived: boolean;
}

function stepsFor(completed: boolean): TaskMilestone[] {
  const running = completed ? "completed" : "in_progress";
  return [
    {
      id: "build", step: "确认浏览器版本", status: "completed", outcome: "passed",
      acceptance: ["browser_status 返回扩展版本"], evidence_refs: [{ call_id: "call-17" }],
      evidence: "browser_status 与 boot_token 均已核对",
    },
    {
      id: "pressure", step: "运行压力测试，验证长步骤在窄屏下换行并保留完整文字", status: running,
      outcome: completed ? "passed" : undefined,
      acceptance: ["内容可滚动", "标题不被截断"],
      evidence_refs: completed ? [{ path: "reports/pressure.json" }] : undefined,
      evidence_facts: completed ? [{ path: "reports/pressure.json", sha256: "a".repeat(64) }] : undefined,
    },
    {
      id: "connection", step: "补测连接", status: completed ? "completed" : "blocked",
      outcome: completed ? "not_covered" : "not_covered",
      blocker: "当前环境没有配置 CDP；记录未覆盖", acceptance: ["记录未覆盖原因"],
    },
    {
      id: "review", step: "评审协议与恢复路径", type: "review", status: "blocked", outcome: "failed",
      deps: ["build"], cycles: 3, max_review_cycles: 3,
      blocker: "评审次数耗尽，需要修复后再审", feedback: { status: "open", report: "补充故障恢复验证" },
    },
    ...Array.from({ length: 11 }, (_, index) => ({
      id: `delivery-${index + 1}`, step: `交付检查 ${index + 1}：核对产物、记录与验收引用`, status: "pending" as const,
    })),
  ];
}

let update: ((updater: (state: FixtureState) => FixtureState) => void) | null = null;

function Harness() {
  const [state, setState] = useState<FixtureState>({ width: 1200, completed: false, nextTurn: false, open: true, archived: false });
  update = setState;
  const steps = state.archived ? [] : stepsFor(state.completed);
  const completed = steps.filter(step => step.status === "completed").length;
  const noop = () => {};

  return (
    <div className="app-shell workspace-panels" style={{ width: state.width, height: 860 }}>
      <RightToolDock
        loading={false}
        hasWorkspace
        filesAvailable={false}
        browserAvailable={false}
        browserOpen={false}
        backgroundOpen={false}
        backgroundRunningCount={0}
        taskOpen={state.open}
        taskCompleted={completed}
        taskTotal={steps.length}
        taskRunning={!state.completed && steps.length > 0}
        agentsOpen={false}
        agentCount={0}
        reviewOpen={false}
        reviewCount={0}
        artifactsOpen={false}
        artifactCount={0}
        inspectorOpen={false}
        accountAuthenticated={false}
        onOpenFiles={noop}
        onToggleBrowser={noop}
        onToggleBackground={noop}
        onToggleTasks={() => setState(current => ({ ...current, open: !current.open }))}
        onToggleAgents={noop}
        onToggleReview={noop}
        onToggleArtifacts={noop}
        onToggleInspector={noop}
        onOpenProfile={noop}
        onOpenAccount={noop}
        onOpenSettings={noop}
      />
      <TaskProgressPanel steps={steps} open={state.open} onClose={() => setState(current => ({ ...current, open: false }))} />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><I18nProvider><Harness /></I18nProvider></StrictMode>,
);

const api = window as unknown as {
  renderPlan(width: number, completed?: boolean, nextTurn?: boolean): void;
  setTaskPanelOpen(open: boolean): void;
  archiveTaskPlan(): void;
};

api.renderPlan = (width, completed = false, nextTurn = false) => {
  flushSync(() => update?.(state => ({ ...state, width, completed, nextTurn, archived: false })));
};
api.setTaskPanelOpen = open => {
  flushSync(() => update?.(state => ({ ...state, open })));
};
api.archiveTaskPlan = () => {
  flushSync(() => update?.(state => ({ ...state, archived: true, open: false })));
};
