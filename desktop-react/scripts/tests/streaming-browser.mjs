// Run against Vite. Requires playwright-core and a Chromium installation.
// LOOM_PLAYWRIGHT_MODULE can point at an external playwright-core ESM entry.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({
  executablePath: process.env.LOOM_CHROMIUM_PATH,
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/streaming.html`);
  await page.waitForFunction(() => Boolean(window.renderStream));
  const frames = (count) => page.evaluate(async (n) => {
    for (let i = 0; i < n; i++) await new Promise(requestAnimationFrame);
  }, count);
  const length = () => page.locator(".markdown-body").last().evaluate((el) => el.textContent.length);

  await page.evaluate(() => window.renderStream("中".repeat(800), true));
  await frames(2);
  const before = await length();
  assert.ok(before > 0 && before < 800, "large first chunk must be buffered, even under StrictMode");
  const moved = await page.evaluate(() => {
    const previous = document.querySelector(".markdown-body").textContent.length;
    window.renderStream("中".repeat(800), false);
    return { previous, next: document.querySelector(".turn-final-answer .markdown-body").textContent.length };
  });
  // flushSync may also commit one already queued animation frame.
  assert.ok(moved.next >= moved.previous && moved.next - moved.previous <= 24,
    "final-answer relocation must preserve progress within one bounded paint");
  assert.equal(await page.locator(".is-receiving").count(), 0, "network indicator ends immediately");
  await page.waitForFunction(() => document.querySelector(".turn-final-answer .markdown-body").textContent.length === 800);

  await page.evaluate(() => { window.resetStream(); window.renderPlain("一", true); });
  await page.waitForFunction(() => document.querySelector(".stream-text-chunk")?.textContent === "一");
  await page.evaluate(() => { window.oldGlyph = document.querySelector(".stream-text-chunk"); window.renderPlain("一二", true); });
  await page.waitForFunction(() => document.querySelectorAll(".stream-text-chunk").length === 2);
  assert.ok(await page.evaluate(() => window.oldGlyph === document.querySelector(".stream-text-chunk")));
  assert.equal(await page.locator(".stream-text-chunk").last().evaluate(el => getComputedStyle(el).animationName), "stream-text-reveal");
  assert.ok(await page.locator(".stream-text-chunk").last().evaluate(el => el.getAnimations().length > 0), "new glyph must have an active animation, not just a class");

  const rich = "```js\nconst value = 1;\n```\n\nFormula $x^2$\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nTail";
  await page.evaluate((text) => { window.resetStream(); window.renderPlain(text, true); }, rich);
  await page.waitForFunction(() => document.querySelector(".markdown-body")?.textContent.endsWith("Tail"));
  await page.evaluate((text) => { window.oldCode = document.querySelector(".markdown-code-block"); window.renderPlain(text + " appended", true); }, rich);
  await page.waitForFunction(() => document.querySelector(".markdown-body")?.textContent.endsWith("appended"));
  assert.ok(await page.evaluate(() => window.oldCode === document.querySelector(".markdown-code-block")));
  assert.equal(await page.locator(".katex").count(), 1);
  assert.equal(await page.locator("table").count(), 1);

  await page.evaluate(() => { window.resetStream(); window.renderStream("<think>" + "思".repeat(800), true); });
  await frames(2);
  assert.ok(await length() < 800, "reasoning must use the same presentation buffer");
  await page.evaluate(() => window.renderStream("<think>" + "思".repeat(800), false, "interrupted"));
  assert.equal(await length(), 800, "interrupt flushes immediately without lingering animation");
  assert.equal(await page.locator(".is-streaming").count(), 0);

  await page.evaluate(() => { window.resetStream(); window.renderStream("历史".repeat(400), false, "completed", "history"); });
  assert.equal(await length(), 800, "history never replays typing");
  assert.equal(await page.locator(".stream-text-chunk").count(), 0, "history has no per-glyph nodes");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => { window.resetStream(); window.renderStream("中".repeat(800), true); });
  assert.equal(await length(), 800);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; window.renderStream("中".repeat(1600), true); });
  await page.waitForFunction(() => document.querySelector(".markdown-body").textContent.length === 1600);
  assert.deepEqual(errors, []);
  console.log("PASS: actual Transcript lifecycle, coarse chunks, final relocation, per-glyph fade, reasoning, interrupt, history, reduced motion, StrictMode");
} finally {
  await browser.close();
}
