// Motion lab: which animations are running during a live run, and what each one costs.
//   node animations.mjs <url> [out.json] [width=935] [height=731] [dsf=1.5] [--at 4000,9000,15000]
// document.getAnimations() at chosen moments after the prompt is sent: for every running animation its target, name,
// duration, iteration count and the properties its keyframes touch. A property that is not transform / opacity (or
// translate / rotate / scale) runs on the main thread: style recalc and paint every frame, for as long as it runs.
// Also records DevTools' own verdict (the trace "Animation" events say why an animation could not be composited).
import fs from "node:fs";
import { launch } from "./launch.mjs";
const args = process.argv.slice(2).filter((arg) => !arg.startsWith("--"));
const flagIndex = process.argv.indexOf("--at");
const at = (flagIndex >= 0 ? process.argv[flagIndex + 1] : "3000,7000,12000,20000").split(",").map(Number);
const [url, out = "", width = "935", height = "731", dsf = "1.5"] = args.filter((arg) => arg !== process.argv[flagIndex + 1]);
const COMPOSITOR = new Set(["transform", "opacity", "translate", "rotate", "scale", "filter", "backdropFilter", "offset", "composite", "easing", "offset", "computedOffset"]);
const browser = await launch();
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: Number(dsf) });
  await page.goto(url);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  await page.waitForTimeout(800);
  const client = await page.context().newCDPSession(page);
  const events = [];
  client.on("Tracing.dataCollected", (chunk) => events.push(...chunk.value));
  await client.send("Tracing.start", { traceConfig: { includedCategories: ["devtools.timeline", "blink.animations", "disabled-by-default-devtools.timeline"] }, transferMode: "ReportEvents" });
  await page.evaluate(() => window.liveFixture.send());
  const snapshots = [];
  const started = Date.now();
  for (const moment of at) {
    const wait = moment - (Date.now() - started);
    if (wait > 0) await page.waitForTimeout(wait);
    snapshots.push({ at: moment, list: await page.evaluate(() => {
      const path = (el) => {
        const parts = [];
        for (let node = el; node && node.nodeType === 1 && parts.length < 4; node = node.parentElement) {
          const cls = String(node.className?.baseVal ?? node.className ?? "").trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".");
          parts.unshift(`${node.tagName.toLowerCase()}${cls ? "." + cls : ""}`);
        }
        return parts.join(" > ");
      };
      return document.getAnimations().map((animation) => {
        const effect = animation.effect;
        const target = effect?.target;
        const timing = effect?.getTiming?.() ?? {};
        const props = new Set();
        for (const frame of effect?.getKeyframes?.() ?? []) for (const key of Object.keys(frame)) props.add(key);
        return {
          kind: animation.constructor.name,
          name: animation.animationName || animation.transitionProperty || "",
          target: target ? path(target) : "",
          pseudo: effect?.pseudoElement || "",
          duration: timing.duration, iterations: timing.iterations === Infinity ? "inf" : timing.iterations,
          state: animation.playState, props: [...props],
        };
      });
    }) });
  }
  await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length === 0, null, { timeout: 300000 }).catch(() => {});
  await client.send("Tracing.end");
  await new Promise((resolve) => client.once("Tracing.tracingComplete", resolve));
  const verdicts = new Map();
  for (const event of events) {
    if (event.name !== "Animation" || !event.args?.data) continue;
    const data = event.args.data;
    if (data.compositeFailed || data.unsupportedProperties?.length) {
      const key = `${data.displayName || data.name || data.id} @ ${data.nodeName ?? ""}`;
      verdicts.set(key, { failed: data.compositeFailed, props: data.unsupportedProperties });
    }
  }
  if (out) fs.writeFileSync(out, JSON.stringify({ snapshots, verdicts: [...verdicts] }));
  for (const snap of snapshots) {
    const running = snap.list.filter((item) => item.state === "running");
    console.log(`\n== ${snap.at}ms: ${running.length} running of ${snap.list.length}`);
    const groups = new Map();
    for (const item of running) {
      const heavy = item.props.filter((prop) => !COMPOSITOR.has(prop));
      const key = `${item.kind === "CSSTransition" ? "transition" : "animation"} ${item.name} ${item.iterations === "inf" ? "∞" : ""} [${item.props.join(",")}]${heavy.length ? "  MAIN-THREAD: " + heavy.join(",") : ""}`;
      const entry = groups.get(key) ?? { count: 0, example: item.target + (item.pseudo ? item.pseudo : "") };
      entry.count += 1;
      groups.set(key, entry);
    }
    for (const [key, entry] of [...groups.entries()].sort((a, b) => b[1].count - a[1].count)) console.log(`  ${String(entry.count).padStart(3)} × ${key}\n        ${entry.example}`);
  }
  if (verdicts.size) {
    console.log("\nanimations the browser could not composite (trace):");
    for (const [key, value] of verdicts) console.log(`  ${key}: reasons ${value.failed}${value.props?.length ? " props " + value.props.join(",") : ""}`);
  }
} finally { await browser.close(); }
