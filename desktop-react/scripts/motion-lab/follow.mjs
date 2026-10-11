// Motion lab: does the transcript stay on the live edge while a run streams?
//   node follow.mjs <url> [out.json] [width=935] [height=731] [dsf=1]
// Records, for every PAINTED frame, how far the newest content lies below the viewport and whether follow is on,
// then lists each episode where the view lagged. A live-app.html url is sent to once the composer is ready (add
// ?scenario=code|approval|error|decision|quick); the run is recorded until its turn ends.
// "Painted" matters: a sample taken inside requestAnimationFrame runs before the controller's own correction for
// that frame and reports lag the reader never sees, so this samples from a message task after the frame.
import fs from "node:fs";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const [url, out = "", width = "935", height = "731", dsf = "1"] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: Number(dsf) });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const rec = { frames: [], marks: [] };
    window.__rec = rec;
    const t0 = performance.now();
    window.__mark = (name) => rec.marks.push({ t: Math.round(performance.now() - t0), name });
    const channel = new MessageChannel();
    channel.port1.onmessage = () => {
      const scroller = document.querySelector(".conversation-stage > .transcript-scroll");
      if (!scroller) return;
      const last = scroller.querySelector(".transcript")?.lastElementChild;
      rec.frames.push({
        t: Math.round(performance.now() - t0),
        st: Math.round(scroller.scrollTop * 10) / 10, sh: scroller.scrollHeight, ch: scroller.clientHeight,
        following: scroller.dataset.following ?? "",
        jump: document.querySelector(".transcript-jump-latest")?.classList.contains("is-visible") ? 1 : 0,
        // px of the last turn below the visible bottom edge; the transcript's own bottom padding is negative here
        hidden: last ? Math.round((last.getBoundingClientRect().bottom - scroller.getBoundingClientRect().bottom) * 10) / 10 : null,
      });
    };
    const loop = () => { channel.port2.postMessage(0); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  });
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
  if (out) fs.writeFileSync(out, JSON.stringify(rec));

  const episodes = (bad) => {
    const found = [];
    let run = null;
    for (const frame of rec.frames) {
      const value = bad(frame);
      if (value !== null) {
        if (!run) run = { from: frame.t, to: frame.t, frames: 0, worst: 0 };
        run.to = frame.t; run.frames += 1; run.worst = Math.max(run.worst, value);
      } else if (run) { found.push(run); run = null; }
    }
    if (run) found.push(run);
    return found;
  };
  const flips = [];
  let previous = null;
  for (const frame of rec.frames) if (frame.following !== previous) { flips.push(`${frame.t}ms following=${frame.following}`); previous = frame.following; }
  console.log(`frames ${rec.frames.length}; marks ${rec.marks.map((m) => `${m.name}@${m.t}`).join(" ")}`);
  console.log(`following: ${flips.join(" | ")}; jump button shown in ${rec.frames.filter((f) => f.jump).length} frames`);
  const lag = episodes((f) => (f.hidden !== null && f.hidden > 4 && f.following === "true" ? f.hidden : null));
  console.log(`${lag.length} episodes where newest content was below the viewport (>4px) while following:`);
  for (const e of lag) console.log(`  ${e.from}-${e.to}ms  ${e.frames} frames  worst ${e.worst}px`);
  const last = rec.frames.at(-1);
  console.log(`final: scrollTop ${last.st} of ${last.sh - last.ch}, hidden ${last.hidden}px, following ${last.following}`);
  if (errors.length) console.log("page errors:", errors);
} finally { await browser.close(); }
