// What a click on another conversation costs the renderer, on the real App with the in-memory host of
// scripts/fixtures/perf-session.tsx (no model, no Host process): from the click to the new conversation on screen,
// how long the main thread was busy, and how long the page stopped painting.
//
//   LOOM_PLAYWRIGHT_MODULE=file:///…/playwright-core/index.mjs \
//   LOOM_CHROMIUM_PATH="C:/Program Files/Google/Chrome/Application/chrome.exe" \
//   LOOM_TEST_ORIGIN=http://127.0.0.1:5199 \
//   node scripts/perf/thread-switch-bench.mjs [cold|warm|ping|hover ...] [--query "turns=25&tools=30"] [--read-ms 0] [--json out.json]
//
// Run it against a production build of the fixture for absolute numbers (React in development mode is several times
// slower): `vite build` with scripts/fixtures/perf-session.html as an input, then any static server.
// Headless Chrome paints in software: it is good for shapes and for comparing two builds, not for milliseconds. The same
// clicks in a real window (the GPU, the display's refresh rate) are thread-switch-electron.cjs, which is the one to quote.
//
// LOOM_BENCH_HEADED=1       real GPU compositing and display refresh (opens a window)
//
// Scenarios (the first conversation is open at the start; ids are perf-0 … perf-5, each with a history of its own)
//   cold    perf-1 … perf-5 in turn, none visited before: a read, the apply and the first render each time.
//   warm    the same five again in another order, straight after `cold`: whatever the page kept.
//   ping    perf-0 and perf-1 alternately, eight clicks.
//   hover   like `cold`, but the pointer rests on the row for --hover ms (default 250) before the click, as a hand does.
//   custom  --order 6,3,6,3 : your own sequence (conversation numbers); --hover ms to rest the pointer first.
//
// Per click, times are from the click event. `swap` is the first frame in which the new conversation's turns are in the
// page (the first turn on screen is another one); `paint` adds that frame; `idle` is the last long task or dropped frame that follows (a conversation that arrives
// and then keeps the page busy for another half second is not "loaded").
import assert from "node:assert/strict";
import fs from "node:fs";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const page_path = process.env.LOOM_BENCH_PAGE || "/scripts/fixtures/perf-session.html";
const headed = process.env.LOOM_BENCH_HEADED === "1";
const argv = process.argv.slice(2);
const flag = (name, fallback) => {
  const at = argv.indexOf(`--${name}`);
  return at >= 0 ? argv[at + 1] : fallback;
};
const scenarios = argv.filter((value, index) => !value.startsWith("--") && !argv[index - 1]?.startsWith("--"));
const names = scenarios.length ? scenarios : ["cold", "warm", "ping"];
const readMs = Number(flag("read-ms", 0));
const hoverMs = Number(flag("hover", 250));
const query = `${flag("query", "turns=25&tools=30&motion=1")}&heavy=${flag("heavy", 6)}&readMs=${readMs}`;
const settleMs = Number(flag("settle", 700));

const RECORDER = `(() => {
  const state = window.__sw = { frames: [], longtasks: [], events: [], reads: [], prefix: "", clickAt: 0, handlerAt: 0, swapAt: 0, active: false };
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
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) if (state.active) state.longtasks.push({ start: entry.startTime, duration: entry.duration });
  }).observe({ entryTypes: ["longtask"] });
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (state.active) state.events.push({ name: entry.name, start: entry.startTime, processingStart: entry.processingStart, processingEnd: entry.processingEnd, duration: entry.duration });
    }
  }).observe({ type: "event", durationThreshold: 16 });
  document.addEventListener("click", (event) => {
    if (!state.active || state.clickAt) return;
    state.clickAt = event.timeStamp;
    state.handlerAt = performance.now();
  }, true);
  const bridge = window.loom;
  const call = bridge.call;
  bridge.call = async function (method, args) {
    const started = performance.now();
    try { return await call.apply(this, arguments); }
    finally { if (state.active && method === "thread/read" && !args?.threadOnly) state.reads.push({ thread: args?.threadId, started, ended: performance.now() }); }
  };
  state.begin = (target) => Object.assign(state, { frames: [], longtasks: [], events: [], reads: [], target, before: document.querySelector(".turn-block")?.dataset.turnId, clickAt: 0, handlerAt: 0, swapAt: 0, feedbackAt: 0, active: true });
  state.end = () => { state.active = false; return { frames: state.frames, longtasks: state.longtasks, events: state.events, reads: state.reads,
    clickAt: state.clickAt, handlerAt: state.handlerAt, swapAt: state.swapAt, feedbackAt: state.feedbackAt }; };
})()`;

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
};
const ms = (value) => Math.round(value);

async function open() {
  const browser = await chromium.launch({
    executablePath: process.env.LOOM_CHROMIUM_PATH,
    headless: !headed,
    args: headed ? ["--window-position=2400,0", "--disable-features=CalculateNativeWinOcclusion"] : [],
  });
  const page = await browser.newPage({ viewport: { width: 1707, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("loom.sidebar.expandedProjects", JSON.stringify(["loom", "agent"])));
  const client = await page.context().newCDPSession(page);
  await client.send("Performance.enable");
  await page.goto(`${origin}${page_path}?${query}`);
  try {
    await page.locator(".turn-block").nth(2).waitFor({ state: "attached", timeout: 60000 });
  } catch (error) {
    console.log("the page never showed three turns:", errors.slice(0, 3), await page.evaluate(() => document.body.innerText.slice(0, 300)).catch(() => ""));
    throw error;
  }
  await page.waitForTimeout(1500);
  await page.evaluate(RECORDER);
  const metrics = async () => Object.fromEntries((await client.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));
  return { browser, page, errors, metrics, current: 0 };
}

// One click on conversation `id`, with the page recording; returns what happened.
async function visit(rig, id, { hover = 0 } = {}) {
  const { page, metrics } = rig;
  const row = page.locator(`.compact-thread-row[data-thread-id="perf-${id}"] .compact-thread-main`);
  await row.scrollIntoViewIfNeeded();
  const box = await row.boundingBox();
  // On the title: the row's quick actions (pin, archive, more) cover its right half while the pointer is over it.
  const x = box.x + Math.min(44, box.width / 4);
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  if (hover) await page.waitForTimeout(hover);
  else await page.waitForTimeout(60);
  await page.evaluate((target) => window.__sw.begin(target), `perf-${id}`);
  const before = await metrics();
  await page.mouse.click(x, y);
  await page.waitForFunction(() => window.__sw.swapAt > 0, null, { timeout: 30000, polling: 16 });
  await page.waitForTimeout(settleMs);
  const after = await metrics();
  const record = await page.evaluate(() => window.__sw.end());
  const t0 = record.clickAt;
  const frames = record.frames.filter((time) => time >= t0 - 20);
  const gaps = frames.slice(1).map((time, index) => time - frames[index]);
  const swapFrame = frames.find((time) => time >= record.swapAt);
  const nextFrame = frames[frames.indexOf(swapFrame) + 1] ?? swapFrame;
  const feedbackFrame = frames.find((time) => time >= record.feedbackAt);
  const afterFeedback = frames[frames.indexOf(feedbackFrame) + 1] ?? feedbackFrame;
  const afterSwap = record.longtasks.filter((task) => task.start >= t0);
  const lastBusy = afterSwap.reduce((latest, task) => Math.max(latest, task.start + task.duration), t0);
  const worstGapAt = gaps.indexOf(Math.max(0, ...gaps));
  const read = record.reads.find((entry) => entry.thread === `perf-${id}`);
  const tap = record.events.filter((entry) => entry.start >= t0 - 200 && entry.start <= t0 + 200);
  return {
    id,
    inputDelay: record.handlerAt - t0,
    readMs: read ? read.ended - read.started : null,
    feedback: afterFeedback - t0,
    swap: record.swapAt - t0,
    paint: nextFrame - t0,
    idle: lastBusy - t0,
    taskMs: (after.TaskDuration - before.TaskDuration) * 1000,
    scriptMs: (after.ScriptDuration - before.ScriptDuration) * 1000,
    layoutMs: (after.LayoutDuration - before.LayoutDuration) * 1000,
    recalcMs: (after.RecalcStyleDuration - before.RecalcStyleDuration) * 1000,
    longTasks: afterSwap.length,
    longest: Math.max(0, ...afterSwap.map((task) => task.duration)),
    worstGap: Math.max(0, ...gaps),
    worstGapAt: gaps.length ? frames[worstGapAt] - t0 : 0,
    slowFrames: gaps.filter((gap) => gap > 34).length,
    interaction: Math.max(0, ...tap.map((entry) => entry.duration)),
  };
}

const header = "click   read  feedb   swap  paint   idle |   task script layout recalc | long  longest  worstGap  >34ms | tap";
const rowText = (r) => [
  `perf-${r.id}`.padEnd(7),
  String(r.readMs === null ? "-" : ms(r.readMs)).padStart(5),
  String(ms(r.feedback)).padStart(6),
  String(ms(r.swap)).padStart(6),
  String(ms(r.paint)).padStart(6),
  String(ms(r.idle)).padStart(6),
  "|",
  String(ms(r.taskMs)).padStart(6),
  String(ms(r.scriptMs)).padStart(6),
  String(ms(r.layoutMs)).padStart(6),
  String(ms(r.recalcMs)).padStart(6),
  "|",
  String(r.longTasks).padStart(4),
  String(ms(r.longest)).padStart(8),
  `${String(ms(r.worstGap)).padStart(5)}@${String(ms(r.worstGapAt)).padEnd(4)}`,
  String(r.slowFrames).padStart(6),
  "|",
  String(ms(r.interaction)).padStart(4),
].join(" ");

function summarise(label, rows) {
  const pick = (key) => ms(median(rows.map((row) => row[key])));
  console.log(`${label.padEnd(8)} median: feedback ${pick("feedback")}  swap ${pick("swap")}  paint ${pick("paint")}  idle ${pick("idle")}  task ${pick("taskMs")}  script ${pick("scriptMs")}  layout ${pick("layoutMs")}  recalc ${pick("recalcMs")}  longest ${pick("longest")}  worstGap ${pick("worstGap")}`);
}

const plans = {
  cold: { order: [1, 2, 3, 4, 5], hover: 0 },
  warm: { order: [3, 1, 5, 2, 4], hover: 0 },
  ping: { order: [0, 1, 0, 1, 0, 1, 0, 1], hover: 0 },
  hover: { order: [1, 2, 3, 4, 5], hover: hoverMs },
  // --order 3,6,3,6 : any sequence of conversations, as many times as you like
  custom: { order: flag("order", "1,2").split(",").map(Number), hover: hoverMs && argv.includes("--hover") ? hoverMs : 0 },
};
const results = {};
const rig = await open();
try {
  console.log(`query ${query}`);
  console.log(header);
  for (const name of names) {
    const plan = plans[name];
    assert.ok(plan, `unknown scenario ${name}`);
    const rows = [];
    for (const id of plan.order) {
      if (id === rig.current) continue; // a click on the open conversation is not a switch
      const row = await visit(rig, id, { hover: plan.hover });
      rig.current = id;
      rows.push(row);
      console.log(rowText(row));
    }
    results[name] = rows;
    summarise(name, rows);
    console.log("");
  }
  assert.deepEqual(rig.errors, []);
  const out = flag("json", "");
  if (out) fs.writeFileSync(out, JSON.stringify(results, null, 1));
} finally { await rig.browser.close(); }
