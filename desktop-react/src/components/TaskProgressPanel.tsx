import { Activity, Check, CheckCircle2, ChevronRight, CircleAlert, ListChecks, Sparkles } from "./icons";
import { useEffect, useId, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { useLoomLanguage } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { RETAIN } from "../motion/tokens";
import { PanelHeader } from "./PanelHeader";
import { milestoneEvidenceLabels, type TaskMilestone } from "./liveTaskProgress";
import "./task-progress-panel.css";

interface TaskProgressPanelProps {
  steps: TaskMilestone[];
  /** Set when the list belongs to an earlier request: the person's new message has no list of its own yet. */
  earlierRequest?: string;
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
    dropped: "已放弃",
    droppedFact: (count: number) => `已放弃 ${count}`,
    droppedGroup: "放弃的步骤",
    droppedNote: "换清单时没有做完的步骤。若其实已做完，模型补记后会从这里消失。",
    earlier: (request: string) => request ? `这份清单属于之前的请求「${request}」，这一轮还没有写自己的清单。`
      : "这份清单属于之前的请求，这一轮还没有写自己的清单。",
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
    dropped: "Dropped",
    droppedFact: (count: number) => `${count} dropped`,
    droppedGroup: "Dropped steps",
    droppedNote: "Steps a list left unfinished when it was replaced. If one was in fact done, it leaves this list once the model records it.",
    earlier: (request: string) => request ? `This list is from an earlier request, “${request}”. This turn has no list of its own yet.`
      : "This list is from an earlier request. This turn has no list of its own yet.",
  },
} as const;

/** Presentation only; milestone truth comes from durable update_plan. */
export function TaskProgressPanel({ steps, earlierRequest, open, onClose }: TaskProgressPanelProps) {
  const language = useLoomLanguage();
  const copy = COPY[language];
  const presence = useMotionPresence(open, RETAIN.base);
  const [droppedOpen, setDroppedOpen] = useState(false);
  const droppedId = useId();

  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose, open]);

  if (!presence.mounted || !steps.length) return null;

  // Dropped steps are shown, not counted: the plan's progress is over what it still means to do.
  const planned = steps.filter(step => step.status !== "dropped");
  const droppedSteps = steps.filter(step => step.status === "dropped");
  const dropped = droppedSteps.length;
  const total = planned.length;
  const completed = planned.filter(step => step.status === "completed").length;
  const passed = planned.filter(step => step.status === "completed" && step.outcome === "passed").length;
  const blocked = planned.filter(step => step.status === "blocked").length;
  const pending = planned.filter(step => step.status === "pending").length;
  const percent = total ? Math.round((completed / total) * 100) : 0;
  const current = planned.find(step => step.status === "in_progress")
    ?? planned.find(step => step.status === "blocked") ?? planned.find(step => step.status === "pending");
  const currentTone = current ? toneFor(current) : "passed";

  const badge = (step: TaskMilestone): string => {
    if (step.outcome === "failed") return copy.failed;
    if (step.outcome === "interrupted") return copy.interrupted;
    if (step.outcome === "not_covered") return copy.notCovered;
    if (step.outcome === "not_assessed") return copy.notAssessed;
    if (step.status === "completed") return step.outcome === "passed" ? copy.passed : copy.completed;
    if (step.status === "in_progress") return copy.inProgress;
    if (step.status === "blocked") return copy.blocked;
    if (step.status === "dropped") return copy.dropped;
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
        meta={<span className={`pl-chip task-plan-chip${completed === total ? " is-complete" : ""}`}>{completed}/{total}</span>}
        closeLabel={copy.close}
        onClose={onClose}
      />

      <div className="task-plan-summary">
        <div className={`task-plan-ring${completed === total ? " is-complete" : ""}`}
          style={{ "--task-plan-ratio": `${percent * 3.6}deg` } as CSSProperties} role="img"
          aria-label={copy.completedLabel(completed, total)}>
          <span>{percent}<small>%</small></span>
        </div>
        <div className="task-plan-summary-copy">
          <span className="task-plan-eyebrow">{copy.overall}</span>
          <strong>{copy.passedLabel(passed, total)}</strong>
          <div className="task-plan-facts">
            <span className="task-plan-fact is-complete"><CheckCircle2 size={11} aria-hidden="true" />{copy.completedLabel(completed, total)}</span>
            {blocked ? <span className="task-plan-fact is-blocked"><CircleAlert size={11} aria-hidden="true" />{copy.blockedFact(blocked)}</span> : null}
            {pending ? <span className="task-plan-fact">{copy.pendingFact(pending)}</span> : null}
            {dropped ? <span className="task-plan-fact">{copy.droppedFact(dropped)}</span> : null}
          </div>
        </div>
      </div>

      {earlierRequest !== undefined ? <p className="task-plan-callout is-blocked" data-task-plan-earlier="true">
        <CircleAlert size={12} aria-hidden="true" /><span>{copy.earlier(earlierRequest)}</span>
      </p> : null}

      <div className={`task-plan-focus is-${currentTone}`}>
        <span className="task-plan-focus-label"><Activity size={12} aria-hidden="true" />{current ? copy.current : copy.allDone}</span>
        <p>{current?.step ?? copy.allDone}</p>
      </div>

      <div className="task-plan-list-zone">
        <div className="task-plan-list-head">
          <span>{copy.steps}</span>
          <span>{completed}/{total}</span>
        </div>
        <ol className="task-plan-list">
          {planned.map((step, index) => {
            const tone = toneFor(step);
            const isCurrent = step === current;
            const evidence = milestoneEvidenceLabels(step, language);
            return (
              <li key={`${index}:${step.step}`} className={`task-plan-step is-${tone}${isCurrent ? " is-current" : ""}`}
                style={{ "--i": Math.min(index, 8) } as CSSProperties} aria-current={isCurrent ? "step" : undefined}>
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
                  {(step.status === "blocked" || step.status === "dropped") && step.blocker ? <p className="task-plan-callout is-blocked">
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
          {droppedSteps.length ? (
            <li className={`task-plan-dropped${droppedOpen ? " is-open" : ""}`}>
              <button type="button" className="task-plan-dropped-head" aria-expanded={droppedOpen} aria-controls={droppedId}
                onClick={() => setDroppedOpen(value => !value)}>
                <ChevronRight size={12} aria-hidden="true" /><span>{copy.droppedGroup}</span><em>{droppedSteps.length}</em>
              </button>
              <div className="task-plan-dropped-fold" id={droppedId} role="region" inert={!droppedOpen}>
                <div>
                  <p className="task-plan-dropped-note">{copy.droppedNote}</p>
                  <ul className="task-plan-dropped-list">
                    {droppedSteps.map((step, index) => (
                      <li key={`${index}:${step.step}`} className="task-plan-dropped-row">
                        <span className="task-plan-dropped-dash" aria-hidden="true" />
                        <div>
                          <p>{step.step}</p>
                          {step.blocker ? <small>{step.blocker}</small> : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </li>
          ) : null}
        </ol>
      </div>

      <footer className="task-plan-foot">
        <span><Sparkles size={12} aria-hidden="true" />{copy.sync}</span>
        <span>{copy.passedLabel(passed, total)}</span>
      </footer>
    </section>,
    document.body,
  );
}

function toneFor(step: TaskMilestone): string {
  if (step.outcome === "failed") return "failed";
  if (step.outcome === "interrupted") return "interrupted";
  if (step.outcome === "not_covered" || step.status === "dropped") return "not_covered";  // not done, said plainly
  if (step.status === "completed") return step.outcome === "passed" ? "passed" : "completed";
  return step.status;
}
