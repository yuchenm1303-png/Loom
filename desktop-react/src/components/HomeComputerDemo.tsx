import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { ArrowRight, Check, ChevronRight, Globe2, Plus, RotateCcw } from "./icons";
import "./home-computer-demo.css";

const STEP_MS = [850, 850, 1800, 900, 1100];

/** Scripted desktop preview. Controls only change local demo state. */
export function HomeComputerDemo({ zh }: { zh: boolean }) {
  const stage = useRef<HTMLDivElement>(null);
  const target = useRef<HTMLElement | null>(null);
  const [step, setStep] = useState(0);
  const [visible, setVisible] = useState(false);
  const [pageVisible, setPageVisible] = useState(!document.hidden);
  const [reduced, setReduced] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [typed, setTyped] = useState(0);
  const [run, setRun] = useState(0);
  const [point, setPoint] = useState({ x: 0, y: 0 });
  const title = zh ? "设计评审" : "Design review";
  const labels = zh ? ["观察页面", "点击目标", "填写内容", "确认保存", "安排完成"] : ["Observe", "Click", "Type", "Confirm", "Scheduled"];
  const auto = visible && pageVisible && !reduced;

  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const motion = () => setReduced(media.matches);
    const page = () => setPageVisible(!document.hidden);
    media.addEventListener("change", motion);
    document.addEventListener("visibilitychange", page);
    const observer = typeof IntersectionObserver === "function" ? new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: .25 }) : null;
    if (stage.current) observer?.observe(stage.current);
    if (!observer) setVisible(true);
    return () => { observer?.disconnect(); media.removeEventListener("change", motion); document.removeEventListener("visibilitychange", page); };
  }, []);

  useEffect(() => {
    if (!auto) return;
    const timer = window.setTimeout(() => {
      if (step === 4) { setTyped(0); setRun(value => value + 1); setStep(0); }
      else setStep(step + 1);
    }, STEP_MS[step]);
    return () => clearTimeout(timer);
  }, [auto, step, run]);

  useEffect(() => {
    if (step !== 2 || typed >= title.length || !visible || !pageVisible) return;
    if (reduced) { setTyped(title.length); return; }
    const timer = window.setTimeout(() => setTyped(value => value + 1), 45);
    return () => clearTimeout(timer);
  }, [step, typed, title, reduced, visible, pageVisible]);

  useLayoutEffect(() => {
    const node = stage.current;
    if (!node) return;
    const measure = () => {
      const bounds = node.getBoundingClientRect();
      const box = (target.current?.isConnected ? target.current : node.querySelector(".computer-demo-event"))?.getBoundingClientRect();
      setPoint(step === 0 ? { x: bounds.width * .39, y: bounds.height * .48 } : box ? { x: box.left - bounds.left + Math.min(box.width / 2, 70), y: box.top - bounds.top + box.height / 2 } : { x: bounds.width * .5, y: bounds.height * .4 });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [step]);

  function choose(next: number) { setStep(next); setTyped(next >= 2 ? title.length : 0); setRun(value => value + 1); }
  function replay() { setStep(0); setTyped(0); setRun(value => value + 1); }

  return <section id="loom-computer-use" className="editorial-section computer-demo-section" aria-labelledby="computer-demo-title">
    <div className="computer-demo-copy" data-reveal>
      <p className="computer-demo-eyebrow">COMPUTER USE</p>
      <h2 id="computer-demo-title">{zh ? "看得见，也做得到。" : "See it. Work with it."}</h2>
      <p>{zh ? "不止回答问题。Loom 能看懂屏幕、移动光标、点击按钮和填写表单，把任务推进到完成。" : "Beyond the conversation. Loom can read the screen, move the pointer, click buttons and fill in forms to carry a task through."}</p>
      <p className="computer-demo-request">{zh ? "“帮我安排周五上午 10 点的设计评审。”" : "“Schedule a design review for Friday at 10.”"}</p>
      <div className="computer-demo-explanation"><span>{`0${step + 1}`}</span><div><strong>{labels[step]}</strong><p>{(zh ? ["先观察日历，找到创建日程的入口。", "移动到目标按钮，点击创建日程。", "填入会议主题、日期和时间。", "检查示例内容，确认并保存日程。", "日程已创建，即将自动演示下一轮。"] : ["Read the calendar and locate the new-event control.", "Move to the target and click to create an event.", "Enter the meeting title, date and time.", "Review the example, confirm and save the event.", "Event created. The next demonstration starts automatically."])[step]}</p></div></div>
    </div>
    <div className="computer-demo-window" data-step={step} data-cycle={run} data-playing={auto} style={{ "--cu-duration": `${STEP_MS[step]}ms` } as CSSProperties} data-liquid-snap="false" aria-label={zh ? "Computer Use 交互演示" : "Computer Use interactive demo"}>
      <div className="computer-demo-browser"><span aria-hidden="true"><i /><i /><i /></span><div><Globe2 size={12} />calendar.example / week</div><span>{zh ? "交互演示" : "Interactive demo"}</span></div>
      <div className="computer-demo-stage" ref={stage}>
        <div className="computer-demo-calendar-head"><strong>{zh ? "我的日历" : "My calendar"}</strong><span>{zh ? "十月 · 本周" : "October · This week"}</span><button type="button" ref={node => { if (step <= 1) target.current = node; }} className={step === 1 ? "is-target" : ""} onClick={() => choose(2)}><Plus size={12} />{zh ? "新建日程" : "New event"}</button></div>
        <div className="computer-demo-week"><span /><span>MON <b>26</b></span><span>TUE <b>27</b></span><span>WED <b>28</b></span><span>THU <b>29</b></span><span className="is-today">FRI <b>30</b></span></div>
        <div className="computer-demo-calendar"><div className="computer-demo-hours"><span>09:00</span><span>10:00</span><span>11:00</span><span>12:00</span></div>{[0, 1, 2, 3, 4].map(day => <div className="computer-demo-day" key={day}>{day === 0 ? <span className="computer-demo-existing">{zh ? "周会" : "Weekly sync"}</span> : null}{day === 4 && step === 4 ? <span className="computer-demo-event"><Check size={12} /><strong>{title}</strong><span>10:00 – 10:30</span></span> : null}</div>)}</div>
        {step >= 2 && step <= 3 ? <div className="computer-demo-event-form"><div><strong>{zh ? "新建日程" : "New event"}</strong><span>{zh ? "草稿" : "Draft"}</span></div><label>{zh ? "主题" : "Title"}<input ref={node => { if (step === 2) target.current = node; }} readOnly aria-label={zh ? "演示会议主题" : "Demo event title"} value={step === 2 ? title.slice(0, typed) : title} placeholder={zh ? "添加主题" : "Add a title"} /></label><div className="computer-demo-form-row"><label>{zh ? "日期" : "Date"}<span>{zh ? "10 月 30 日，周五" : "Fri, October 30"}</span></label><label>{zh ? "时间" : "Time"}<span>10:00 – 10:30</span></label></div><button ref={node => { if (step === 3) target.current = node; }} type="button" className={step === 3 ? "is-target" : ""} onClick={() => choose(step === 3 ? 4 : 3)}>{step === 3 ? (zh ? "批准并保存演示" : "Approve & save demo") : (zh ? "检查日程" : "Review event")}<ArrowRight size={12} /></button></div> : null}
        {/* HUD composition follows ComputerUseHudOverlay and the page-local browser HUD. */}
        <div className="computer-demo-hud" aria-hidden="true">
          <div className="computer-demo-scan" />
          <div className="computer-demo-edge" />
          <div className="computer-demo-pill"><i /><strong>{step === 4 ? (zh ? "任务已完成" : "Task complete") : step === 3 ? (zh ? "正在确认保存" : "Confirming & saving") : (zh ? "Loom 正在操作" : "Loom is controlling")}</strong><span>Computer Use</span></div>
          <div className={`computer-demo-cursor ${step === 1 || step === 3 ? "is-clicking" : ""}`} style={{ "--cu-x": `${point.x}px`, "--cu-y": `${point.y}px` } as CSSProperties}><HudCursor /><i /></div>
          <div className="computer-demo-hud-info"><strong>{labels[step]}</strong><span>{zh ? "执行坐标" : "Target point"}<b>{Math.round(point.x)}, {Math.round(point.y)}</b></span><span>{zh ? "动作来源" : "Action source"}<b>screenshot + UIA</b></span><p>{zh ? "示例操作 · 屏幕上的每一步都看得见" : "Sample action · Every step stays visible"}</p></div>
        </div>
      </div>
      <div className="computer-demo-progress" aria-hidden="true"><span key={`${run}-${step}`} /></div>
      <div className="computer-demo-controls"><div role="group" aria-label={zh ? "Computer Use 演示步骤" : "Computer Use demo steps"}>{labels.slice(0, 4).map((label, index) => <button type="button" key={label} aria-pressed={step === index || (step === 4 && index === 3)} onClick={() => choose(index)}>{index < step ? <Check size={11} /> : <span>{index + 1}</span>}{label}</button>)}</div><div><button type="button" aria-label={zh ? "重播 Computer Use 演示" : "Replay Computer Use demo"} onClick={replay}><RotateCcw size={13} /></button>{reduced ? <button type="button" onClick={() => choose((step + 1) % 5)}>{zh ? "下一步" : "Next"}<ChevronRight size={12} /></button> : <span className="computer-demo-loop"><i />{zh ? "循环演示" : "On repeat"}</span>}</div></div>
      <p className="computer-demo-footnote">{zh ? "模拟日历中的示例，不会创建真实日程。" : "A simulated calendar. No real events are created."}</p>
    </div>
  </section>;
}

function HudCursor() {
  // Same silhouette, base gradient and rim as extensions/browser-current-tab/browser-hud.js.
  return <svg viewBox="0 0 64 64" focusable="false"><defs><path id="home-cu-shape" d="M 7.8 7.8 C 8.91 5.71, 13.83 7.17, 20.4 10.4 C 26.97 13.63, 49.02 25.34, 51.1 29.1 C 53.18 32.86, 37.42 31.38, 34.1 35.2 C 30.78 39.02, 30.93 52.78, 29.2 54.3 C 27.47 55.82, 25.14 49.77, 22.7 45.2 C 20.26 40.63, 15.36 29.87, 13.1 24.2 C 10.84 18.53, 6.69 9.89, 7.8 7.8 Z" /><linearGradient id="home-cu-base" x1="9" y1="9" x2="42" y2="55" gradientUnits="userSpaceOnUse"><stop stopColor="#2EF1FF" /><stop offset=".23" stopColor="#69E9FF" /><stop offset=".54" stopColor="#EDF7FF" /><stop offset=".78" stopColor="#E6D9FF" /><stop offset="1" stopColor="#FF9FCE" /></linearGradient><linearGradient id="home-cu-rim"><stop stopColor="#CCFEFF" /><stop offset="1" stopColor="#FFC5E2" /></linearGradient></defs><use href="#home-cu-shape" fill="url(#home-cu-base)" stroke="url(#home-cu-rim)" strokeWidth="1.02" /></svg>;
}
