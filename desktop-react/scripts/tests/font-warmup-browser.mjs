import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

// The UI face is a variable font and each weight is an instance made on its first layout (7-10ms). After the app has
// been idle for a moment the weights the stylesheets use are already made, and one the stylesheets do not use is not:
// that difference is what the warm-up buys, and it is what this measures.
try {
  const page = await browser.newPage({ viewport: { width: 1180, height: 780 }, deviceScaleFactor: 1.5 });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html`);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  // The warm-up is one weight per idle slice; give it its time and wait for its probe to go.
  await page.waitForFunction(() => ![...document.body.children].some(node => node.getAttribute("aria-hidden") === "true" && node.textContent === "Aa"), null, { timeout: 30000, polling: 250 });
  const costs = await page.evaluate(() => {
    const first = weight => {
      const probe = document.createElement("span");
      probe.style.cssText = `position:fixed;left:-9999px;top:0;visibility:hidden;white-space:pre;font-weight:${weight}`;
      probe.textContent = "Aa";
      document.body.appendChild(probe);
      const start = performance.now();
      probe.getBoundingClientRect();
      const cost = performance.now() - start;
      probe.remove();
      return cost;
    };
    // Weights the stylesheets use (never laid out in the page by this script before), and ones they do not.
    return { listed: [545, 780, 710, 730].map(first), unlisted: [401, 403, 407, 411].map(first) };
  });
  const listed = Math.max(...costs.listed);
  const unlisted = Math.min(...costs.unlisted);
  assert.ok(listed < unlisted, `a weight the app uses costs ${listed.toFixed(1)}ms at most to lay out, one it does not ${unlisted.toFixed(1)}ms at least`);
  assert.ok(unlisted > 3, `the comparison is meaningful: a fresh instance costs ${unlisted.toFixed(1)}ms`);
  assert.deepEqual(errors, []);
  console.log(`PASS: font instances are made while the app is idle (a listed weight ${listed.toFixed(1)}ms, a fresh one ${unlisted.toFixed(1)}ms)`);
} finally {
  await browser.close();
}
