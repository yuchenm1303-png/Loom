// Motion lab: what one panel toggle costs the main thread before anything can move, and why.
//   node toggle-cost.mjs <origin> [--n 5] [--why] [--only sidebar,inspector,review]
// For each toggle (median of n opens and n closes): main-thread task, script, style and layout time from the click to
// the frame after the one that shows it (CDP Performance.getMetrics deltas), and the style recalcs of a traced toggle
// with how many elements each touched against the size of the document. --why lists the invalidations Blink recorded
// for the first big recalc: "invalidates subtree" next to a class change means some selector made that change re-style
// everything below (a substring of the class attribute, an inherited custom property, ...). Build the fixture for
// production first; headless milliseconds are larger than a real window's, the element counts are the same.
import { launch } from "./launch.mjs";

const argv = process.argv.slice(2);
const option = (name, fallback = "") => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const origin = argv.find((a) => /^https?:/.test(a)) || process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const n = Number(option("--n", "5"));
const only = option("--only") ? new Set(option("--only").split(",")) : null;

const TOGGLES = {
  sidebar: [".sidebar-controls .sidebar-toggle, .codex-sidebar-topbar .sidebar-toggle", ".sidebar-controls .sidebar-toggle, .thread-header .sidebar-toggle"],
  inspector: ['.loom-tool-button[data-tool-id="inspector"]', '.loom-tool-button[data-tool-id="inspector"]'],
  review: ['.loom-tool-button[data-tool-id="review"]', '.loom-tool-button[data-tool-id="review"]'],
};

const browser = await launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const cdp = await page.context().newCDPSession(page);
await cdp.send("Performance.enable");
await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=dark&lang=en`);
await page.locator(".thread-header").waitFor({ timeout: 90000 });
await page.waitForTimeout(2000);
const metrics = async () => Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));
const twoFrames = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const elements = await page.evaluate(() => document.querySelectorAll("*").length);
console.log(`document: ${elements} elements`);

for (const [name, [first, second]] of Object.entries(TOGGLES)) {
  if (only && !only.has(name)) continue;
  const rows = [];
  for (let i = 0; i < n * 2; i += 1) {
    await page.waitForTimeout(700);
    const before = await metrics();
    await page.locator(i % 2 === 0 ? first : second).click();
    await twoFrames();
    const after = await metrics();
    const d = (key) => Math.round((after[key] - before[key]) * 1000);
    rows.push({ task: d("TaskDuration"), script: d("ScriptDuration"), style: d("RecalcStyleDuration"), layout: d("LayoutDuration") });
  }
  // Traced toggle: the recalcs and what invalidated them.
  await page.waitForTimeout(700);
  const events = [];
  const onData = (chunk) => events.push(...chunk.value);
  cdp.on("Tracing.dataCollected", onData);
  await cdp.send("Tracing.start", { traceConfig: { includedCategories: ["devtools.timeline", "disabled-by-default-devtools.timeline", "disabled-by-default-devtools.timeline.invalidationTracking"] }, transferMode: "ReportEvents" });
  await page.waitForTimeout(150);
  await page.locator(first).click();
  await page.waitForTimeout(600);
  await cdp.send("Tracing.end");
  await new Promise((resolve) => cdp.once("Tracing.tracingComplete", resolve));
  cdp.off("Tracing.dataCollected", onData);
  await page.locator(second).click();
  const recalcs = events.filter((e) => e.name === "UpdateLayoutTree" && (e.ph === "X" || e.ph === "B")).sort((a, b) => a.ts - b.ts);
  const big = recalcs.filter((r) => (r.args?.elementCount ?? 0) > 20);
  const med = (key, list) => list.map((r) => r[key]).sort((a, b) => a - b)[Math.floor(list.length / 2)];
  for (const [label, list] of [["one way", rows.filter((_, i) => i % 2 === 0)], ["back", rows.filter((_, i) => i % 2 === 1)]]) {
    console.log(`${name.padEnd(10)} ${label.padEnd(8)} task ${String(med("task", list)).padStart(4)}ms  script ${med("script", list)}  style ${med("style", list)}  layout ${med("layout", list)}`);
  }
  console.log(`${"".padEnd(10)} recalcs over 20 elements: ${big.map((r) => `${r.args.elementCount}`).join(", ") || "none"}`);
  if (argv.includes("--why") && big.length) {
    const firstBig = big[0];
    const around = events.filter((e) => /InvalidationTracking/.test(e.name) && e.ts <= firstBig.ts + (firstBig.dur ?? 0) && e.ts > firstBig.ts - 80000);
    const tally = new Map();
    for (const e of around) {
      const data = e.args?.data || {};
      const key = `${e.name.replace("InvalidationTracking", "")} | ${data.reason || ""} | ${data.changedClass || data.changedAttribute || ""} | ${(data.nodeName || "").slice(0, 50)}${JSON.stringify(data.invalidationList || "").includes("allDescendantsMightBeInvalid") ? " | WHOLE SUBTREE" : ""}`;
      tally.set(key, (tally.get(key) || 0) + 1);
    }
    for (const [key, count] of [...tally.entries()].sort((a, b) => b[1] - a[1]).slice(0, 15)) console.log(`${"".padEnd(12)}${String(count).padStart(4)}  ${key}`);
  }
}
await browser.close();
