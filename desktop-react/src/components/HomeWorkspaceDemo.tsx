import { useEffect, useRef, useState } from "react";
import { Check, FolderOpen, ArrowRight, PanelRight, Search, Pencil, ChevronRight, ChevronDown, Globe2, FileDiff, Terminal, Settings, Paperclip, KeyRound, SlidersHorizontal, Layers, CircleAlert, Archive } from "./icons";
import { ActivityGlyph } from "./ToolIdentity";
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
      <span className="home-demo-brand"><img src="/loom-icon.png" alt="" /> Loom <span>/ {zh ? "修复发布测试" : "Fix release test"}</span></span>
      <span className="home-demo-badge">{zh ? "交互演示" : "Interactive demo"}</span>
    </div>
    <div className="home-demo-workspace">
      <aside className="home-demo-sidebar" aria-label={zh ? "示例项目与对话" : "Sample projects and conversations"}>
        <div className="home-demo-sidebar-head"><PanelRight size={13} /><img src="/loom-icon.png" alt="" /><strong>Loom</strong><Search size={12} /><Pencil size={12} /></div>
        <span className="home-demo-eyebrow">{zh ? "项目" : "Projects"}</span>
        <div className="home-demo-project"><FolderOpen size={13} /> Loom</div>
        <div className="home-demo-threads"><span className="is-current">{zh ? "修复发布测试" : "Fix release test"}<span className="home-demo-thread-dot" /></span><span>{zh ? "梳理项目架构" : "Explore the project"}</span><span>{zh ? "整理更新日志" : "Release notes"}</span></div>
        <div className="home-demo-project home-demo-project-muted"><FolderOpen size={13} /> website</div>
        <div className="home-demo-threads"><span>{zh ? "更新首页" : "Update the home page"}</span></div>
        <span className="home-demo-local"><Archive size={12} />{zh ? "归档" : "Archive"}</span>
      </aside>
      <div className="home-demo-main">
        <div className="home-demo-tabs" role="group" aria-label={zh ? "演示面板" : "Demo panels"}>
          <button type="button" aria-label={zh ? "对话" : "Conversation"} aria-pressed={tab === "activity"} onClick={() => { setTab("activity"); setPlaying(false); }}>{zh ? "修复发布测试" : "Fix release test"}</button>
          <button type="button" aria-pressed={tab === "diff"} onClick={() => { setTab("diff"); setPlaying(false); }}>{zh ? "代码修改" : "Changes"}<span>1</span></button>
        </div>
        <div className="home-demo-content">
          {tab === "diff" ? <div className="home-demo-scene" key="diff"><Diff zh={zh} /><p className="home-demo-detail">{zh ? "把边界判断改为包含当前版本。" : "Include the current version in the release check."}</p></div> : <div className="home-demo-scene" key={`${run}-${step}`}>
            <div className="home-demo-prompt">{zh ? "修好发布测试，然后推送。" : "Fix the release test, then push it."}</div>
            <div className="home-demo-agent"><ActivityGlyph size={12} item={{ id: "demo-tool", threadId: "home-demo", type: step === 1 ? "file_edit" : step === 3 ? "approval" : "process", status: step < 3 ? "running" : "completed" }} /><span className="home-demo-status" role="status">{labels[step]}</span><span className="home-demo-process-time">· {step === 0 ? "1s" : "3s"}</span><ChevronRight size={10} /></div>
            <p className="home-demo-narration">{(zh ? ["先读一下项目，定位失败的测试。", "找到了：当前版本被边界条件排除了。", "修复已完成，现在验证测试。", "41 项测试全部通过。推送前，请你确认。", "已批准。修复已提交并推送。"] : ["I’ll read the project and trace the failing test.", "Found it. The boundary check excludes the current version.", "The fix is in. Let’s verify it with the tests.", "All 41 tests passed. Ready for your review before pushing.", "Approved. The fix is committed and pushed."])[step]}</p>
            {step === 0 ? <div className="home-demo-files"><span><FolderOpen size={14} /> release-fix /</span><code>release.py <span>124 {zh ? "行" : "lines"}</span></code><code>tests/test_release.py <span>86 {zh ? "行" : "lines"}</span></code><div className="home-demo-scanning"><i />{zh ? "正在追踪测试失败原因…" : "Tracing the failing assertion…"}</div></div> : null}
            {step >= 1 && step <= 3 ? <button type="button" className="home-demo-file-summary" onClick={() => { setTab("diff"); setPlaying(false); }}><span>{zh ? "已编辑" : "Edited"} <strong>release.py</strong></span><span><span className="home-demo-passed">+1</span> <span className="home-demo-removed">−1</span><ChevronRight size={11} /></span></button> : null}
            {step === 1 ? <Diff zh={zh} /> : null}
            {step === 2 ? <div className="home-demo-terminal"><span>TERMINAL</span><code>$ pytest tests/test_release.py</code><code className="home-demo-test-dots">.........................................</code><code className="home-demo-passed">41 passed in 0.82s</code><span>exit code 0</span></div> : null}
            {step === 3 ? <div className="home-demo-approval"><strong><CircleAlert size={14} />{zh ? "需要你的批准" : "Approval required"}</strong><span>{zh ? "终端 · 推送到远程仓库" : "Terminal · Push to the remote repository"}</span><code>git push origin main</code><div><button type="button" onClick={() => setTab("diff")}>{zh ? "查看修改" : "Review changes"}</button><button className="home-demo-approve" type="button" onClick={() => choose(4)}><Check size={12} />{zh ? "批准演示" : "Approve demo"}</button></div></div> : null}
            {step === 4 ? <div className="home-demo-complete"><Check size={24} /><strong>{zh ? "从想法，到完成。" : "From a request to a result."}</strong><span>{zh ? "一次提交，每一步都看得见。" : "One commit. Every step in view."}</span><code>main → origin/main</code></div> : null}
          </div>}
        </div>
        <div className="home-demo-composer"><span>{zh ? "描述任务，或提出问题…" : "Describe a task, or ask a question…"}</span><div className="home-demo-composer-tools"><SlidersHorizontal size={12} /><Paperclip size={12} /><span><FolderOpen size={11} />Loom</span><span><KeyRound size={11} />{zh ? "需要批准" : "Ask approval"}<ChevronDown size={9} /></span><span><Layers size={11} />{zh ? "当前模型" : "Model"}<ChevronDown size={9} /></span><ArrowRight size={14} /></div></div>
      </div>
      <div className="home-demo-dock" role="group" aria-label={zh ? "演示工具栏" : "Demo tool dock"}>
        <button type="button" aria-label={zh ? "查看文件" : "View files"} onClick={() => choose(0)}><FolderOpen size={14} /></button>
        <span><Globe2 size={14} /></span>
        <button type="button" aria-label={zh ? "查看代码修改" : "View code changes"} aria-pressed={tab === "diff"} onClick={() => { setTab("diff"); setPlaying(false); }}><FileDiff size={14} /><i>1</i></button>
        <button type="button" aria-label={zh ? "查看终端" : "View terminal"} onClick={() => choose(2)}><Terminal size={14} /></button>
        <span className="home-demo-dock-settings"><Settings size={14} /></span>
      </div>
    </div>
    <div className="home-demo-playback" role="group" aria-label={zh ? "演示步骤" : "Demo steps"}>{labels.slice(0, 4).map((label, index) => <button key={label} type="button" aria-pressed={step === index || (index === 3 && step === 4)} onClick={() => choose(index)}><span className="home-demo-step-icon">{index < step ? <Check size={11} /> : `0${index + 1}`}</span>{label}</button>)}</div>
    <div className="home-demo-footer"><span>{zh ? "示例场景 · 点击步骤亲自体验" : "Sample session · Click a step to explore"}</span><div><button type="button" aria-label={zh ? "重新播放演示" : "Replay demo"} onClick={replay}>↻</button>{!reduced || step >= 3 ? <button type="button" onClick={() => { if (step >= 3) replay(); else setPlaying(value => !value); }}>{step >= 3 ? (zh ? "重播" : "Replay") : playing ? (zh ? "暂停" : "Pause") : (zh ? "继续" : "Play")}</button> : null}{reduced && step < 3 ? <button type="button" onClick={() => choose(step + 1)}>{zh ? "下一步" : "Next"}</button> : null}</div></div>
  </div>;
}

function Diff({ zh }: { zh: boolean }) {
  return <div className="home-demo-diff"><div><span>release.py</span><span>{zh ? "修改预览" : "Change preview"}</span></div><pre><code><span>  def is_ready(version, current):</span>{"\n"}<span className="home-demo-removed">−     return version &gt; current</span>{"\n"}<span className="home-demo-passed">+     return version &gt;= current</span></code></pre></div>;
}
