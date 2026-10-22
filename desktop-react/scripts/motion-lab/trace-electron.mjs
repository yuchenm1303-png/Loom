// Motion lab: where the main thread goes between an input and the frame that answers it, in a real Electron window
// (production build of the live-app fixture). Records a Chrome trace around one click and prints the renderer main
// thread's tasks from the input on: script, style recalculation, layout, paint, and what else ran, so a slow first
// frame says what it was spent on.
//   TRACE_ORIGIN=http://127.0.0.1:5192 TRACE_CLICK='.loom-tool-button[data-tool-id="settings"]' [TRACE_PREP='sel1||sel2']
//   [TRACE_MS=700] [TRACE_THEME=dark] [TRACE_OUT=trace.json] node_modules/electron/dist/electron.exe scripts/motion-lab/trace-electron.mjs
// The origin comes in the environment (Electron quits before running a script given an http:// argument).
import { app, BrowserWindow, screen } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const origin = process.env.TRACE_ORIGIN;
const target = process.env.TRACE_CLICK;
const prep = (process.env.TRACE_PREP || "").split("||").filter(Boolean);
const span = Number(process.env.TRACE_MS || 700);
const theme = process.env.TRACE_THEME || "dark";
app.setPath("userData", path.join(os.tmpdir(), "loom-trace-electron"));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  const area = screen.getPrimaryDisplay().workArea;
  const win = new BrowserWindow({
    width: 1440, height: 900, x: area.x + area.width - 60, y: area.y + 20, frame: false, show: false, skipTaskbar: true,
    backgroundColor: theme === "light" ? "#f7f7f8" : "#0d0e11",
    webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false },
  });
  await win.loadURL(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=en`);
  win.showInactive();
  const js = (code) => win.webContents.executeJavaScript(code);
  await js(`new Promise((resolve) => { const ready = () => document.querySelector(".thread-header") ? resolve(true) : setTimeout(ready, 50); ready(); })`);
  await wait(1800);
  const click = async (selector) => {
    const p = await js(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
    if (!p) throw new Error(`not found: ${selector}`);
    win.webContents.sendInputEvent({ type: "mouseMove", x: p.x, y: p.y });
    await wait(120);
    win.webContents.sendInputEvent({ type: "mouseDown", x: p.x, y: p.y, button: "left", clickCount: 1 });
    await wait(16);
    win.webContents.sendInputEvent({ type: "mouseUp", x: p.x, y: p.y, button: "left", clickCount: 1 });
  };
  for (const selector of prep) { await click(selector); await wait(900); }

  const dbg = win.webContents.debugger;
  dbg.attach("1.3");
  const events = [];
  let done;
  const complete = new Promise((resolve) => { done = resolve; });
  dbg.on("message", (_event, method, params) => {
    if (method === "Tracing.dataCollected") events.push(...params.value);
    if (method === "Tracing.tracingComplete") done();
  });
  await dbg.sendCommand("Tracing.start", {
    traceConfig: { includedCategories: ["devtools.timeline", "disabled-by-default-devtools.timeline", "blink.user_timing", "v8.execute"] },
    transferMode: "ReportEvents",
  });
  await wait(200);
  const marker = await js("performance.now()");
  await click(target);
  await wait(span);
  await dbg.sendCommand("Tracing.end");
  await complete;
  dbg.detach();
  if (process.env.TRACE_OUT) fs.writeFileSync(process.env.TRACE_OUT, JSON.stringify(events));

  // The renderer's main thread, from the input on.
  const threadNames = new Map(events.filter((e) => e.name === "thread_name").map((e) => [`${e.pid}:${e.tid}`, e.args.name]));
  const main = [...threadNames].find(([, name]) => name === "CrRendererMain")?.[0];
  const input = events.find((e) => e.name === "EventDispatch" && /mousedown|pointerdown/.test(e.args?.data?.type ?? "") && `${e.pid}:${e.tid}` === main);
  const t0 = input ? input.ts : Math.min(...events.filter((e) => e.ts).map((e) => e.ts));
  const onMain = events.filter((e) => `${e.pid}:${e.tid}` === main && e.ph === "X" && e.ts >= t0 - 1000);
  const tasks = onMain.filter((e) => e.name === "RunTask" || e.name === "ThreadControllerImpl::RunTask").sort((a, b) => a.ts - b.ts);
  const kinds = ["FunctionCall", "EventDispatch", "TimerFire", "FireAnimationFrame", "RunMicrotasks", "UpdateLayoutTree", "Layout", "PrePaint", "Paint", "Layerize", "CommitStyles", "ParseHTML", "EvaluateScript", "v8.compile", "HitTest", "ScheduleStyleRecalculation"];
  console.log(`input at 0 ms; main-thread tasks over 3 ms in the next ${span} ms:`);
  for (const task of tasks) {
    const at = (task.ts - t0) / 1000;
    const dur = task.dur / 1000;
    if (dur < 3 || at < -1 || at > span) continue;
    const inner = onMain.filter((e) => e.ts >= task.ts && e.ts + e.dur <= task.ts + task.dur && kinds.includes(e.name));
    const sum = new Map();
    for (const e of inner) {
      // Count only the outermost event of each kind (nested FunctionCalls would double).
      const outer = inner.some((o) => o !== e && o.name === e.name && o.ts <= e.ts && o.ts + o.dur >= e.ts + e.dur);
      if (!outer) sum.set(e.name, (sum.get(e.name) || 0) + e.dur / 1000);
    }
    const parts = [...sum].sort((a, b) => b[1] - a[1]).filter(([, ms]) => ms >= .5).map(([name, ms]) => `${name} ${ms.toFixed(1)}`).join(", ");
    const style = inner.filter((e) => e.name === "UpdateLayoutTree").map((e) => e.args?.elementCount).filter(Boolean);
    console.log(`  ${at.toFixed(1).padStart(7)} ms  ${dur.toFixed(1).padStart(6)} ms  ${parts}${style.length ? `  (styled ${style.join("+")} elements)` : ""}`);
    if (process.env.TRACE_LAYOUT) {
      for (const layout of inner.filter((e) => e.name === "Layout" && e.dur >= 1500)) {
        const begin = layout.args?.beginData ?? {};
        console.log(`             layout ${(layout.dur / 1000).toFixed(1)} ms: ${begin.dirtyObjects ?? "?"} of ${begin.totalObjects ?? "?"} objects dirty${begin.partialLayout ? ", partial" : ""}${layout.args?.beginData?.stackTrace ? ` from ${layout.args.beginData.stackTrace.slice(0, 3).map((f) => f.functionName || "?").join(" < ")}` : ""}`);
      }
    }
  }
  const frames = events.filter((e) => e.name === "DrawFrame" || e.name === "Commit").filter((e) => e.ts >= t0).map((e) => ((e.ts - t0) / 1000).toFixed(0));
  console.log(`commits/frames after the input: ${frames.slice(0, 20).join(" ")}`);
  console.log(`(marker ${marker.toFixed(0)})`);
  app.quit();
});
