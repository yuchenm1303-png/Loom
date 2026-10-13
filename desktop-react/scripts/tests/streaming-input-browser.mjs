import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/streaming.html`);
  await page.waitForFunction(() => Boolean(window.renderTypingStress));
  await page.evaluate(() => window.renderTypingStress());
  const input = page.locator("textarea");
  await input.focus();
  const text = "typing stays responsive while markdown output streams ".repeat(3);
  const latencies = [];
  for (const char of text) {
    const started = performance.now();
    await input.pressSequentially(char);
    latencies.push(performance.now() - started);
  }
  assert.equal(await input.inputValue(), text);
  assert.equal(await input.evaluate(node => node === document.activeElement), true);
  assert.deepEqual(errors, []);
  const p95 = latencies.sort((a, b) => a - b)[Math.floor(latencies.length * .95)];
  assert.ok(p95 < 250, `Input p95 ${p95.toFixed(1)}ms exceeded the responsiveness budget`);
  console.log(`Long streaming Markdown: ${text.length} keystrokes preserved; input p95 ${p95.toFixed(1)}ms.`);
} finally { await browser.close(); }
