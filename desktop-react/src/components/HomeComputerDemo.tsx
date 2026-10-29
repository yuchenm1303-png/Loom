import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { ArrowLeft, ArrowRight, Check, ChevronRight, ChevronDown, Globe2, Plus, RotateCcw, Search, Settings, Clock3, UserRound, X } from "./icons";
import { CalendarDays, MapPin } from "lucide-react";
import "./home-computer-demo.css";
import { HomeComputerHud } from "./HomeComputerHud";

const STEP_MS = [850, 850, 1800, 900, 1100];

/** Scripted desktop preview. Controls only change local demo state. */
export function HomeComputerDemo({ zh }: { zh: boolean }) {
  const stage = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
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

  useLayoutEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const resize = () => setScale(node.clientWidth / 1280);
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

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
      const zoom = bounds.width / 1280;
      setPoint(step === 0 ? { x: 640, y: 360 } : box ? { x: (box.left - bounds.left) / zoom + Math.min(box.width / zoom / 2, 70), y: (box.top - bounds.top + box.height / 2) / zoom } : { x: 640, y: 360 });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [step, scale]);

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
    <div className="computer-demo-window" data-step={step} data-cycle={run} data-playing={auto} data-liquid-snap="false" aria-label={zh ? "Computer Use 交互演示" : "Computer Use interactive demo"}>
      <div className="computer-demo-viewport" ref={viewport} style={{ "--desktop-scale": scale } as CSSProperties}><div className="computer-demo-desktop">
      <div className="computer-demo-browser"><div className="computer-demo-browser-tab"><span className="computer-demo-window-dots"><i /><i /><i /></span><span><CalendarDays size={13} />{zh ? "日历 · 本周" : "Calendar · This week"}<X size={11} /></span><Plus size={13} /></div><div className="computer-demo-address"><ArrowLeft size={14} /><ArrowRight size={14} /><RotateCcw size={13} /><span><Globe2 size={12} />calendar.example/week</span><span className="computer-demo-browser-avatar">A</span></div></div>
      <div className="computer-demo-stage" ref={stage}>
        <aside className="computer-demo-calendar-sidebar"><div className="computer-demo-app-name"><CalendarDays size={23} /><strong>{zh ? "日历" : "Calendar"}</strong></div><button type="button" className="computer-demo-create" ref={node => { if (step <= 1) target.current = node; }} onClick={() => choose(2)}><Plus size={18} />{zh ? "新建日程" : "New event"}</button><div className="computer-demo-mini-month"><strong>{zh ? "2026 年 10 月" : "October 2026"}</strong><span><ChevronRight size={13} /></span><div>{(zh ? ["一", "二", "三", "四", "五", "六", "日"] : ["M", "T", "W", "T", "F", "S", "S"]).map((d, i) => <span key={`day-${i}`}>{d}</span>)}{Array.from({ length: 35 }, (_, i) => <span key={i} className={i === 32 ? "is-selected" : i < 3 || i > 33 ? "is-outside" : ""}>{i < 3 ? 28 + i : i > 33 ? 1 : i - 2}</span>)}</div></div><div className="computer-demo-calendar-list"><strong>{zh ? "我的日历" : "My calendars"}</strong><span><i />{zh ? "工作" : "Work"}</span><span><i />{zh ? "个人" : "Personal"}</span><span><i />{zh ? "提醒事项" : "Reminders"}</span></div><span className="computer-demo-calendar-account"><span>A</span><span>Alex Morgan<small>{zh ? "个人工作区" : "Personal workspace"}</small></span></span></aside>
        <div className="computer-demo-calendar-main">
        <div className="computer-demo-calendar-head"><strong>{zh ? "2026 年 10 月" : "October 2026"}</strong><span className="computer-demo-today">{zh ? "今天" : "Today"}</span><span className="computer-demo-date-arrows"><ArrowLeft size={15} /><ArrowRight size={15} /></span><span className="computer-demo-calendar-tools"><Search size={17} /><Settings size={17} /><span>{zh ? "周" : "Week"}<ChevronDown size={12} /></span></span></div>
        <div className="computer-demo-week"><span>GMT−7</span>{(zh ? ["周一", "周二", "周三", "周四", "周五"] : ["MON", "TUE", "WED", "THU", "FRI"]).map((day, i) => <span key={day} className={i === 4 ? "is-today" : ""}>{day}<b>{26 + i}</b></span>)}</div>
        <div className="computer-demo-all-day"><span>{zh ? "全天" : "all-day"}</span><span>{zh ? "产品迭代 · 第 44 周" : "Product sprint · Week 44"}</span></div>
        <div className="computer-demo-calendar"><div className="computer-demo-hours">{["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"].map(time => <span key={time}>{time}</span>)}</div>{[0, 1, 2, 3, 4].map(day => <div className="computer-demo-day" key={day}>{day === 0 || day === 2 ? <span className={`computer-demo-existing ${day === 2 ? "is-later" : ""}`}><strong>{day === 0 ? (zh ? "每周同步" : "Weekly sync") : (zh ? "项目讨论" : "Project check-in")}</strong><span>{day === 0 ? "09:00 – 09:30" : "11:00 – 11:30"}</span></span> : null}{day === 4 && step === 4 ? <span className="computer-demo-event"><strong>{title}</strong><span>10:00 – 10:30</span><small>{zh ? "工作 · 已保存" : "Work · Saved"}</small></span> : null}</div>)}</div>
        </div>
        {step >= 2 && step <= 3 ? <div className="computer-demo-event-form"><div className="computer-demo-form-title"><CalendarDays size={17} /><strong>{zh ? "新建日程" : "New event"}</strong><X size={16} /></div><label className="computer-demo-title-label">{zh ? "主题" : "Title"}<input ref={node => { if (step === 2) target.current = node; }} readOnly aria-label={zh ? "演示会议主题" : "Demo event title"} value={step === 2 ? title.slice(0, typed) : title} placeholder={zh ? "添加主题" : "Add a title"} /></label><div className="computer-demo-form-detail"><Clock3 size={16} /><div><strong>{zh ? "10 月 30 日，周五" : "Friday, October 30"}</strong><span>10:00 – 10:30 · Pacific Time</span></div></div><div className="computer-demo-form-detail"><UserRound size={16} /><span>{zh ? "添加参与者" : "Add guests"}</span></div><div className="computer-demo-form-detail"><MapPin size={16} /><span>{zh ? "添加地点或视频会议" : "Add location or video call"}</span></div><div className="computer-demo-form-detail"><span className="computer-demo-calendar-dot" /><span>{zh ? "工作日历 · 提前 10 分钟提醒" : "Work calendar · Remind 10 minutes before"}</span></div><footer><span>{zh ? "仅用于演示" : "Demo calendar"}</span><button ref={node => { if (step === 3) target.current = node; }} type="button" aria-label={step === 3 ? (zh ? "批准并保存演示" : "Approve & save demo") : (zh ? "检查日程" : "Review event")} onClick={() => choose(step === 3 ? 4 : 3)}>{step === 3 ? (zh ? "保存日程" : "Save event") : (zh ? "检查日程" : "Review event")}<ArrowRight size={14} /></button></footer></div> : null}
        <HomeComputerHud payload={{
          visible: visible && pageVisible,
          source: "computer",
          title: step === 4 ? (zh ? "任务已完成" : "Task complete") : (zh ? "Loom 正在控制桌面" : "Loom is controlling desktop"),
          meta: "Computer Use",
          bubbleTitle: labels[step],
          confidence: "—",
          hudX: point.x,
          hudY: point.y,
          actionSource: "screenshot + UIA",
          thought: zh ? "模拟日历中的示例操作。" : "Sample action in a simulated calendar.",
          clickRevision: step === 1 || step === 3 ? run + "-" + step : "",
        }} />
      </div>
      </div></div>
      <div className="computer-demo-controls"><div role="group" aria-label={zh ? "Computer Use 演示步骤" : "Computer Use demo steps"}>{labels.slice(0, 4).map((label, index) => <button type="button" key={label} aria-pressed={step === index || (step === 4 && index === 3)} onClick={() => choose(index)}>{index < step ? <Check size={11} /> : <span>{index + 1}</span>}{label}</button>)}</div><div><button type="button" aria-label={zh ? "重播 Computer Use 演示" : "Replay Computer Use demo"} onClick={replay}><RotateCcw size={13} /></button>{reduced ? <button type="button" onClick={() => choose((step + 1) % 5)}>{zh ? "下一步" : "Next"}<ChevronRight size={12} /></button> : <span className="computer-demo-loop"><i />{zh ? "循环演示" : "On repeat"}</span>}</div></div>
      <p className="computer-demo-footnote">{zh ? "模拟日历中的示例，不会创建真实日程。" : "A simulated calendar. No real events are created."}</p>
    </div>
  </section>;
}
