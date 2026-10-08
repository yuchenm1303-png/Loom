import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/runtime-motion.html`);
  await page.waitForFunction(() => Boolean(window.motionFixture));
  await page.evaluate(() => window.motionFixture.turn([
    { id: "user-markdown", threadId: "markdown", turnId: "one", type: "user_message",
      text: "```javascript\nconst userValue = 1;\n```\n\n$x^2$\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nhttps://example.com/docs\n\n![remote](https://example.com/pixel.png)\n\n```unknown-loom-language\nplain fallback\n```" },
    { id: "assistant-markdown", threadId: "markdown", turnId: "one", type: "assistant_message",
      text: "```python\ndef assistant_value():\n    return 2\n```", status: "completed", phase: "final_answer" },
  ], false));
  await page.locator(".user-rich-message .hljs-keyword").first().waitFor();
  await page.locator(".markdown-body .hljs-keyword").first().waitFor();
  assert.equal(await page.locator(".user-rich-message table tbody tr").count(), 1);
  assert.equal(await page.locator(".user-rich-message .katex").count(), 1);
  assert.equal(await page.locator(".user-link-card").getAttribute("href"), "https://example.com/docs");
  assert.equal(await page.locator('.user-rich-message img[src*="pixel"]').count(), 0);
  assert.match(await page.locator(".user-rich-message").textContent(), /plain fallback/);
  const reused = await page.evaluate(async () => {
    const { rehypeHighlightOnce } = await import("/src/components/markdownHighlight.ts");
    const first = rehypeHighlightOnce();
    return Array.from({ length: 100 }, () => rehypeHighlightOnce()).every(value => value === first);
  });
  assert.ok(reused, "rendering user and assistant messages shares the registered grammars");
  assert.deepEqual(errors, []);
  console.log("PASS: shared code highlighter, user/assistant language isolation, math, tables, links, unknown language and remote-image protection");
} finally { await browser.close(); }
