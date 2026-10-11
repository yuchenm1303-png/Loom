// Motion lab: does the live edge hold still, or does it shake?
//   node jitter.mjs <url> [out.json] [width=935] [height=731] [dsf=1.5] [--cdp]
// follow.mjs asks "is the newest content on screen". This asks what the reader's eye sees while it is:
// for every painted frame it records the scroll position, the page height, and where a set of landmarks sit in the
// viewport (the user's bubble above the run, each block of the live turn, the line being typed). Then it reports
//   - scroll steps: how large each frame's scroll is, how many reverse direction (the view going up then down),
//     how many are fractions of a pixel (text re-rasterised at a new subpixel offset reads as shimmer);
//   - landmark moves: a landmark that was not touched yet moved against the scroll, or by a different amount than
//     the scroll (layout above it changed under a view that was pinned to the bottom);
//   - the typed line: how far it travels per frame while the view is pinned to the bottom, and the frames in which it
//     moved and came back;
//   - frame pacing and the number of running animations.
// "Painted" matters (see follow.mjs): samples are taken from a message task after the frame.
import fs from "node:fs";
import { launch } from "./launch.mjs";
const args = process.argv.slice(2).filter((arg) => !arg.startsWith("--"));
const flags = new Set(process.argv.slice(2).filter((arg) => arg.startsWith("--")));
const [url, out = "", width = "935", height = "731", dsf = "1.5"] = args;
const browser = await launch();
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: Number(dsf) });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const rec = { frames: [], marks: [], labels: {} };
    window.__rec = rec;
    const t0 = performance.now();
    window.__mark = (name) => rec.marks.push({ t: Math.round(performance.now() - t0), name });
    const ids = new WeakMap();
    let next = 0;
    const describe = (el) => {
      let id = ids.get(el);
      if (id === undefined) {
        id = next++;
        ids.set(el, id);
        const cls = String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className || "").trim().split(/\s+/).slice(0, 2).join(".");
        rec.labels[id] = `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ""}`;
      }
      return id;
    };
    // The landmarks: the live turn's blocks down to depth 4, the user bubble, and every line box of the receiving text.
    const landmarks = (scroller, top) => {
      const found = [];
      const user = scroller.querySelector(".turn-block.is-active .message-shell.user-message-shell, .turn-block:last-child .user-message-shell");
      if (user) found.push([describe(user), user.getBoundingClientRect()]);
      const turn = scroller.querySelector(".turn-block.is-active") || scroller.querySelector(".transcript > .turn-block:last-child");
      if (turn) {
        const walk = (el, depth) => {
          for (const child of el.children) {
            const rect = child.getBoundingClientRect();
            if (rect.height > 0) found.push([describe(child), rect]);
            if (depth < 4 && child.children.length && rect.height > 0) walk(child, depth + 1);
          }
        };
        walk(turn, 0);
      }
      return found.map(([id, rect]) => [id, Math.round((rect.top - top) * 100) / 100, Math.round(rect.height * 100) / 100]);
    };
    // The line being typed: the bottom of the block that holds the last text of the receiving message (a glyph box
    // changes with the font, a CJK character and a Latin one differ by 4px; the block only moves when a line is added).
    const typedLine = (scroller, top) => {
      const body = scroller.querySelector(".turn-block.is-active .markdown-body.is-receiving, .turn-block.is-active .markdown-body.is-streaming");
      if (!body) return null;
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      let last = null;
      for (let node = walker.nextNode(); node; node = walker.nextNode()) if (node.nodeValue.trim()) last = node;
      if (!last) return null;
      const block = last.parentElement?.closest("p, li, td, th, h1, h2, h3, h4, h5, h6, pre, blockquote") ?? body;
      const rect = block.getBoundingClientRect();
      return [Math.round((rect.bottom - top) * 100) / 100, Math.round(rect.left * 100) / 100, last.nodeValue.length, body.textContent.slice(-26), block.tagName.toLowerCase(), body.textContent.length];
    };
    let previous = performance.now();
    let animationCount = 0;
    let animationAt = 0;
    const channel = new MessageChannel();
    channel.port1.onmessage = () => {
      const scroller = document.querySelector(".conversation-stage > .transcript-scroll");
      if (!scroller) return;
      const now = performance.now();
      const box = scroller.getBoundingClientRect();
      if (now - animationAt > 250) { animationCount = document.getAnimations().length; animationAt = now; }
      const content = scroller.querySelector(".transcript");
      const lastChild = content?.lastElementChild;
      rec.frames.push({
        t: Math.round((now - t0) * 10) / 10,
        dt: Math.round((now - previous) * 10) / 10,
        st: scroller.scrollTop, sh: scroller.scrollHeight, ch: scroller.clientHeight,
        fol: scroller.dataset.following ?? "",
        edge: lastChild ? Math.round((lastChild.getBoundingClientRect().bottom - box.bottom) * 100) / 100 : null,
        anim: animationCount,
        lm: landmarks(scroller, box.top),
        line: typedLine(scroller, box.top),
      });
      previous = now;
    };
    const loop = () => { channel.port2.postMessage(0); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  });
  const client = flags.has("--cdp") ? await page.context().newCDPSession(page) : null;
  if (client) await client.send("Performance.enable");
  const samples = [];
  const sampler = client ? setInterval(async () => {
    try {
      const { metrics } = await client.send("Performance.getMetrics");
      const row = { at: Date.now() };
      for (const metric of metrics) row[metric.name] = metric.value;
      samples.push(row);
    } catch { /* page closing */ }
  }, 250) : null;
  await page.goto(url);
  if (/live-app/.test(url)) {
    await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
    await page.waitForTimeout(800);
    await page.evaluate(() => { window.__mark("send"); window.liveFixture.send(); });
    await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length > 0, null, { timeout: 30000 });
    await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length === 0, null, { timeout: 300000 });
    await page.waitForTimeout(2500);
  } else {
    await page.waitForTimeout(8000);
  }
  const rec = await page.evaluate(() => window.__rec);
  if (sampler) clearInterval(sampler);
  rec.metrics = samples;
  if (out) fs.writeFileSync(out, JSON.stringify(rec));
  report(rec);
  if (samples.length > 4) busy(samples);
  if (errors.length) console.log("page errors:", errors);
} finally { await browser.close(); }

function report(rec) {
  const frames = rec.frames;
  const sendAt = rec.marks.find((mark) => mark.name === "send")?.t ?? 0;
  const live = frames.filter((frame) => frame.t >= sendAt);
  console.log(`frames ${frames.length}, after send ${live.length}; frame time p50 ${percentile(live.map((f) => f.dt), 50)}ms p95 ${percentile(live.map((f) => f.dt), 95)}ms max ${Math.max(...live.map((f) => f.dt))}ms; animations running p50 ${percentile(live.map((f) => f.anim), 50)} max ${Math.max(...live.map((f) => f.anim))}`);
  const slow = live.filter((frame) => frame.dt > 33);
  console.log(`frames over 33ms: ${slow.length}${slow.length ? ` (${slow.slice(0, 8).map((f) => `${Math.round(f.t)}:${Math.round(f.dt)}`).join(" ")}${slow.length > 8 ? " …" : ""})` : ""}`);

  // 1. Scroll steps while the view follows the live edge.
  const steps = [];
  for (let index = 1; index < live.length; index += 1) {
    const a = live[index - 1];
    const b = live[index];
    if (a.fol === "true" && b.fol === "true") steps.push({ t: b.t, d: round(b.st - a.st), grow: b.sh - a.sh, edge: b.edge, dt: b.dt });
  }
  const moving = steps.filter((step) => Math.abs(step.d) > 0.01);
  const reversals = [];
  for (let index = 1; index < moving.length; index += 1) {
    const a = moving[index - 1];
    const b = moving[index];
    // adjacent in time (a frame or two apart) and opposite in sign
    if (Math.sign(a.d) !== Math.sign(b.d) && Math.abs(a.d) >= 0.5 && Math.abs(b.d) >= 0.5 && b.t - a.t < 80) reversals.push([a, b]);
  }
  const fractional = moving.filter((step) => Math.abs(step.d) < 1);
  const big = moving.filter((step) => Math.abs(step.d) >= 12);
  console.log(`\nscroll steps while following: ${moving.length} of ${steps.length} frames move the view; ${fractional.length} are under 1px; ${big.length} are 12px or more in one frame`);
  console.log(`  direction reversals within 80ms: ${reversals.length}${reversals.length ? "  e.g. " + reversals.slice(0, 6).map(([a, b]) => `${Math.round(a.t)}ms ${a.d}→${b.d}`).join("  ") : ""}`);
  const histogram = {};
  for (const step of moving) {
    const key = Math.abs(step.d) < 1 ? "<1" : Math.abs(step.d) < 4 ? "1-4" : Math.abs(step.d) < 12 ? "4-12" : Math.abs(step.d) < 30 ? "12-30" : "30+";
    histogram[key] = (histogram[key] ?? 0) + 1;
  }
  console.log(`  |step| histogram: ${JSON.stringify(histogram)}`);
  const behind = live.filter((frame) => frame.fol === "true" && frame.edge !== null && frame.edge > 2);
  console.log(`  frames with the newest block below the viewport edge by more than 2px: ${behind.length} (worst ${Math.max(0, ...behind.map((f) => f.edge))}px)`);

  // 2. Landmarks: expected on-screen y = previous y - scroll step. Anything else is layout moving under the reader.
  const bad = new Map();
  for (let index = 1; index < live.length; index += 1) {
    const a = live[index - 1];
    const b = live[index];
    const scroll = b.st - a.st;
    const before = new Map(a.lm.map(([id, top, h]) => [id, [top, h]]));
    for (const [id, top, h] of b.lm) {
      const prior = before.get(id);
      if (!prior) continue;
      const moved = top - prior[0] + scroll; // layout movement of this block in the document
      if (Math.abs(moved) < 0.5) continue;
      const entry = bad.get(id) ?? { frames: 0, up: 0, down: 0, worst: 0, firstAt: b.t, grow: 0 };
      entry.frames += 1;
      entry.worst = Math.max(entry.worst, Math.abs(moved));
      if (moved < 0) entry.up += 1; else entry.down += 1;
      entry.grow += h - prior[1];
      bad.set(id, entry);
    }
  }
  const ranked = [...bad.entries()].sort((a, b) => b[1].frames - a[1].frames).slice(0, 14);
  console.log("\nblocks whose position in the document changed (layout moving them), by frames:");
  for (const [id, e] of ranked) console.log(`  ${rec.labels[id].padEnd(40)} ${String(e.frames).padStart(4)} frames  worst ${e.worst.toFixed(1)}px  up ${e.up} down ${e.down}  height change ${e.grow.toFixed(0)}px`);

  typing(live);

  // 3. The typed line.
  const lines = live.filter((frame) => frame.line && frame.fol === "true");
  const travels = [];
  for (let index = 1; index < lines.length; index += 1) {
    const a = lines[index - 1];
    const b = lines[index];
    if (b.t - a.t > 80) continue;
    travels.push({ t: b.t, dy: round(b.line[0] - a.line[0]), scroll: round(b.st - a.st), grow: b.sh - a.sh, dx: round(b.line[1] - a.line[1]) });
  }
  const jumps = travels.filter((travel) => Math.abs(travel.dy) >= 3);
  const backAndForth = [];
  for (let index = 1; index < travels.length; index += 1) {
    const a = travels[index - 1];
    const b = travels[index];
    if (Math.abs(a.dy) >= 1.5 && Math.abs(b.dy) >= 1.5 && Math.sign(a.dy) !== Math.sign(b.dy)) backAndForth.push([a, b]);
  }
  console.log(`\nthe line being typed, while pinned: ${lines.length} frames; moved 3px+ in a frame ${jumps.length} times; went one way and straight back ${backAndForth.length} times`);
  if (jumps.length) console.log("  moves: " + jumps.slice(0, 14).map((j) => `${Math.round(j.t)}ms dy ${j.dy} (scroll ${j.scroll}, grow ${j.grow})`).join(" | "));
  if (backAndForth.length) console.log("  back and forth: " + backAndForth.slice(0, 8).map(([a, b]) => `${Math.round(a.t)}ms ${a.dy}→${b.dy}`).join(" | "));
}

// The typing rhythm of the receiving message: how long between reveals, how much each reveals.
function typing(live) {
  const reveals = [];
  let previous = null;
  for (const frame of live) {
    const chars = frame.line?.[5];
    if (chars === undefined) continue;
    if (previous && chars !== previous.chars) reveals.push({ t: frame.t, gap: frame.t - previous.t, grew: chars - previous.chars, chars });
    if (!previous || chars !== previous.chars) previous = { t: frame.t, chars };
  }
  if (!reveals.length) return;
  const byLength = [[0, 2000], [2000, 5000], [5000, 9000], [9000, 1e9]];
  console.log("\ntyping rhythm (reveals of the receiving answer; gap = ms since the previous reveal):");
  for (const [lo, hi] of byLength) {
    const part = reveals.filter((reveal) => reveal.chars >= lo && reveal.chars < hi && reveal.gap < 400);
    if (part.length < 5) continue;
    console.log(`  ${String(lo).padStart(5)}-${hi === 1e9 ? "end" : hi} chars: ${part.length} reveals, gap p50 ${percentile(part.map((r) => r.gap), 50)}ms p90 ${percentile(part.map((r) => r.gap), 90)}ms, ${percentile(part.map((r) => r.grew), 50)} chars each (p90 ${percentile(part.map((r) => r.grew), 90)})`);
  }
}

// Where the main thread went, per second of the run (CDP Performance.getMetrics deltas, seconds of work per second).
function busy(samples) {
  const rows = [];
  for (let index = 4; index < samples.length; index += 4) {
    const a = samples[index - 4];
    const b = samples[index];
    const wall = (b.at - a.at) / 1000;
    const d = (key) => ((b[key] - a[key]) / wall) * 100;
    rows.push({ script: d("ScriptDuration"), layout: d("LayoutDuration"), style: d("RecalcStyleDuration"), task: d("TaskDuration"), nodes: b.Nodes });
  }
  const mean = (key) => Math.round(rows.reduce((sum, row) => sum + row[key], 0) / rows.length);
  console.log(`
main thread, % of wall time (mean over ${rows.length}s): script ${mean("script")}  layout ${mean("layout")}  style ${mean("style")}  task total ${mean("task")}  | nodes ${rows.at(-1).nodes}`);
  console.log("  per second task%: " + rows.map((row) => Math.round(row.task)).join(" "));
}

function round(value) { return Math.round(value * 100) / 100; }
function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return round(sorted[Math.min(sorted.length - 1, Math.floor((sorted.length * p) / 100))]);
}
