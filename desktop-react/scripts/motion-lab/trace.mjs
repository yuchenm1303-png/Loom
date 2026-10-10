// Motion lab: per-frame geometry trace of a live run, with jump / flicker detection.
//   node trace.mjs <url> <out.json> [durationMs=30000] [width=1280] [height=860]
// A live-app.html url is sent to automatically once the composer is ready (add ?scenario=approval|error|decision|quick).
import fs from "node:fs";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const [url, out, duration = "30000", width = "1280", height = "860"] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const ids = new WeakMap();
    let next = 1;
    const SELECTOR = [
      "[data-row-key]", ".wv-stage", ".wv-line", ".wv-fold", ".wv-detail", ".wv-sub-fold", ".wv-earlier", ".process-notes-row",
      "[data-message-id]", ".pending-thinking-presence", ".live-reasoning", ".turn-process-grid", ".turn-process-header-shell",
      ".earlier-process-entry", ".process-handoff-slot", ".run-progress-frame", ".turn-final-answer", ".turn-artifacts-slot",
      ".entry-user_message", ".entry-approval", ".composer",
    ].join(",");
    const label = (el) => {
      if (el.dataset.rowKey) return `row:${el.dataset.rowKey}`;
      if (el.dataset.messageId) return `msg:${el.dataset.messageId}`;
      return [...el.classList].slice(0, 2).join(".");
    };
    const rec = { frames: [], marks: [], scroll: [] };
    window.__rec = rec;
    const t0 = performance.now();
    const loop = () => {
      const t = Math.round(performance.now() - t0);
      const rows = [];
      for (const el of document.querySelectorAll(SELECTOR)) {
        if (!ids.has(el)) ids.set(el, next++);
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        rows.push([ids.get(el), label(el), Math.round(r.top * 10) / 10, Math.round(r.height * 10) / 10, Math.round(Number(cs.opacity) * 100) / 100, Math.round(r.left * 10) / 10]);
      }
      const live = document.querySelector(".markdown-body.is-receiving");
      const sc = document.querySelector(".transcript-scroll");
      rec.frames.push({ t, tx: live ? live.textContent.length : -1, rows, st: sc ? Math.round(sc.scrollTop) : null, sh: sc ? sc.scrollHeight : null, ch: sc ? sc.clientHeight : null });
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    window.__mark = (name) => rec.marks.push({ t: Math.round(performance.now() - t0), name });
  });
  await page.goto(url);
  if (/live-app/.test(url)) {
    await page.waitForFunction(() => window.liveFixture && document.querySelector('.composer textarea:not([disabled])'), null, { timeout: 30000 });
    await page.waitForTimeout(800);
    await page.evaluate(() => window.__mark?.("send"));
    await page.evaluate(() => window.liveFixture.send());
  }
  await page.waitForTimeout(Number(duration));
  const data = await page.evaluate(() => window.__rec);
  fs.writeFileSync(out, JSON.stringify(data));
  console.log("frames", data.frames.length, errors.length ? errors : "");
} finally { await browser.close(); }
