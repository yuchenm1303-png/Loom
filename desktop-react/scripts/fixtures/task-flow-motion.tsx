/**
 * Live transcript motion fixture.
 *
 * Serves the real Transcript / RunProgress components under the renderer's
 * full stylesheet cascade (same import order as src/main.tsx) and replays a
 * scripted live turn, so conversation motion can be judged frame by frame
 * without a runtime.
 *
 *   cd desktop-react && npx vite --port 5199
 *   open http://127.0.0.1:5199/scripts/fixtures/task-flow-motion.html
 *
 *   ?theme=light|dark      initial theme (default light)
 *   ?autoplay=0            wait for Replay (window.__motion.replay() also works)
 *   ?mode=film&case=group|row|settle|handoff|thinking|reasoning|transient|answer&frames=0,60,120
 *                          freeze one entrance/transition at several times, stacked
 *   ?gap=ms                film: override the delay before a case's final step
 *   ?zoom=1.5              magnify for inspection
 *   ?raf=timeout           keep the rAF-paced stream reveal moving in a hidden page
 */
import { StrictMode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
// App is imported for its stylesheet side effects only, so the cascade order
// matches the renderer (App subtree first, then main.tsx's own list).
import "../../src/App";
import "../../src/components/BootErrorBoundary";
import "../../src/components/GlobalContextMenu";
import { I18nProvider } from "../../src/i18n";
import { applyThemePreference } from "../../src/theme";
import "../../src/styles.css";
import "../../src/shell-fix.css";
import "../../src/components/model-panel-overrides.css";
import "../../src/components/inline-thinking.css";
import "../../src/components/composer-stability.css";
import "../../src/components/composer-attachment-polish.css";
import "../../src/typography-scale.css";
import "../../src/components/settings-simple.css";
import "../../src/components/settings-icon-alignment.css";
import "../../src/components/computer-use-hud.css";
import "../../src/components/inspector-tabs.css";
import "../../src/components/semantic-colors.css";
import "../../src/components/thread-header-mark-refinement.css";
import "../../src/components/settings-models-polish.css";
import "../../src/components/settings-capabilities-polish.css";
import "../../src/components/review-motion.css";
import "../../src/components/sidebar-clarity-fix.css";
import "../../src/components/renderer-crispness.css";
import "../../src/components/model-core-redesign.css";
import "../../src/theme.css";
import "../../src/components/sidebar-primary-actions-polish.css";
import "../../src/components/permission-popover-polish.css";
import "../../src/components/model-picker.css";
import "../../src/components/composer-control-pills.css";
import "../../src/components/project-details-panel-theme.css";
import "../../src/global-motion.css";
import "../../src/components/generation-motion.css";
import { RunProgress } from "../../src/components/RunProgress";
import { Transcript } from "../../src/components/Transcript";
import { TranscriptScrollController } from "../../src/components/TranscriptScrollController";
import type { TranscriptItem } from "../../src/types/loom";

const THREAD = "thread-motion";
const LIVE_TURN = "turn-live";
const params = new URLSearchParams(window.location.search);

interface Scene {
  items: TranscriptItem[];
  running: boolean;
  startedAt: number | null;
}

class Cancelled extends Error {}

// ?raf=timeout keeps the streaming reveal (rAF-paced) progressing while the
// page is hidden, e.g. when driven headlessly. Visual timing is then coarse.
if (params.get("raf") === "timeout") {
  window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 16);
  window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
}

const root = document.documentElement;
root.style.setProperty("--content-width", "860px");
root.style.setProperty("--loom-chat-font-size", "13px");
root.style.setProperty("--loom-message-line-height", "1.72");
root.style.setProperty("--loom-code-font", "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace");
root.style.setProperty("--loom-code-font-size", "12px");
root.style.setProperty("--loom-code-line-height", "1.62");
root.dataset.loomReducedMotion = "false";
if (params.get("zoom")) document.body.style.zoom = params.get("zoom")!;
root.dataset.loomDensity = "comfortable";
root.dataset.loomAmbientEffects = "true";
applyThemePreference(params.get("theme") === "dark" ? "dark" : "light", { animate: false, syncNative: false });

const harnessCss = document.createElement("style");
harnessCss.textContent = `
  .motion-harness { height: 100%; display: grid; grid-template-rows: auto minmax(0, 1fr); background: var(--bg); }
  .motion-bar { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 7px 12px; border-bottom: 1px solid var(--border-soft); font: 12px/1.2 "Segoe UI", sans-serif; color: var(--muted); }
  .motion-bar button { font: inherit; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border); background: var(--panel); color: var(--text-2); cursor: pointer; }
  .motion-bar button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
  .motion-bar .spacer { flex: 1; }
  .motion-film { overflow: auto; padding: 8px 0 40px; }
  .motion-film-frame { position: relative; display: grid; grid-template-columns: 70px minmax(0, 1fr); align-items: start; border-bottom: 1px dashed var(--border-soft); }
  .motion-film-frame > b { padding: 10px 0 0 12px; font: 600 11px/1 ui-monospace, Consolas, monospace; color: var(--muted); }
  .motion-film-frame .conversation-stage { display: block; overflow: visible; }
  .motion-film-frame .transcript { min-height: 0; padding: 6px 0 8px; width: min(760px, calc(100% - 20px)); margin: 0; gap: 8px; }
  .motion-film-frame .transcript-scroll { overflow: visible; contain: none !important; background: transparent; }
  .motion-film-frame .chat-ambient { display: none; }
  .motion-film-frame .entry-user_message { display: none; }
  .motion-film-frame .turn-block { gap: 6px; }
`;
document.head.appendChild(harnessCss);

function stamp(offsetMs = 0): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

function historicalTurn(): TranscriptItem[] {
  const t0 = Date.now() - 8 * 60_000;
  const at = (seconds: number) => new Date(t0 + seconds * 1000).toISOString();
  const turnId = "turn-history";
  return [
    { id: "h-user", threadId: THREAD, turnId, type: "user_message", status: "completed", text: "先看看 C 盘现在的占用情况", createdAt: at(0) },
    { id: "h-a0", threadId: THREAD, turnId, type: "assistant_message", phase: "commentary", status: "completed", text: "先读一下磁盘占用。", createdAt: at(2), updatedAt: at(3) },
    { id: "h-p1", threadId: THREAD, turnId, type: "process", status: "completed", argv: ["powershell", "-NoProfile", "-Command", "Get-PSDrive C | Select-Object Used,Free"], stdout: "Used          Free\n----          ----\n172875743232  42305699840", createdAt: at(3), updatedAt: at(5) },
    { id: "h-a1", threadId: THREAD, turnId, type: "assistant_message", phase: "final_answer", status: "completed", text: "C 盘总量 200GB，已用 161GB，剩余 **39.4GB**。", createdAt: at(6), updatedAt: at(9) },
  ];
}

const COMMANDS = {
  drive: ["powershell", "-NoProfile", "-Command", "Get-PSDrive C | Select-Object Used,Free,@{n='TotalGB';e={[math]::Round(($_.Used+$_.Free)/1GB,1)}}"],
  scan: ["powershell", "-NoProfile", "-Command", "$paths=@('C:\\Windows\\Temp','C:\\Users\\demo\\AppData\\Local\\Temp','C:\\Windows\\SoftwareDistribution\\Download'); foreach($p in $paths){ '{0}  {1:N1} GB' -f $p,((Get-ChildItem $p -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum/1GB) }"],
  stopWu: ["powershell", "-NoProfile", "-Command", "Write-Host '=== Stage 1: stop Windows Update service ==='; Stop-Service wuauserv -Force; Get-Service wuauserv"],
  clean: ["powershell", "-NoProfile", "-Command", "function Clean-Dir($p){ if(Test-Path $p){$s=(Get-ChildItem $p -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum; Remove-Item \"$p\\*\" -Recurse -Force -ErrorAction SilentlyContinue; '{0} freed {1:N2} GB' -f $p,($s/1GB)} }; Clean-Dir 'C:\\Windows\\Temp'"],
  recycle: ["powershell", "-NoProfile", "-Command", "Write-Host '=== Empty Recycle Bin ==='; Clear-RecycleBin -DriveLetter C -Force -ErrorAction SilentlyContinue; Write-Host 'done'"],
};

type Update = (fn: (scene: Scene) => Scene) => void;

class Director {
  token = 0;
  speed = 1;
  paused = false;

  constructor(private readonly update: Update) {}

  private async wait(ms: number, token: number): Promise<void> {
    let left = ms;
    while (left > 0) {
      const step = Math.min(left, 16);
      await new Promise((resolve) => window.setTimeout(resolve, step / this.speed));
      if (token !== this.token) throw new Cancelled();
      if (!this.paused) left -= step;
    }
  }

  private upsert(item: Partial<TranscriptItem> & { id: string }): void {
    this.update((scene) => {
      const index = scene.items.findIndex((entry) => entry.id === item.id);
      if (index < 0) {
        return { ...scene, items: [...scene.items, { threadId: THREAD, turnId: LIVE_TURN, createdAt: stamp(), ...item } as TranscriptItem] };
      }
      const items = [...scene.items];
      items[index] = { ...items[index], ...item, updatedAt: stamp() };
      return { ...scene, items };
    });
  }

  private async stream(id: string, field: "text" | "reasoning", full: string, token: number): Promise<void> {
    let shown = "";
    const graphemes = Array.from(full);
    while (shown.length < full.length) {
      const next = graphemes.slice(Array.from(shown).length, Array.from(shown).length + 3).join("");
      shown += next;
      this.upsert({ id, [field]: shown });
      await this.wait(42, token);
    }
  }

  private async command(id: string, argv: string[], ms: number, token: number, stdout = "done"): Promise<void> {
    // Real runtime order: the exec wrapper starts ~30ms before its process item.
    this.upsert({ id: `${id}-exec`, type: "tool_call", toolName: "exec", status: "running", arguments: { argv } });
    await this.wait(30, token);
    this.upsert({ id, type: "process", status: "running", argv });
    await this.wait(ms, token);
    this.upsert({ id, status: "completed", stdout });
    this.upsert({ id: `${id}-exec`, status: "completed", content: `exit=0\nstdout:\n${stdout}` });
  }

  async play(): Promise<void> {
    const token = ++this.token;
    this.update(() => ({ items: historicalTurn(), running: false, startedAt: null }));
    try {
      await this.wait(700, token);
      this.update((scene) => ({
        ...scene,
        running: true,
        startedAt: Date.now(),
        items: [...scene.items, { id: "u1", threadId: THREAD, turnId: LIVE_TURN, type: "user_message", status: "completed", text: "帮我把 C 盘能安全清理的都清一下，每一步告诉我腾出了多少", createdAt: stamp() }],
      }));
      await this.wait(1300, token);

      this.upsert({ id: "a1", type: "assistant_message", phase: "commentary", status: "streaming", text: "", reasoning: "" });
      await this.stream("a1", "reasoning", "用户想清理 C 盘。先确认磁盘总量和剩余空间，再挑能放心删除的缓存目录。", token);
      await this.stream("a1", "text", "控制台对中文编码不太友好，切到 PowerShell 看一下准确数据。[[AI_LEDGER_INLINE_STICKER:thinking_soft]]", token);
      this.upsert({ id: "a1", status: "completed" });
      await this.wait(380, token);

      await this.command("p1", COMMANDS.drive, 1100, token, "Used 172875743232  Free 42305699840  TotalGB 200");
      await this.wait(220, token);
      await this.command("p2", COMMANDS.scan, 1500, token, "C:\\Windows\\Temp  3.2 GB\nLocal\\Temp  1.9 GB\nSoftwareDistribution\\Download  2.4 GB");
      await this.wait(500, token);

      this.upsert({ id: "a2", type: "assistant_message", phase: "commentary", status: "streaming", text: "", reasoning: "" });
      await this.stream("a2", "reasoning", "四个目录都可以安全清理。Windows Update 服务可能占用下载缓存，先停服务。", token);
      await this.stream("a2", "text", "C 盘 200GB，已用 161GB，剩余 39.4GB。下面这几个常见缓存点能腾出几 GB，而且都可以放心删：先停掉 Windows Update 服务避免占用文件，然后清理、再恢复服务。[[AI_LEDGER_INLINE_STICKER:confident_ready]]", token);
      this.upsert({ id: "a2", status: "completed" });
      await this.wait(380, token);

      this.upsert({ id: "t-fail", type: "tool_call", toolName: "exec", status: "running", arguments: { argv: ["sc", "stop", "wuauserv"] } });
      await this.wait(700, token);
      this.upsert({ id: "t-fail", status: "failed", content: "Access is denied." });
      await this.wait(260, token);
      await this.command("p3", COMMANDS.stopWu, 1000, token, "=== Stage 1: stop Windows Update service ===\nStopped  wuauserv");
      await this.wait(420, token);

      this.upsert({ id: "a3", type: "assistant_message", phase: "commentary", status: "streaming", text: "" });
      await this.stream("a3", "text", "服务已停。[[AI_LEDGER_INLINE_STICKER:got_it_point]] 现在按顺序清理临时文件，每一步都报一下释放了多少。", token);
      this.upsert({ id: "a3", status: "completed" });
      await this.wait(360, token);

      await this.command("p4", COMMANDS.clean, 1300, token, "C:\\Windows\\Temp freed 3.18 GB");
      await this.wait(200, token);
      this.upsert({ id: "f1", type: "file_edit", status: "running", paths: ["cleanup-report.md"] });
      await this.wait(700, token);
      this.upsert({ id: "f1", status: "changed", diff: "+# C 盘清理记录\n+- Windows\\Temp: 3.18 GB\n+- 回收站: 待清理\n-旧记录" });
      await this.wait(160, token);
      await this.command("p5", COMMANDS.recycle, 1700, token, "=== Empty Recycle Bin ===\ndone");
      await this.wait(600, token);

      this.upsert({ id: "a4", type: "assistant_message", phase: "final_answer", status: "streaming", text: "" });
      await this.stream("a4", "text", "清理完成，一共腾出 **7.4 GB**：\n\n- `C:\\Windows\\Temp`：3.18 GB\n- 用户 Temp：1.9 GB\n- Windows Update 下载缓存：2.3 GB\n\n回收站也已清空，Windows Update 服务已经恢复运行。", token);
      this.upsert({ id: "a4", status: "completed" });
      await this.wait(260, token);
      this.update((scene) => ({ ...scene, running: false, startedAt: null }));
    } catch (error) {
      if (!(error instanceof Cancelled)) throw error;
    }
  }
}

function useAnimationRate(speed: number): void {
  useEffect(() => {
    let frame = 0;
    const tick = () => {
      for (const animation of document.getAnimations()) {
        if (animation.playbackRate !== speed) animation.playbackRate = speed;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [speed]);
}

function LiveHarness() {
  const [scene, setScene] = useState<Scene>({ items: historicalTurn(), running: false, startedAt: null });
  const [speed, setSpeed] = useState(1);
  const [theme, setTheme] = useState(root.dataset.loomTheme === "dark" ? "dark" : "light");
  const [reduced, setReduced] = useState(false);
  const directorRef = useRef<Director | null>(null);
  directorRef.current ??= new Director(setScene);
  useAnimationRate(speed);

  useEffect(() => {
    const director = directorRef.current!;
    (window as unknown as { __motion: unknown }).__motion = {
      replay: () => void director.play(),
      setSpeed: (value: number) => { director.speed = value; setSpeed(value); },
      pause: (value = true) => { director.paused = value; },
      scene: () => scene,
    };
  });

  useEffect(() => {
    if (params.get("autoplay") !== "0") void directorRef.current!.play();
  }, []);

  const liveTurn = scene.running ? LIVE_TURN : null;

  return (
    <div className="motion-harness">
      <div className="motion-bar">
        <button type="button" onClick={() => void directorRef.current!.play()}>▶ Replay</button>
        {[1, 0.5, 0.25, 0.1].map((value) => (
          <button key={value} type="button" aria-pressed={speed === value} onClick={() => { directorRef.current!.speed = value; setSpeed(value); }}>{value}×</button>
        ))}
        <span className="spacer" />
        <button type="button" onClick={() => {
          const next = theme === "light" ? "dark" : "light";
          applyThemePreference(next, { animate: false, syncNative: false });
          setTheme(next);
        }}>{theme === "light" ? "☀ light" : "☾ dark"}</button>
        <button type="button" aria-pressed={reduced} onClick={() => {
          root.dataset.loomReducedMotion = String(!reduced);
          setReduced(!reduced);
        }}>reduced motion</button>
      </div>
      <div className={`conversation-stage ${scene.running ? "is-running" : ""}`}>
        {scene.running ? (
          <RunProgress items={scene.items} startedAt={scene.startedAt} threadStatus="running" currentTurnId={liveTurn} totalTokens={18_400} placement="top" />
        ) : null}
        <Transcript items={scene.items} running={scene.running} currentTurnId={liveTurn} workspace="C:\\demo" onApproval={() => {}} onPrompt={() => {}} />
        <TranscriptScrollController items={scene.items} threadId={THREAD} currentTurnId={liveTurn} running={scene.running} />
      </div>
    </div>
  );
}

/* Filmstrip: one frozen copy per requested time. A case with `steps` mounts,
   then re-renders with each step's items after its delay (ms); only animations
   born after the initial mount are scrubbed, so transitions can be inspected. */
interface FilmStep {
  after: number;
  items: TranscriptItem[];
}

interface FilmCase {
  items: TranscriptItem[];
  steps?: FilmStep[];
  running: boolean;
  focus: string;
}

const live = (item: Partial<TranscriptItem> & { id: string; type: string }): TranscriptItem => ({ threadId: THREAD, turnId: LIVE_TURN, status: "completed", ...item });
const FILM_USER = live({ id: "u", type: "user_message", text: "清理 C 盘" });
const FILM_P1_DONE = live({ id: "p1", type: "process", argv: COMMANDS.drive, stdout: "ok" });
const FILM_REASONING = "先确认磁盘总量和剩余空间，再挑能放心删除的缓存目录。";
const FILM_CASES: Record<string, FilmCase> = {
  group: {
    running: true,
    focus: ".task-flow-group",
    items: [FILM_USER, live({ id: "p1", type: "process", status: "running", argv: COMMANDS.drive })],
  },
  row: {
    running: true,
    focus: ".task-flow-row-wrap:last-child",
    items: [FILM_USER, FILM_P1_DONE, live({ id: "p2", type: "process", status: "running", argv: COMMANDS.recycle })],
  },
  settle: {
    running: true,
    focus: ".task-flow-row-wrap:last-child",
    items: [FILM_USER, live({ id: "p1", type: "process", status: "running", argv: COMMANDS.drive })],
    steps: [{ after: 120, items: [FILM_USER, FILM_P1_DONE] }],
  },
  handoff: {
    running: true,
    focus: ".task-flow-group",
    items: [FILM_USER, FILM_P1_DONE],
    steps: [{ after: 120, items: [FILM_USER, FILM_P1_DONE, live({ id: "a1", type: "assistant_message", phase: "commentary", text: "C 盘剩余 39.4GB。" })] }],
  },
  thinking: {
    running: true,
    focus: ".inline-thinking",
    items: [FILM_USER],
  },
  // The pending capsule has been on screen: reasoning continues it in place.
  reasoning: {
    running: true,
    focus: ".turn-process",
    items: [FILM_USER],
    steps: [{ after: 900, items: [FILM_USER, live({ id: "a1", type: "assistant_message", phase: "commentary", status: "streaming", text: "", reasoning: FILM_REASONING })] }],
  },
  // Runtime order after a tool: an empty streaming item, its first delta one
  // presentation frame later. The pending capsule mounted for that frame was
  // never seen, so the reasoning capsule must be born, not handed off.
  transient: {
    running: true,
    focus: ".turn-process .live-reasoning",
    items: [FILM_USER, FILM_P1_DONE],
    steps: [
      { after: 900, items: [FILM_USER, FILM_P1_DONE, live({ id: "a1", type: "assistant_message", phase: "commentary", status: "streaming", text: "" })] },
      { after: 28, items: [FILM_USER, FILM_P1_DONE, live({ id: "a1", type: "assistant_message", phase: "commentary", status: "streaming", text: "", reasoning: FILM_REASONING })] },
    ],
  },
  answer: {
    running: true,
    focus: ".turn-process",
    items: [FILM_USER, live({ id: "a1", type: "assistant_message", phase: "commentary", status: "streaming", text: "", reasoning: "先确认磁盘总量和剩余空间。" })],
    // Completed, so the whole answer (and its sticker) mounts in one frame.
    steps: [{ after: 120, items: [FILM_USER, live({ id: "a1", type: "assistant_message", phase: "commentary", text: "控制台对中文编码不太友好，切到 PowerShell 看一下准确数据。[[AI_LEDGER_INLINE_STICKER:thinking_soft]]", reasoning: "先确认磁盘总量和剩余空间。" })] }],
  },
};

function FilmFrame({ time, spec }: { time: number; spec: FilmCase }) {
  const ref = useRef<HTMLDivElement>(null);
  const steps = spec.steps ?? [];
  const [step, setStep] = useState(0);
  const priorRef = useRef<Set<Animation>>(new Set());
  const bornRef = useRef(new Map<Animation, number>());
  const lastStepAtRef = useRef(0);

  useLayoutEffect(() => {
    const host = ref.current;
    if (!host) return;
    if (step < steps.length) {
      const timer = window.setTimeout(() => {
        // Nominal time, not wall time: hidden pages throttle timers to ~1s.
        const now = lastStepAtRef.current + steps[step].after;
        if (step === 0) {
          // The initial mount's entrances are settled out of the way.
          priorRef.current = new Set(host.getAnimations({ subtree: true }));
          for (const animation of priorRef.current) {
            animation.pause();
            animation.currentTime = 60_000;
          }
        } else {
          // Motion started by an intermediate step sits exactly where real
          // time would have taken it, even when the page is hidden and the
          // document timeline is not advancing.
          for (const animation of host.getAnimations({ subtree: true })) {
            if (priorRef.current.has(animation)) continue;
            if (!bornRef.current.has(animation)) bornRef.current.set(animation, lastStepAtRef.current);
            animation.pause();
            animation.currentTime = now - bornRef.current.get(animation)!;
          }
        }
        lastStepAtRef.current = now;
        setStep(step + 1);
      }, steps[step].after);
      return () => window.clearTimeout(timer);
    }
    const freeze = () => {
      const focus = host.querySelector(spec.focus);
      for (const animation of host.getAnimations({ subtree: true })) {
        const target = (animation.effect as KeyframeEffect | null)?.target as Element | null;
        const inFocus = Boolean(focus && target && (focus === target || focus.contains(target)));
        animation.pause();
        animation.currentTime = inFocus && !priorRef.current.has(animation) ? time : 60_000;
      }
    };
    freeze();
    const late = window.setTimeout(freeze, 60);
    return () => window.clearTimeout(late);
  }, [spec, step, steps, time]);

  return (
    <div className="motion-film-frame" data-step={step}>
      <b>{time}ms</b>
      <div ref={ref} className="conversation-stage">
        <Transcript items={step ? steps[step - 1].items : spec.items} running={spec.running} currentTurnId={spec.running ? LIVE_TURN : null} onApproval={() => {}} />
      </div>
    </div>
  );
}

function FilmHarness() {
  const base = FILM_CASES[params.get("case") ?? "group"] ?? FILM_CASES.group;
  // ?gap=ms overrides the delay before the final step.
  const gap = Number(params.get("gap"));
  const spec = useState<FilmCase>(() => (base.steps && Number.isFinite(gap) && params.has("gap")
    ? { ...base, steps: base.steps.map((step, index, all) => (index === all.length - 1 ? { ...step, after: gap } : step)) }
    : base))[0];
  const frames = (params.get("frames") ?? "0,40,80,120,160,220,300,420,600")
    .split(",")
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isFinite(value));
  return (
    <div className="motion-harness" style={{ gridTemplateRows: "minmax(0,1fr)" }}>
      <div className="motion-film">
        {frames.map((time) => <FilmFrame key={time} time={time} spec={spec} />)}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      {params.get("mode") === "film" ? <FilmHarness /> : <LiveHarness />}
    </I18nProvider>
  </StrictMode>,
);
