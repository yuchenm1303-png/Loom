import { Check, CircleAlert, ChevronLeft, ListChecks, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { TaskMilestone } from "./liveTaskProgress";
import "./task-progress-panel.css";

/** Responsive presentation only; milestone truth comes from durable update_plan. */
export function TaskProgressPanel({ steps }: { steps: TaskMilestone[] }) {
  const panel = useRef<HTMLElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [compact, setCompact] = useState(true);
  const [collapsed, setCollapsed] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const titleId = useId();
  const completed = steps.filter(step => step.status === "completed").length;
  const current = steps.find(step => step.status === "in_progress")
    ?? steps.find(step => step.status === "blocked") ?? steps.find(step => step.status === "pending");
  const rail = compact || collapsed;

  useEffect(() => {
    const stage = panel.current?.parentElement;
    if (!stage) return;
    const observer = new ResizeObserver(([entry]) => setCompact(entry.contentRect.width < 1000));
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!compact && dialog.current?.open) dialog.current.close();
  }, [compact]);

  const content = (
    <>
      <div className="task-plan-heading">
        <ListChecks size={17} aria-hidden="true" />
        <h2 id={titleId}>任务进度</h2>
        <span>{completed}/{steps.length} 已完成</span>
      </div>
      <progress className="task-plan-meter" max={steps.length} value={completed} aria-label="已完成的计划步骤" />
      <p className="task-plan-current">{current?.step ?? "计划步骤已完成"}</p>
      <ol className="task-plan-list">
        {steps.map((step, index) => (
          <li key={`${index}:${step.step}`} className={`is-${step.status}`} aria-current={step.status === "in_progress" ? "step" : undefined}>
            <span className="task-plan-step-mark" aria-hidden="true">{step.outcome === "failed" || step.outcome === "interrupted" || step.outcome === "not_covered" ? <CircleAlert size={13} /> : step.status === "completed" ? <Check size={13} /> : index + 1}</span>
            <div><span className="task-plan-status">{({ pending: "待处理", in_progress: "进行中", completed: "已完成", blocked: "受阻" })[step.status]}</span>
              <p>{step.step}</p>
              {step.outcome && step.outcome !== "not_assessed" ? <small className={`task-plan-outcome is-${step.outcome}`}>{({ passed: "验收通过", failed: "验收失败", interrupted: "测试中断", not_covered: "未覆盖" })[step.outcome]}</small> : null}
              {step.status === "blocked" && step.blocker ? <small>{step.blocker}</small> : null}
            </div>
          </li>
        ))}
      </ol>
    </>
  );

  return (
    <aside ref={panel} className={`task-progress-dock ${rail ? "is-rail" : "is-expanded"}`} aria-label="任务进度">
      {rail ? (
        <button ref={trigger} type="button" className="task-plan-trigger" aria-label={`查看任务进度，${completed}/${steps.length} 已完成`}
          title={current?.step ?? "计划步骤已完成"} aria-haspopup={compact ? "dialog" : undefined} aria-expanded={compact ? dialogOpen : false}
          onClick={() => { if (compact) { dialog.current?.showModal(); setDialogOpen(true); } else setCollapsed(false); }}>
          <ListChecks size={18} aria-hidden="true" /><span>{completed}/{steps.length}</span>
        </button>
      ) : (
        <section className="task-plan-card">
          <button type="button" className="task-plan-close" aria-label="折叠任务进度" onClick={() => setCollapsed(true)}><ChevronLeft size={16} /></button>
          {content}
        </section>
      )}
      <dialog ref={dialog} className="task-plan-dialog" aria-labelledby={titleId}
        onClose={() => { setDialogOpen(false); trigger.current?.focus(); }}
        onClick={event => { if (event.target === event.currentTarget) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.current?.close(); } }}>
        <button type="button" className="task-plan-close" aria-label="关闭任务进度" onClick={() => dialog.current?.close()}><X size={18} /></button>
        {compact ? content : null}
      </dialog>
    </aside>
  );
}
