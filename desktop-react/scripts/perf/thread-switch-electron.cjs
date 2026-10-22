// The thread-switch bench (thread-switch-bench.mjs) in a real Electron window: the GPU, the display's refresh rate, real
// input events. This is the one to quote; headless Chrome paints in software and is good for shapes, not milliseconds.
//   BENCH_ORIGIN=http://127.0.0.1:5199 [BENCH_QUERY="turns=25&tools=30&heavy=6&motion=1"] [BENCH_ORDER=1,2,3,4,5,1,2,3,4,5]
//   [BENCH_OUT=run.json] node_modules/electron/dist/electron.exe scripts/perf/thread-switch-electron.cjs
// The origin comes in the environment (Electron quits before running a script given an http:// argument). Serve a production
// build of scripts/fixtures/perf-session.html for absolute numbers (a Vite dev server runs React in development mode). The
// window sits almost entirely past the right edge of the screen; a 60 px sliver stays visible so Windows does not treat it as
// occluded and throttle its frames. Prints medians for the first time a conversation is opened and for opening it again.
const { app, BrowserWindow, screen } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const origin = process.env.BENCH_ORIGIN;
const order = (process.env.BENCH_ORDER || "1,2,3,4,5,1,2,3,4,5").split(",").map(Number);
const query = process.env.BENCH_QUERY || "turns=25&tools=30&heavy=6&motion=1";
app.setPath("userData", path.join(os.tmpdir(), "loom-thread-switch-electron"));
app.commandLine.appendSwitch("disable-features", "CalculateNativeWinOcclusion");
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const RECORDER = `(() => {
  const state = window.__sw = { frames: [], longtasks: [], events: [], active: false, target: "", before: "", clickAt: 0, swapAt: 0, feedbackAt: 0 };
  const tick = (now) => {
    if (state.active) {
      state.frames.push(now);
      if (!state.swapAt && state.clickAt) {
        const block = document.querySelector(".turn-block");
        const switching = document.querySelector(".workspace")?.classList.contains("is-thread-switching");
        if (block && block.dataset.turnId !== state.before && !switching) state.swapAt = now;
      }
      if (!state.feedbackAt && state.clickAt && state.target
        && document.querySelector(".compact-thread-row.active")?.dataset.threadId === state.target) state.feedbackAt = now;
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  new PerformanceObserver((list) => { for (const e of list.getEntries()) if (state.active) state.longtasks.push({ start: e.startTime, duration: e.duration }); }).observe({ entryTypes: ["longtask"] });
  document.addEventListener("click", (event) => { if (state.active && !state.clickAt) state.clickAt = event.timeStamp; }, true);
  state.begin = (target) => Object.assign(state, { frames: [], longtasks: [], target, before: document.querySelector(".turn-block")?.dataset.turnId, clickAt: 0, swapAt: 0, feedbackAt: 0, active: true });
  state.end = () => { state.active = false; return { frames: state.frames, longtasks: state.longtasks, clickAt: state.clickAt, swapAt: state.swapAt, feedbackAt: state.feedbackAt }; };
})()`;

app.whenReady().then(async () => {
  const display = screen.getPrimaryDisplay();
  const area = display.workArea;
  const win = new BrowserWindow({
    width: 1707, height: 1000, x: area.x + area.width - 60, y: area.y + 10, frame: false, show: false, skipTaskbar: true,
    backgroundColor: "#0d0e11", webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false },
  });
  const js = (code) => win.webContents.executeJavaScript(code);
  await win.webContents.session.clearCache();
  await win.loadURL(`${origin}/scripts/fixtures/perf-session.html?${query}`);
  win.setAlwaysOnTop(true, "floating");
  win.showInactive();
  await js(`localStorage.setItem("loom.sidebar.expandedProjects", JSON.stringify(["loom", "agent"]))`);
  await js(`new Promise((resolve) => { const ready = () => document.querySelectorAll(".turn-block").length >= 3 ? resolve(true) : setTimeout(ready, 50); ready(); })`);
  await wait(1500);
  await js(RECORDER);
  console.log(`display ${display.size.width}x${display.size.height} @${display.displayFrequency}Hz, scale ${display.scaleFactor}`);
  const rows = [];
  let current = 0;
  for (const id of order) {
    if (id === current) continue;
    const point = await js(`(() => {
      const row = document.querySelector('.compact-thread-row[data-thread-id="perf-${id}"] .compact-thread-main');
      row.scrollIntoView({ block: "nearest" });
      const box = row.getBoundingClientRect();
      return { x: Math.round(box.left + Math.min(44, box.width / 4)), y: Math.round(box.top + box.height / 2) };
    })()`);
    win.webContents.sendInputEvent({ type: "mouseMove", x: point.x, y: point.y });
    await wait(120);
    await js(`void window.__sw.begin("perf-${id}")`);
    win.webContents.sendInputEvent({ type: "mouseDown", x: point.x, y: point.y, button: "left", clickCount: 1 });
    await wait(16);
    win.webContents.sendInputEvent({ type: "mouseUp", x: point.x, y: point.y, button: "left", clickCount: 1 });
    await js(`new Promise((resolve) => { const t0 = performance.now(); const ready = () => window.__sw.swapAt > 0 || performance.now() - t0 > 20000 ? resolve(true) : setTimeout(ready, 16); ready(); })`);
    await wait(700);
    const record = await js(`window.__sw.end()`);
    const t0 = record.clickAt;
    const frames = record.frames.filter((time) => time >= t0 - 20);
    const gaps = frames.slice(1).map((time, index) => time - frames[index]);
    const swapFrame = frames.find((time) => time >= record.swapAt);
    const paint = (frames[frames.indexOf(swapFrame) + 1] ?? swapFrame) - t0;
    const feedbackFrame = frames.find((time) => time >= record.feedbackAt);
    const feedback = (frames[frames.indexOf(feedbackFrame) + 1] ?? feedbackFrame) - t0;
    const tasks = record.longtasks.filter((task) => task.start >= t0);
    const gapList = []; for (let i = 1; i < frames.length; i += 1) if (frames[i] - frames[i - 1] > 12) gapList.push([Math.round(frames[i - 1] - t0), Math.round(frames[i] - frames[i - 1])]); rows.push({ id, gapList, feedback, paint, longest: Math.max(0, ...tasks.map((task) => task.duration)), worstGap: Math.max(0, ...gaps), slow: gaps.filter((gap) => gap > 12).length });
    current = id;
  }
  const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const summary = Object.fromEntries(["feedback", "paint", "longest", "worstGap", "slow"].map((key) => [key, Math.round(median(rows.map((row) => row[key])))]));
  const seen = new Set();
  const groups = { first: [], again: [] };
  for (const row of rows) { (seen.has(row.id) ? groups.again : groups.first).push(row); seen.add(row.id); }
  const per = Object.fromEntries(Object.entries(groups).map(([name, list]) => [name, Object.fromEntries(["feedback", "paint", "longest", "worstGap"].map((key) => [key, Math.round(median(list.map((row) => row[key])))]))]));
  console.log(JSON.stringify({ origin, clicks: rows.length, summary, per }));
  if (process.env.BENCH_OUT) fs.writeFileSync(process.env.BENCH_OUT, JSON.stringify({ rows, summary }, null, 1));
  app.quit();
});
