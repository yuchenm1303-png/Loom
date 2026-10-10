// Motion lab: replay a fixture and save screenshots every `interval` ms.
//   node film.mjs <url> <outDir> [intervalMs=1000] [durationMs=34000] [width=1280] [height=860]
import fs from "node:fs";
import path from "node:path";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const [url, out, interval = "1000", duration = "34000", width = "1280", height = "860"] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: Number(process.env.DSF || 1) });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(url);
  if (/live-app/.test(url)) {
    await page.waitForFunction(() => window.liveFixture && document.querySelector('.composer textarea:not([disabled])'), null, { timeout: 30000 });
    await page.waitForTimeout(800);
    await page.evaluate(() => window.__mark?.("send"));
    await page.evaluate(() => window.liveFixture.send());
  }
  const started = Date.now();
  let index = 0;
  while (Date.now() - started < Number(duration)) {
    const name = String(index++).padStart(3, "0");
    await page.screenshot({ path: path.join(out, `f${name}.png`) });
    const spent = Date.now() - started - index * Number(interval);
    if (spent < 0) await page.waitForTimeout(-spent);
  }
  console.log(`saved ${index} frames`, errors.length ? errors : "");
} finally { await browser.close(); }
