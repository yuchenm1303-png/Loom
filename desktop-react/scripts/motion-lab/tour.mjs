// Motion lab: a tour of the whole interface, measured.
//   node tour.mjs <url-of-live-app.html> [outDir] [--shots] [--only label,label] [--dsf 1.5] [--theme dark|light]
// Drives the live-app fixture (the real App over an in-memory host) through the interactions people actually make:
// sidebar hover, selection, search, folding; header toggles; settings and its pages; the composer's popovers and
// growth; message actions; the process disclosure; a context menu; a send. For every step it records, from painted
// frames, how long the worst frame took, how many frames overran 25 ms, how much of the page moved without being asked
// to (layout shift, and which elements), and the most animations running at once. --shots also saves a picture
// just after the step begins and when it has settled, for reading by eye. --popin lists what appeared on screen
// with no animation at all (a surface that is simply there on the next frame) and what vanished the same way. --trace breaks each step's longest main-thread
// tasks into script, style, layout and paint (and names the stage that forced the layout), to say where a hitch went.
import fs from "node:fs";
import { launch } from "./launch.mjs";
import { popinProbe, tally, report } from "./popin-probe.mjs";

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const option = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const skip = new Set(["--shots", "--trace", "--popin"]);
const valued = new Set(["--only", "--dsf", "--theme", "--repeat"]);
const positional = argv.filter((arg, index) => !skip.has(arg) && !valued.has(arg) && !valued.has(argv[index - 1]));
const [url, outDir = ""] = positional;
const only = option("--only", "") ? new Set(option("--only", "").split(",")) : null;
const dsf = Number(option("--dsf", "1.5"));
const theme = option("--theme", "dark");
if (outDir) fs.mkdirSync(outDir, { recursive: true });

const browser = await launch();
try {
  const page = await browser.newPage({ viewport: { width: 1180, height: 780 }, deviceScaleFactor: dsf });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  if (flag("--popin")) await page.addInitScript(popinProbe);
  await page.addInitScript(() => {
    const rec = { frames: [], shifts: [], on: false, peakAnimations: 0 };
    window.__tour = rec;
    const label = (el) => {
      if (!el || el.nodeType !== 1) return "?";
      const cls = String(el.className?.baseVal ?? el.className ?? "").trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".");
      return `${el.tagName.toLowerCase()}${cls ? "." + cls : ""}`;
    };
    try {
      new PerformanceObserver((list) => {
        if (!rec.on) return;
        for (const entry of list.getEntries()) {
          if (entry.hadRecentInput) continue;
          rec.shifts.push({ value: entry.value, sources: (entry.sources ?? []).map((source) => `${label(source.node)} ${Math.round(source.previousRect.y)}→${Math.round(source.currentRect.y)}`).slice(0, 3) });
        }
      }).observe({ type: "layout-shift", buffered: false });
    } catch { /* not supported */ }
    let previous = performance.now();
    const channel = new MessageChannel();
    channel.port1.onmessage = () => {
      const now = performance.now();
      if (rec.on) {
        rec.frames.push(now - previous);
        rec.peakAnimations = Math.max(rec.peakAnimations, document.getAnimations().filter((a) => a.playState === "running").length);
      }
      previous = now;
    };
    const loop = () => { channel.port2.postMessage(0); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  });
  await page.goto(`${url}${url.includes("?") ? "&" : "?"}theme=${theme}`);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  await page.waitForTimeout(1500);

  const byText = (text) => page.locator("button", { hasText: text }).first();
  const byLabel = (label) => page.locator(`[aria-label^="${label}"], [title^="${label}"]`).first();
  // The quick actions (pin, archive, more) sit over the right half of a hovered row: aim at its left.
  const row = (title) => page.locator(".compact-thread-main", { hasText: title }).first();
  const at = { position: { x: 36, y: 12 } };
  const settle = (ms) => page.waitForTimeout(ms);
  const steps = [
    ["sidebar-hover", async () => { for (const title of ["修复模型面板卡片错位", "优化图片附件发送", "检查项目面板浅色主题"]) { await row(title).hover(at); await settle(140); } await page.mouse.move(700, 400); }, 300],
    ["sidebar-select-thread", async () => { await row("优化图片附件发送").click(at); }, 900],
    ["sidebar-select-back", async () => { await row("登录页邮箱校验").click(at); }, 900],
    ["sidebar-search-open", async () => { await byLabel("搜索对话").click(); }, 500],
    ["sidebar-search-type", async () => { await page.locator(".compact-search input").type("模型", { delay: 60 }); }, 500],
    ["sidebar-search-close", async () => { await page.keyboard.press("Escape"); }, 500],
    ["sidebar-fold-project", async () => { await byLabel("折叠 Loom").click(); }, 500],
    ["sidebar-unfold-project", async () => { await byLabel("展开 Loom").click(); }, 500],
    ["header-hide-sidebar", async () => { await byLabel("收起会话侧栏").click(); }, 700],
    ["header-show-sidebar", async () => { await byLabel("展开会话侧栏").click(); }, 700],
    ["inspector-open", async () => { await byLabel("打开运行检查器").click(); }, 800],
    ["inspector-close", async () => { await page.locator(".thread-inspector-button").click(); }, 800],
    ["process-expand", async () => { await page.locator(".turn-process-header").first().click(); }, 900],
    ["process-collapse", async () => { await page.locator(".turn-process-header").first().click(); }, 900],
    ["message-hover", async () => { await page.locator(".assistant-message-shell").last().hover(); }, 400],
    ["composer-focus", async () => { await page.locator(".composer textarea").click(); }, 500],
    ["composer-type", async () => { await page.keyboard.type("给登录页加邮箱校验", { delay: 45 }); }, 300],
    ["composer-grow", async () => { for (let i = 0; i < 4; i += 1) { await page.keyboard.press("Shift+Enter"); await page.keyboard.type("第 " + (i + 2) + " 行", { delay: 30 }); } }, 500],
    ["composer-clear", async () => { await page.keyboard.press("Control+A"); await page.keyboard.press("Backspace"); }, 500],
    ["composer-permission-open", async () => { await byLabel("权限设置").click(); }, 600],
    ["composer-permission-close", async () => { await page.keyboard.press("Escape"); }, 500],
    ["composer-model-open", async () => { await page.locator(".model-chip").click(); }, 700],
    ["composer-model-close", async () => { await page.keyboard.press("Escape"); }, 500],
    ["composer-sticker-open", async () => { await byLabel("表情设置").click(); }, 600],
    ["composer-sticker-close", async () => { await page.keyboard.press("Escape"); }, 500],
    ["context-menu-open", async () => { await row("修复模型面板卡片错位").click({ button: "right", ...at }); }, 600],
    ["context-menu-close", async () => { await page.keyboard.press("Escape"); }, 500],
    ["settings-open", async () => { await byLabel("打开 Loom 设置").click(); }, 1100],
    ["settings-page-2", async () => { const buttons = page.locator(".settings-nav button"); await buttons.nth(2).click(); }, 800],
    ["settings-page-3", async () => { const buttons = page.locator(".settings-nav button"); await buttons.nth(4).click(); }, 800],
    ["settings-page-4", async () => { const buttons = page.locator(".settings-nav button"); await buttons.nth(6).click(); }, 800],
    ["settings-close", async () => { await page.keyboard.press("Escape"); }, 1000],
    ["profile-open", async () => { await byLabel("个人主页与使用洞察").click(); }, 1100],
    ["profile-close", async () => { await page.keyboard.press("Escape"); }, 1000],
  ];

  const repeat = Number(option("--repeat", "1"));
  const results = [];
  const maps = { popins: new Map(), popouts: new Map() };
  const client = flag("--trace") ? await page.context().newCDPSession(page) : null;
  const plan = [];
  for (let round = 1; round <= repeat; round += 1) for (const step of steps) plan.push([round, ...step]);
  for (const [round, stepName, action, settleMs] of plan) {
    const name = repeat > 1 ? `${stepName}#${round}` : stepName;
    if (only && !only.has(stepName)) continue;
    const events = [];
    const collect = (chunk) => events.push(...chunk.value);
    if (client) {
      client.on("Tracing.dataCollected", collect);
      await client.send("Tracing.start", { traceConfig: { includedCategories: ["devtools.timeline", "disabled-by-default-devtools.timeline", "disabled-by-default-devtools.timeline.stack"] }, transferMode: "ReportEvents" });
    }
    if (flag("--shots") && outDir) await page.screenshot({ path: `${outDir}/${name}-0before.png` });
    await page.evaluate(() => { window.__tour.frames = []; window.__tour.shifts = []; window.__tour.peakAnimations = 0; window.__tour.on = true; if (window.__popin) window.__popin.on = true; });
    let failure = "";
    try {
      const done = action().catch((error) => { failure = String(error.message).split("\n")[0].slice(0, 90); });
      if (flag("--shots") && outDir) {
        await page.waitForTimeout(70);
        await page.screenshot({ path: `${outDir}/${name}-1early.png` });
      }
      await done;
      await settle(settleMs);
    } catch (error) { failure = String(error.message).split("\n")[0].slice(0, 90); }
    await page.waitForTimeout(0);
    const rec = await page.evaluate(() => { window.__tour.on = false; return { frames: window.__tour.frames, shifts: window.__tour.shifts, peak: window.__tour.peakAnimations }; });
    if (flag("--popin")) tally(maps, await page.evaluate(() => window.__popin.take()), stepName);
    if (flag("--shots") && outDir) await page.screenshot({ path: `${outDir}/${name}-2after.png` });
    const frames = rec.frames;
    const worst = Math.max(0, ...frames);
    const over = frames.filter((dt) => dt > 25).length;
    const shift = rec.shifts.reduce((sum, entry) => sum + entry.value, 0);
    const biggest = rec.shifts.sort((a, b) => b.value - a.value)[0];
    let longest = "";
    if (client) {
      await client.send("Tracing.end");
      await new Promise((resolve) => client.once("Tracing.tracingComplete", resolve));
      client.off("Tracing.dataCollected", collect);
      const tasks = events.filter((event) => event.name === "RunTask" && event.ph === "X" && event.dur >= 18000 && !event.args?.gpu).sort((a, b) => b.dur - a.dur).slice(0, 3);
      longest = tasks.map((task) => {
        const inside = events.filter((event) => event.ph === "X" && event.tid === task.tid && event.pid === task.pid && event.ts >= task.ts && event.ts + (event.dur ?? 0) <= task.ts + task.dur && event !== task);
        const sums = new Map();
        for (const event of inside) sums.set(event.name, (sums.get(event.name) ?? 0) + (event.dur ?? 0));
        const noise = /^(RunTask|ThreadControllerImpl|SequenceManager|v8.callFunction|v8.run|ProfileChunk|Profile|UpdateCounters|TracingStartedInBrowser)/;
        const part = [...sums.entries()].filter(([key, value]) => value > 2000 && !noise.test(key)).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([key, value]) => `${key} ${Math.round(value / 1000)}`).join(" ");
        const forced = inside.filter((event) => event.name === "Layout" && event.dur > 4000 && event.args?.beginData?.stackTrace?.length).map((event) => event.args.beginData.stackTrace[0]).map((frame) => `${frame.functionName || "(anon)"}@${String(frame.url || "").split("/").pop().split("?")[0]}:${frame.lineNumber}`);
        const layouts = inside.filter((event) => event.name === "Layout" && event.dur > 3000).map((event) => `${Math.round(event.dur / 1000)}ms dirty ${event.args?.beginData?.dirtyObjects}/${event.args?.beginData?.totalObjects}`);
        return `  task ${Math.round(task.dur / 1000)}ms: ${part}${layouts.length ? "  [layouts " + layouts.join(", ") + "]" : ""}${forced.length ? "  forced layout by " + [...new Set(forced)].slice(0, 3).join(", ") : ""}`;
      }).join("\n");
    }
    results.push({ name, worst, over, frames: frames.length, shift, peak: rec.peak, biggest, failure, longest });
  }
  const width = Math.max(...results.map((result) => result.name.length));
  console.log(`${"step".padEnd(width)}  frames  worst   >25ms  shift   anims  note`);
  for (const result of results) {
    const note = result.failure ? `FAILED: ${result.failure}` : result.shift > 0.02 && result.biggest ? `shift ${result.biggest.value.toFixed(3)} ${result.biggest.sources.join("; ")}` : "";
    console.log(`${result.name.padEnd(width)}  ${String(result.frames).padStart(6)}  ${result.worst.toFixed(0).padStart(4)}ms  ${String(result.over).padStart(5)}  ${result.shift.toFixed(3)}  ${String(result.peak).padStart(5)}  ${note}`);
    if (result.longest) console.log(result.longest);
  }
  if (flag("--popin")) report(maps);
  if (errors.length) console.log("page errors:", [...new Set(errors)].slice(0, 5));
} finally { await browser.close(); }
