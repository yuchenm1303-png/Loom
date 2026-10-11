// Motion lab: what makes the browser recalculate style (and lay out) while a run streams?
//   node style-cost.mjs <url> [width=935] [height=731] [dsf=1.5] [--from 9000] [--to 20000] [--off animations|ambient|...]
// Trace events from the page between --from and --to ms after the prompt: how many UpdateLayoutTree / Layout /
// Paint events there are, how long they take, how many elements each style recalc touched, and the largest ones with
// the reason Blink recorded. --off <css selector list> injects `animation: none !important` for them first, which turns
// an experiment ("how much of this is the ambient glow?") into one command.
import fs from "node:fs";
import { launch } from "./launch.mjs";
const argv = process.argv.slice(2);
const value = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const flagged = new Set(["--from", "--to", "--off", "--css"]);
const positional = argv.filter((arg, index) => !arg.startsWith("--") && !flagged.has(argv[index - 1]));
const [url, width = "935", height = "731", dsf = "1.5"] = positional;
const from = Number(value("--from", 9000));
const to = Number(value("--to", 20000));
const off = value("--off", "");
const css = value("--css", "");
const browser = await launch();
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: Number(dsf) });
  await page.goto(url);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  await page.waitForTimeout(800);
  if (off) await page.addStyleTag({ content: `${off} { animation: none !important; }` });
  if (css) await page.addStyleTag({ content: css });
  const client = await page.context().newCDPSession(page);
  const events = [];
  client.on("Tracing.dataCollected", (chunk) => events.push(...chunk.value));
  await page.evaluate(() => window.liveFixture.send());
  const started = Date.now();
  await page.waitForTimeout(Math.max(0, from));
  await client.send("Tracing.start", { traceConfig: { includedCategories: ["devtools.timeline", "disabled-by-default-devtools.timeline", "disabled-by-default-devtools.timeline.invalidationTracking"] }, transferMode: "ReportEvents" });
  await page.waitForTimeout(Math.max(0, to - from));
  await client.send("Tracing.end");
  await new Promise((resolve) => client.once("Tracing.tracingComplete", resolve));
  const window_ = (to - from) / 1000;
  const count = new Map();
  const dur = new Map();
  const big = [];
  const styleByCount = [];
  for (const event of events) {
    if (event.ph !== "X" && event.ph !== "B") continue;
    if (!["UpdateLayoutTree", "Layout", "Paint", "PrePaint", "Layerize", "Commit", "FunctionCall", "FireAnimationFrame", "RunTask", "ResizeObserver::deliverObservations", "UpdateLayer", "HitTest", "IntersectionObserverController::computeIntersections", "EventDispatch", "TimerFire", "XHRReadyStateChange", "MajorGC", "MinorGC"].includes(event.name)) continue;
    count.set(event.name, (count.get(event.name) ?? 0) + 1);
    dur.set(event.name, (dur.get(event.name) ?? 0) + (event.dur ?? 0));
    if (event.name === "UpdateLayoutTree") {
      const n = event.args?.elementCount ?? 0;
      styleByCount.push(n);
      big.push({ dur: event.dur ?? 0, n, args: event.args });
    }
    if (event.name === "Layout") big.push({ dur: event.dur ?? 0, n: event.args?.beginData?.totalObjects ?? 0, layout: true, args: event.args });
  }
  console.log(`window ${window_}s (${from}-${to}ms after send)${off ? `  animations off for: ${off}` : ""}${css ? `  css: ${css}` : ""}`);
  for (const name of ["RunTask", "FunctionCall", "FireAnimationFrame", "UpdateLayoutTree", "Layout", "PrePaint", "Paint", "Layerize", "Commit", "UpdateLayer", "ResizeObserver::deliverObservations", "EventDispatch", "TimerFire", "MinorGC", "MajorGC"]) {
    if (!count.has(name)) continue;
    console.log(`  ${name.padEnd(38)} ${String(count.get(name)).padStart(6)} events  ${String(Math.round((dur.get(name) / 1000) / window_ * 10) / 10).padStart(7)} ms/s  avg ${(dur.get(name) / count.get(name) / 1000).toFixed(2)}ms`);
  }
  if (styleByCount.length) {
    const sorted = [...styleByCount].sort((a, b) => a - b);
    console.log(`  style recalcs: ${styleByCount.length}; elements per recalc p50 ${sorted[Math.floor(sorted.length / 2)]} p90 ${sorted[Math.floor(sorted.length * 0.9)]} max ${sorted.at(-1)}; total elements/s ${Math.round(styleByCount.reduce((a, b) => a + b, 0) / window_)}`);
  }
  const reasons = new Map();
  for (const event of events) {
    if (event.name !== "StyleRecalcInvalidationTracking") continue;
    const data = event.args?.data ?? {};
    const key = `${data.reason ?? "?"} | ${data.nodeName ?? ""} | ${data.extraData ?? ""} | ${data.selectorPart ?? ""}`;
    reasons.set(key, (reasons.get(key) ?? 0) + 1);
  }
  if (reasons.size) {
    console.log("  style invalidations by reason | node | extra | selector (count over the window):");
    for (const [key, n] of [...reasons.entries()].sort((a, b) => b[1] - a[1]).slice(0, 18)) console.log(`    ${String(n).padStart(6)}  ${key.slice(0, 150)}`);
  }
  console.log("  slowest style recalcs / layouts:");
  for (const item of big.sort((a, b) => b.dur - a.dur).slice(0, 8)) {
    console.log(`    ${item.layout ? "layout" : "style "} ${(item.dur / 1000).toFixed(2)}ms  objects/elements ${item.n}${item.args?.beginData?.dirtyObjects !== undefined ? ` dirty ${item.args.beginData.dirtyObjects}` : ""}`);
  }
  if (process.env.LOOM_TRACE_OUT) fs.writeFileSync(process.env.LOOM_TRACE_OUT, JSON.stringify(events));
} finally { await browser.close(); }
