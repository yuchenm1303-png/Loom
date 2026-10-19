import { Activity, Check, CheckCircle2, ChevronRight, CircleAlert, ListChecks, Sparkles } from "./icons";
import { useEffect, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { useLoomLanguage } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { RETAIN } from "../motion/tokens";
import { PanelHeader } from "./PanelHeader";
import { milestoneEvidenceLabels, type TaskMilestone } from "./liveTaskProgress";
import "./task-progress-panel.css";

interface TaskProgressPanelProps {
  steps: TaskMilestone[];
  open: boolean;
  onClose(): void;
}

const COPY = {
  "zh-CN": {
    title: "任务进度",
    close: "关闭任务进度",
    current: "当前步骤",
    allDone: "所有计划步骤已完成",
    steps: "计划步骤",
    overall: "整体进度",
    completedLabel: (done: number, total: number) => `${done}/${total} 已完成`,
    passedLabel: (passed: number, total: number) => `${passed}/${total} 验收通过`,
    completed: "已完成",
    inProgress: "进行中",
    blocked: "受阻",
    pending: "待处理",
    passed: "验收通过",
    failed: "验收失败",
    interrupted: "测试中断",
    notCovered: "未覆盖",
    notAssessed: "未验收",
    review: (cycles: number, max: number) => `评审 · ${cycles}/${max} 次`,
    dependency: (deps: string) => `依赖：${deps}`,
    acceptance: "验收条件",
    evidence: "证据记录",
    feedback: "待返工",
    sync: "来自计划更新 · 实时同步",
    blockedFact: (count: number) => `受阻 ${count}`,
    failedFact: (count: number) => `异常 ${count}`,
    pendingFact: (count: number) => `待处理 ${count}`,
  },
  en: {
    title: "Task progress",
    close: "Close task progress",
    current: "Current step",
    allDone: "All plan steps are complete",
    steps: "Plan steps",
    overall: "Overall progress",
    completedLabel: (done: number, total: number) => `${done}/${total} complete`,
    passedLabel: (passed: number, total: number) => `${passed}/${total} accepted`,
    completed: "Completed",
    inProgress: "In progress",
    blocked: "Blocked",
    pending: "Pending",
    passed: "Accepted",
    failed: "Acceptance failed",
    interrupted: "Test interrupted",
    notCovered: "Not covered",
    notAssessed: "Not assessed",
    review: (cycles: number, max: number) => `Review · ${cycles}/${max} cycles`,
    dependency: (deps: string) => `Depends on: ${deps}`,
    acceptance: "Acceptance criteria",
    evidence: "Evidence",
    feedback: "Rework needed",
    sync: "Synced from plan updates",
    blockedFact: (count: number) => `${count} blocked`,
    failedFact: (count: number) => `${count} with issues`,
    pendingFact: (count: number) => `${count} pending`,
  },
} as const;

/** Presentation only; milestone truth comes from durable update_plan. */
export function TaskProgressPanel({ steps, open, onClose }: TaskProgressPanelProps) {
  const language = useLoomLanguage();
  const copy = COPY[language];
  const presence = useMotionPresence(open, RETAIN.base);

  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose, open]);

  if (!presence.mounted || !steps.length) return null;

  const completed = steps.filter(step => step.status === "completed").length;
  const passed = steps.filter(step => step.status === "completed" && step.outcome === "passed").length;
  const blocked = steps.filter(step => step.status === "blocked").length;
  const pending = steps.filter(step => step.status === "pending").length;
  const percent = Math.round((completed / steps.length) * 100);
  const current = steps.find(step => step.status === "in_progress")
    ?? steps.find(step => step.status === "blocked") ?? steps.find(step => step.status === "pending");
  const currentTone = current ? toneFor(current) : "passed";

  const badge = (step: TaskMilestone): string => {
    if (step.outcome === "failed") return copy.failed;
    if (step.outcome === "interrupted") return copy.interrupted;
    if (step.outcome === "not_covered") return copy.notCovered;
    if (step.outcome === "not_assessed") return copy.notAssessed;
    if (step.status === "completed") return step.outcome === "passed" ? copy.passed : copy.completed;
    if (step.status === "in_progress") return copy.inProgress;
    if (step.status === "blocked") return copy.blocked;
    return copy.pending;
  };

  return createPortal(
    <section className="task-plan-panel pl-float pl-reveal" data-motion-phase={presence.phase}
      data-task-plan-panel="true" inert={!open} role="dialog" aria-modal="false" aria-label={copy.title}>
      <PanelHeader
        className="task-plan-panel-header"
        mark={<ListChecks size={16} strokeWidth={1.8} />}
        title={copy.title}
        subtitle={current?.step ?? copy.allDone}
        subtitleHint={current?.step}
        meta={<span className={`pl-chip task-plan-chip${completed === steps.length ? " is-complete" : ""}`}>{completed}/{steps.length}</span>}
        closeLabel={copy.close}
        onClose={onClose}
      />

      <div className="task-plan-summary">
        <div className={`task-plan-ring${completed === steps.length ? " is-complete" : ""}`}
          style={{ "--task-plan-ratio": `${percent * 3.6}deg` } as CSSProperties} role="img"
          aria-label={copy.completedLabel(completed, steps.length)}>
          <span>{percent}<small>%</small></span>
        </div>
        <div className="task-plan-summary-copy">
          <span className="task-plan-eyebrow">{copy.overall}</span>
          <strong>{copy.passedLabel(passed, steps.length)}</strong>
          <div className="task-plan-facts">
            <span className="task-plan-fact is-complete"><CheckCircle2 size={11} aria-hidden="true" />{copy.completedLabel(completed, steps.length)}</span>
            {blocked ? <span className="task-plan-fact is-blocked"><CircleAlert size={11} aria-hidden="true" />{copy.blockedFact(blocked)}</span> : null}
            {pending ? <span className="task-plan-fact">{copy.pendingFact(pending)}</span> : null}
          </div>
        </div>
      </div>

      <div className={`task-plan-focus is-${currentTone}`}>
        <span className="task-plan-focus-label"><Activity size={12} aria-hidden="true" />{current ? copy.current : copy.allDone}</span>
        <p>{current?.step ?? copy.allDone}</p>
      </div>

      <div className="task-plan-list-zone">
        <div className="task-plan-list-head">
          <span>{copy.steps}</span>
          <span>{completed}/{steps.length}</span>
        </div>
        <ol className="task-plan-list">
          {steps.map((step, index) => {
            const tone = toneFor(step);
            const isCurrent = step === current;
            const evidence = milestoneEvidenceLabels(step, language);
            return (
              <li key={`${index}:${step.step}`} className={`task-plan-step is-${tone}${isCurrent ? " is-current" : ""}`}
                aria-current={isCurrent ? "step" : undefined}>
                <span className="task-plan-step-mark" aria-hidden="true">
                  {tone === "failed" || tone === "interrupted" || tone === "not_covered"
                    ? <CircleAlert size={12} />
                    : step.status === "completed" ? <Check size={12} /> : index + 1}
                </span>
                <div className="task-plan-step-body">
                  <div className="task-plan-step-head">
                    <p className="task-plan-step-title">{step.step}</p>
                    <span className={`task-plan-badge is-${tone}`}>{badge(step)}</span>
                  </div>
                  {step.type === "review" ? <span className="task-plan-meta">
                    {copy.review(step.cycles ?? 0, step.max_review_cycles ?? 3)}
                  </span> : null}
                  {step.deps?.length ? <span className="task-plan-meta">
                    {copy.dependency(step.deps.map(id => steps.find(candidate => candidate.id === id)?.step ?? id).join(language === "zh-CN" ? "、" : ", "))}
                  </span> : null}
                  {step.feedback?.status === "open" ? <p className="task-plan-callout is-failed">
                    <CircleAlert size={12} aria-hidden="true" /><span>{copy.feedback}：{step.feedback.report}</span>
                  </p> : null}
                  {step.status === "blocked" && step.blocker ? <p className="task-plan-callout is-blocked">
                    <CircleAlert size={12} aria-hidden="true" /><span>{step.blocker}</span>
                  </p> : null}
                  {step.acceptance?.length ? (
                    <details className="task-plan-detail">
                      <summary><ChevronRight size={12} aria-hidden="true" /><span>{copy.acceptance}</span><em>{step.acceptance.length}</em></summary>
                      <div className="task-plan-detail-body">
                        {step.acceptance.map(criterion => <span key={criterion}>{criterion}</span>)}
                      </div>
                    </details>
                  ) : null}
                  {evidence.length || step.evidence ? (
                    <details className="task-plan-detail">
                      <summary><ChevronRight size={12} aria-hidden="true" /><span>{copy.evidence}</span><em>{step.evidence_refs?.length ?? 0}</em></summary>
                      <div className="task-plan-detail-body">
                        {evidence.map(label => <span key={label}>{label}</span>)}
                        {step.evidence ? <span>{step.evidence}</span> : null}
                      </div>
                    </details>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      </div>

      <footer className="task-plan-foot">
        <span><Sparkles size={12} aria-hidden="true" />{copy.sync}</span>
        <span>{copy.passedLabel(passed, steps.length)}</span>
      </footer>
    </section>,
    document.body,
  );
}

function toneFor(step: TaskMilestone): string {
  if (step.outcome === "failed") return "failed";
  if (step.outcome === "interrupted") return "interrupted";
  if (step.outcome === "not_covered") return "not_covered";
  if (step.status === "completed") return step.outcome === "passed" ? "passed" : "completed";
  return step.status;
}
