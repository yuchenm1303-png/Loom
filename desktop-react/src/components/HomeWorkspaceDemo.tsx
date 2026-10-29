import { useEffect, useRef, useState } from "react";
import { Check, FolderOpen, ArrowRight } from "./icons";
import "./home-workspace-demo.css";

/** A local, scripted product preview. No files, commands or approvals leave this component. */
export function HomeWorkspaceDemo({ zh, ready }: { zh: boolean; ready: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [visible, setVisible] = useState(false);
  const [pageVisible, setPageVisible] = useState(!document.hidden);
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [tab, setTab] = useState<"activity" | "diff">("activity");
  const [run, setRun] = useState(0);
  const labels = zh ? ["读取项目", "修改代码", "运行测试", "等待批准", "已完成"] : ["Read project", "Edit code", "Run tests", "Your approval", "Complete"];

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const motion = () => setReduced(media.matches);
    const visibility = () => setPageVisible(!document.hidden);
    media.addEventListener("change", motion);
    document.addEventListener("visibilitychange", visibility);
    if (typeof IntersectionObserver !== "function") setVisible(true);
    const observer = typeof IntersectionObserver === "function" ? new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: .15 }) : null;
    if (root.current) observer?.observe(root.current);
    return () => {
      observer?.disconnect();
      media.removeEventListener("change", motion);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);

  const auto = playing && visible && pageVisible && ready && !reduced && step < 3;
  useEffect(() => {
    if (!auto) return;
    const timer = window.setTimeout(() => setStep(value => Math.min(3, value + 1)), step === 0 ? 3200 : 2600);
    return () => window.clearTimeout(timer);
  }, [auto, step, run]);

  function choose(next: number) {
    setPlaying(false);
    setStep(next);
    setTab("activity");
  }

  function replay() {
    setStep(0);
    setTab("activity");
    setPlaying(true);
    setRun(value => value + 1);
  }

  return <div ref={root} className="home-demo" data-step={step} data-playing={auto} data-liquid-snap="false">
    <div className="home-demo-bar">
      <span className="home-demo-brand"><img src="/loom-icon.png" alt="" /> Loom <span>/ release-fix</span></span>
      <span className="home-demo-badge">{zh ? "交互演示" : "Interactive demo"}</span>
    </div>
    <div className="home-demo-workspace">
      <aside className="home-demo-sidebar" aria-label={zh ? "演示步骤" : "Demo steps"}>
        <span className="home-demo-eyebrow">{zh ? "工作区" : "WORKSPACE"}</span>
        <div className="home-demo-project"><FolderOpen size={14} /> release-fix</div>
        <span className="home-demo-eyebrow">{zh ? "任务进度" : "TASK PROGRESS"}</span>
        {labels.slice(0, 4).map((label, index) => <button key={label} type="button" aria-pressed={step === index || (index === 3 && step === 4)} onClick={() => choose(index)}>
          <span className="home-demo-step-icon">{index < step ? <Check size={12} /> : `0${index + 1}`}</span><span>{label}</span>
        </button>)}
        <span className="home-demo-local"><span />{zh ? "在你的电脑上" : "On your computer"}</span>
      </aside>
      <div className="home-demo-main">
        <div className="home-demo-tabs" role="group" aria-label={zh ? "演示面板" : "Demo panels"}>
          <button type="button" aria-pressed={tab === "activity"} onClick={() => { setTab("activity"); setPlaying(false); }}>{zh ? "对话" : "Conversation"}</button>
          <button type="button" aria-pressed={tab === "diff"} onClick={() => { setTab("diff"); setPlaying(false); }}>{zh ? "代码修改" : "Changes"}<span>1</span></button>
        </div>
        <div className="home-demo-content">
          {tab === "diff" ? <div className="home-demo-scene" key="diff"><Diff zh={zh} /><p className="home-demo-detail">{zh ? "把边界判断改为包含当前版本。" : "Include the current version in the release check."}</p></div> : <div className="home-demo-scene" key={`${run}-${step}`}>
            <div className="home-demo-prompt">{zh ? "修好发布测试，然后推送。" : "Fix the release test, then push it."}</div>
            <div className="home-demo-agent"><img src="/loom-icon.png" alt="" /><span>Loom</span><span className="home-demo-status" role="status">{labels[step]}</span></div>
            <p className="home-demo-narration">{(zh ? ["先读一下项目，定位失败的测试。", "找到了：当前版本被边界条件排除了。", "修复已完成，现在验证测试。", "41 项测试全部通过。推送前，请你确认。", "已批准。修复已提交并推送。"] : ["I’ll read the project and trace the failing test.", "Found it. The boundary check excludes the current version.", "The fix is in. Let’s verify it with the tests.", "All 41 tests passed. Ready for your review before pushing.", "Approved. The fix is committed and pushed."])[step]}</p>
            {step === 0 ? <div className="home-demo-files"><span><FolderOpen size={14} /> release-fix /</span><code>release.py <span>124 {zh ? "行" : "lines"}</span></code><code>tests/test_release.py <span>86 {zh ? "行" : "lines"}</span></code><div className="home-demo-scanning"><i />{zh ? "正在追踪测试失败原因…" : "Tracing the failing assertion…"}</div></div> : null}
            {step === 1 ? <Diff zh={zh} /> : null}
            {step === 2 ? <div className="home-demo-terminal"><span>TERMINAL</span><code>$ pytest tests/test_release.py</code><code className="home-demo-test-dots">.........................................</code><code className="home-demo-passed">41 passed in 0.82s</code><span>exit code 0</span></div> : null}
            {step === 3 ? <div className="home-demo-approval"><strong>{zh ? "推送这次修改？" : "Push this change?"}</strong><span>release.py <span className="home-demo-passed">+1</span> <span className="home-demo-removed">−1</span> · 41 {zh ? "项测试通过" : "tests passed"}</span><code>git push origin main</code><div><button type="button" onClick={() => setTab("diff")}>{zh ? "查看修改" : "Review changes"}</button><button className="home-demo-approve" type="button" onClick={() => choose(4)}>{zh ? "批准演示" : "Approve demo"}<ArrowRight size={13} /></button></div></div> : null}
            {step === 4 ? <div className="home-demo-complete"><Check size={24} /><strong>{zh ? "从想法，到完成。" : "From a request to a result."}</strong><span>{zh ? "一次提交，每一步都看得见。" : "One commit. Every step in view."}</span><code>main → origin/main</code></div> : null}
          </div>}
        </div>
        <div className="home-demo-composer"><span>{zh ? "接下来想做什么？" : "What would you like to do next?"}</span><ArrowRight size={15} /></div>
      </div>
    </div>
    <div className="home-demo-footer"><span>{zh ? "示例场景 · 点击步骤亲自体验" : "Sample session · Click a step to explore"}</span><div><button type="button" aria-label={zh ? "重新播放演示" : "Replay demo"} onClick={replay}>↻</button>{!reduced || step >= 3 ? <button type="button" onClick={() => { if (step >= 3) replay(); else setPlaying(value => !value); }}>{step >= 3 ? (zh ? "重播" : "Replay") : playing ? (zh ? "暂停" : "Pause") : (zh ? "继续" : "Play")}</button> : null}{reduced && step < 3 ? <button type="button" onClick={() => choose(step + 1)}>{zh ? "下一步" : "Next"}</button> : null}</div></div>
  </div>;
}

function Diff({ zh }: { zh: boolean }) {
  return <div className="home-demo-diff"><div><span>release.py</span><span>{zh ? "修改预览" : "Change preview"}</span></div><pre><code><span>  def is_ready(version, current):</span>{"\n"}<span className="home-demo-removed">−     return version &gt; current</span>{"\n"}<span className="home-demo-passed">+     return version &gt;= current</span></code></pre></div>;
}
