import { useRef, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { DUR, EASE } from "../motion/tokens";
import { useReducedMotion } from "../motion/useReducedMotion";
import { Check, Circle, Compass, Minus, X } from "../components/icons";
import type { FlowData } from "../components/ProjectFlow";
import type { LongGoal } from "../components/projectOverview";
import { TickText } from "../components/TickText";

/** The long goal on the phone, read top to bottom: what state it is in and what it has spent, what it set out
 *  to deliver, where along its phases it is, then each criterion with a mark. Long prose folds to a few lines. */
type Goal = NonNullable<FlowData["goal"]>;

const money = (value: number) => value >= 1_000_000 ? `${Number((value / 1_000_000).toFixed(1))}M` : `${Math.round(value / 1000)}K`;
const MARK = { running: <Circle size={12} strokeWidth={2} />, done: <Check size={13} strokeWidth={2.4} />, failed: <X size={13} strokeWidth={2.4} />,
               claimed: <Check size={12} strokeWidth={1.6} /> };

function Clamp({ lines, children, className = "" }: { lines: number; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  // Clamping is not something CSS can animate: the height is measured on both sides and played between them. Closing
  // keeps the full text painted until the room has closed over it, so no line is cut before it goes.
  const toggle = () => {
    const root = box.current;
    const text = root?.querySelector("p");
    if (!root || !text || reduced) { setOpen(!open); return; }
    const from = text.offsetHeight;
    flushSync(() => setOpen(!open));
    const to = text.offsetHeight;
    if (open) root.setAttribute("data-settling", "");
    const motion = text.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: DUR.flow, easing: EASE.move });
    motion.onfinish = motion.oncancel = () => root.removeAttribute("data-settling");
  };
  return (
    <div ref={box} className={`m-goal-clamp ${className}`} data-open={open || undefined} style={{ ["--m-lines" as string]: lines }}>
      <p>{children}</p>
      <button type="button" className="m-goal-more" aria-expanded={open} onClick={toggle}>{open ? "收起" : "展开"}</button>
    </div>
  );
}

export function MobileGoalCard({ goal, raw, children }: { goal: Goal; raw: LongGoal; children?: ReactNode }) {
  // goal.text is "<state> · <spent>": the state heads the card, the spend becomes the meter.
  const state = goal.text.replace(/ · [\d.]+[KM] \/ [\d.]+[KM]$/, "").replace(/^长目标(?=.)/, "").replace(/^[，,]/, "");
  const minutes = raw.startedAt ? Math.max(0, Math.round((Date.now() / 1000 - raw.startedAt) / 60)) : 0;
  // With hours given the bar is the night going by; otherwise it is the budget (a huge budget leaves a token bar empty).
  const share = raw.hours && raw.startedAt ? Math.min(1, minutes / (raw.hours * 60)) : raw.tokenBudget ? Math.min(1, raw.tokens / raw.tokenBudget) : 0;
  const elapsed = minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分` : `${minutes} 分`;
  const charter = raw.charter;
  const tone = goal.live ? "run" : goal.action === "resume" ? "wait" : "idle";
  const criteria = goal.charter?.items ?? [];
  const met = criteria.filter((item) => item.state === "done" && !/放弃/.test(item.label)).length;
  const counted = criteria.filter((item) => !/放弃/.test(item.label)).length;
  return (
    <section className="m-card m-goal" data-tone={tone}>
      <header className="m-goal-head">
        <span className="m-tile" aria-hidden="true"><Compass size={18} strokeWidth={1.8} /></span>
        <div className="m-goal-title">
          <h2>长目标</h2>
          <span className="m-goal-state"><i aria-hidden="true" /><TickText value={state} /></span>
        </div>
      </header>

      <div className="m-goal-meter" role="group" aria-label={raw.hours ? "长目标时间与用量" : "长目标用量"}>
        <div className="m-goal-meter-row">
          <span><b><TickText value={money(raw.tokens)} /></b> / {money(raw.tokenBudget)}</span>
          <span>{raw.startedAt ? <TickText value={`已运行 ${elapsed}${raw.hours ? ` / ${raw.hours} 小时` : ""}`} /> : ""}</span>
        </div>
        <div className="m-goal-bar" aria-hidden="true"><span style={{ transform: `scaleX(${Math.max(share, 0.004)})` }} /></div>
      </div>

      {goal.detail ? <Clamp lines={3} className="m-goal-objective">{goal.detail}</Clamp> : null}

      {charter && goal.charter ? <>
        <div className="m-goal-block">
          <span className="m-goal-label">交付</span>
          <Clamp lines={4}>{goal.charter.deliverable}</Clamp>
        </div>
        <div className="m-goal-block">
          <span className="m-goal-label">阶段 {charter.current + 1}/{charter.phases.length}{goal.charter.where.includes("轮评审") ? ` · ${goal.charter.where.split(" · ").pop()}` : ""}</span>
          <ol className="m-goal-phases" aria-label="阶段">
            {charter.phases.map((phase, index) => (
              <li key={index} data-at={index === charter.current ? "now" : index < charter.current ? "past" : undefined} title={phase} />
            ))}
          </ol>
          <p className="m-goal-phase">{charter.phases[charter.current]?.replace(/^Revision round (\d+)$/, "第 $1 轮修改")}</p>
          {goal.charter.judgment ? <p className="m-goal-phase">{goal.charter.judgment}</p> : null}
        </div>
        <div className="m-goal-block">
          <span className="m-goal-label">验收标准 · {goal.charter.tally ?? `${met}/${counted} 已达标`}</span>
          <ul className="m-goal-criteria">
            {criteria.map((item) => {
              const dropped = /放弃/.test(item.label);
              return (
                <li key={item.title} data-state={dropped ? "dropped" : item.state}>
                  <span className="m-goal-mark" aria-hidden="true">{dropped ? <Minus size={13} strokeWidth={2.4} /> : MARK[item.state as keyof typeof MARK] ?? MARK.running}</span>
                  {/* The lead's own mark says so in words: only the check's is a pass. */}
                  <span className="m-goal-criterion"><span>{item.title}</span>{item.state === "claimed" || item.label.includes(" · ")
                    ? <small>{[item.state === "claimed" ? "自评已达标" : "", ...item.label.split(" · ").slice(1)].filter(Boolean).join(" · ")}</small> : null}</span>
                </li>
              );
            })}
          </ul>
        </div>
      </> : null}

      {goal.lines?.length ? (
        <div className="m-goal-block">
          <span className="m-goal-label">并行支线</span>
          <ul className="m-goal-criteria">
            {goal.lines.map((line) => (
              <li key={line.title} data-state={line.state === "done" ? "done" : line.state === "failed" ? "failed" : "running"}>
                <span className="m-goal-mark" aria-hidden="true">{line.state === "done" ? MARK.done : line.state === "failed" ? MARK.failed : MARK.running}</span>
                <span className="m-goal-criterion"><span>{line.title}</span><small>{line.label}</small></span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {children}
    </section>
  );
}
