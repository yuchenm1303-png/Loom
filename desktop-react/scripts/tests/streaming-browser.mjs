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
  await page.waitForFunction(() => document.querySelector(".markdown-body")?.textContent === "一");
  await page.evaluate(() => window.renderPlain("一二", true));
  await page.waitForFunction(() => document.querySelector(".markdown-body")?.textContent === "一二");
  assert.equal(await page.locator(".stream-text-chunk").count(), 0, "streaming text must not allocate one animated span per grapheme");

  await page.evaluate(() => { window.resetStream(); window.renderPlain("中".repeat(800), true); });
  await page.waitForFunction(() => document.querySelector(".markdown-body")?.textContent.length > 0);
  assert.ok(await page.locator(".markdown-body span").count() < 16, "plain streaming prose keeps a bounded DOM node count");

  const rich = "```js\nconst value = 1;\n```\n\nFormula $x^2$\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nTail";
  await page.evaluate((text) => { window.resetStream(); window.renderPlain(text, true); }, rich);
  await page.waitForFunction(() => document.querySelector(".markdown-body")?.textContent.endsWith("Tail"));
  await page.evaluate((text) => { window.oldCode = document.querySelector(".markdown-code-block"); window.renderPlain(text + " appended", true); }, rich);
  await page.waitForFunction(() => document.querySelector(".markdown-body")?.textContent.endsWith("appended"));
  assert.ok(await page.evaluate(() => window.oldCode === document.querySelector(".markdown-code-block")));
  assert.equal(await page.locator(".katex").count(), 1);
  assert.equal(await page.locator("table").count(), 1);

  await page.evaluate(() => { window.resetStream(); window.renderStream("<think>" + "思".repeat(800), true); });
  // Collapsed reasoning is synchronized immediately and must not hold tools.
  assert.equal(await length(), 800);
  await page.locator(".live-reasoning-trigger").click();
  await page.evaluate(() => window.renderStream("<think>" + "思".repeat(1600), true));
  await frames(2);
  assert.ok(await length() < 1600, "expanded reasoning must use the same presentation buffer");
  await page.evaluate(() => window.renderStream("<think>" + "思".repeat(1600), false, "interrupted"));
  assert.equal(await length(), 1600, "interrupt flushes immediately without lingering animation");
  assert.equal(await page.locator(".is-streaming").count(), 0);

  await page.evaluate(() => {
    window.resetStream();
    window.renderItems([
      { id: "reasoning", threadId: "thread-1", turnId: "turn-1", type: "assistant_message", reasoning: "思".repeat(12000), text: "", status: "completed" },
      { id: "tool", threadId: "thread-1", turnId: "turn-1", type: "tool_call", toolName: "exec_command", status: "running", arguments: { cmd: "echo test" } },
    ], true);
  });
  await frames(2);
  assert.equal(await page.locator(".entry-activity").count(), 1, "collapsed reasoning cannot delay a tool entrance");

  await page.evaluate(() => {
    window.resetStream();
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
    window.renderPlain("后台".repeat(8000), true);
  });
  assert.equal(await length(), 16000, "background updates synchronize without rAF backlog");
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
  });

  await page.evaluate(() => { window.resetStream(); window.renderStream("历史".repeat(400), false, "completed", "history"); });
  assert.equal(await length(), 800, "history never replays typing");
  assert.equal(await page.locator(".stream-text-chunk").count(), 0, "history has no per-glyph nodes");

  const longTable = "| Name | Result |\n| --- | --- |\n" + "| Streaming row | ✅ completed with full details |\n".repeat(400);
  await page.evaluate((text) => { window.resetStream(); window.renderPlain(text, true); }, longTable);
  await frames(3);
  await page.evaluate((text) => window.renderPlain(text, false), longTable);
  await page.waitForFunction(() => document.querySelectorAll("tbody tr").length === 400, { timeout: 2500 });
  assert.equal(await page.locator(".is-streaming").count(), 0, "long final backlog drains before settling finishes");

  await page.evaluate((text) => { window.resetStream(); window.renderScrolled(text, false); }, longTable);
  await page.waitForFunction(() => {
    const el = document.querySelector(".transcript-scroll");
    return el.scrollHeight - el.clientHeight - el.scrollTop < 3;
  });
  await page.locator(".transcript-scroll").hover();
  await page.mouse.wheel(0, -600);
  await page.waitForFunction(() => document.querySelector(".transcript-jump-latest").classList.contains("is-visible"));
  const detachedTop = await page.locator(".transcript-scroll").evaluate((el) => el.scrollTop);
  await page.evaluate((text) => window.renderScrolled(text + "\n\nMore output", true), longTable);
  await frames(20);
  const stillDetached = await page.locator(".transcript-scroll").evaluate((el) => el.scrollTop);
  assert.ok(Math.abs(detachedTop - stillDetached) < 3, "streaming must not pull a reader away from history");
  await page.locator(".transcript-jump-latest").click();
  await page.waitForFunction(() => {
    const el = document.querySelector(".transcript-scroll");
    return el.scrollHeight - el.clientHeight - el.scrollTop < 3;
  });

  const customThumb = page.locator(".loom-scroll-y:not([hidden]) .loom-scroll-thumb").first();
  await customThumb.focus(); await page.keyboard.press("Home");
  await page.evaluate((text) => window.renderScrolled(text + "\n\nMore output and another delta", true), longTable);
  await frames(12);
  assert.equal(await page.locator(".transcript-scroll").evaluate((el) => el.scrollTop), 0,
    "custom scrollbar input detaches from live follow");
  await customThumb.focus(); await page.keyboard.press("End");
  await page.waitForFunction(() => !document.querySelector(".transcript-jump-latest").classList.contains("is-visible"));

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => { window.resetStream(); window.renderStream("中".repeat(800), true); });
  assert.equal(await length(), 800);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; window.renderStream("中".repeat(1600), true); });
  await page.waitForFunction(() => document.querySelector(".markdown-body").textContent.length === 1600);
  assert.deepEqual(errors, []);
  console.log("PASS: Transcript lifecycle, long tables, scroll detachment/return, final relocation, bounded DOM, collapsed/expanded reasoning, interrupt, history, reduced motion, StrictMode");
} finally {
  await browser.close();
}
