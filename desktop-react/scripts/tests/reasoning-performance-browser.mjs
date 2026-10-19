import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    window.reasoningVisibilityListeners = new Set();
    const add = EventTarget.prototype.addEventListener, remove = EventTarget.prototype.removeEventListener;
    EventTarget.prototype.addEventListener = function(type, handler, options) {
      if (this === document && type === "visibilitychange") window.reasoningVisibilityListeners.add(handler);
      return add.call(this, type, handler, options);
    };
    EventTarget.prototype.removeEventListener = function(type, handler, options) {
      if (this === document && type === "visibilitychange") window.reasoningVisibilityListeners.delete(handler);
      return remove.call(this, type, handler, options);
    };
  });
  if (process.env.LOOM_REASONING_BASELINE) {
    await page.route("**/src/components/Transcript.tsx*", async route => {
      const response = await route.fetch();
      const source = await response.text();
      assert.ok(source.includes("contentPresence.mounted ?"), "baseline must restore eager reasoning content");
      assert.ok(source.includes("content: displayedReasoning.current"));
      await route.fulfill({ response, body: source.replace("contentPresence.mounted ?", "hasReasoning ?")
        .replace("content: displayedReasoning.current", "content: reasoning") });
    });
  }
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/runtime-motion.html`);
  await page.waitForFunction(() => window.motionFixture);
  const metrics = await page.evaluate(() => {
    const section = "## Inspection\n\n" + "Detailed reasoning with **emphasis**. ".repeat(20)
      + "\n\n| Check | Status |\n| --- | --- |\n" + "| Configuration | complete |\n".repeat(12)
      + "\n\n```typescript\n" + "const result = { status: 'complete', count: 42 };\n".repeat(12) + "```\n\n";
    const reasoning = section.repeat(8) + "reasoning-tail";
    const items = Array.from({ length: 20 }, (_, index) => [
      { id: `user-${index}`, turnId: `turn-${index}`, threadId: "thread-1", type: "user_message", text: `Inspect ${index}` },
      { id: `answer-${index}`, turnId: `turn-${index}`, threadId: "thread-1", type: "assistant_message",
        phase: "final_answer", status: "completed", text: `Finished ${index}`, reasoning },
    ]).flat();
    window.reasoningItems = items;
    const listeners = window.reasoningVisibilityListeners.size;
    const started = performance.now();
    window.motionFixture.turn(items, false);
    return { mountMs: performance.now() - started, nodes: document.querySelectorAll("*").length,
      hiddenMarkdown: document.querySelectorAll(".live-reasoning .markdown-body").length,
      additionalVisibilityListeners: window.reasoningVisibilityListeners.size - listeners };
  });
  console.log(JSON.stringify(metrics));
  if (process.env.LOOM_REASONING_BASELINE) {
    assert.ok(metrics.hiddenMarkdown === 20);
  } else {
    assert.equal(metrics.hiddenMarkdown, 0);
    assert.equal(metrics.additionalVisibilityListeners, 0, "static history must not subscribe every message to visibility events");
    assert.ok(metrics.nodes < 2000, `collapsed reasoning created ${metrics.nodes} elements`);
    for (const viewport of [{ width: 1400, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      const button = page.locator(".live-reasoning-trigger").last();
      await button.scrollIntoViewIfNeeded();
      await button.click();
      await page.waitForFunction(() => document.querySelector(".live-reasoning.open .markdown-body")?.textContent.includes("reasoning-tail"));
      await page.waitForTimeout(300);
      const openedHeight = await page.locator(".live-reasoning-grid").last().evaluate(el => el.getBoundingClientRect().height);
      assert.ok(openedHeight > 100);
      await button.click();
      assert.equal(await page.locator(".live-reasoning-inner").last().getAttribute("inert"), "");
      await page.waitForTimeout(70);
      assert.ok(await page.locator(".live-reasoning-grid").last().evaluate(el => el.getBoundingClientRect().height) < openedHeight,
        "closing retains its fold animation");
      // Reverse an exit before its cleanup timer fires.
      await button.click();
      await page.waitForTimeout(350);
      assert.equal(await page.locator(".live-reasoning .markdown-body").count(), 1);
      await button.click();
      await page.waitForFunction(() => !document.querySelector(".live-reasoning .markdown-body"));
      await page.evaluate(() => {
        const index = window.reasoningItems.length - 1;
        window.reasoningItems[index] = { ...window.reasoningItems[index], reasoning: window.reasoningItems[index].reasoning + "\nlatest-hidden-text" };
        window.motionFixture.turn(window.reasoningItems.slice(), false);
      });
      assert.equal(await page.locator(".live-reasoning .markdown-body").count(), 0);
      await button.click();
      await page.waitForFunction(() => document.querySelector(".live-reasoning.open .markdown-body")?.textContent.includes("latest-hidden-text"));
      await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
      await button.click();
      await page.waitForFunction(() => !document.querySelector(".live-reasoning .markdown-body"));
      await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "false"; });
    }
    await page.evaluate(() => window.motionFixture.reset());
    assert.equal(await page.evaluate(() => window.reasoningVisibilityListeners.size), 0);
    assert.deepEqual(errors, []);
    console.log("PASS: lazy reasoning, current text on reopen, exit reversal, reduced motion, desktop/mobile and listener cleanup");
  }
} finally { await browser.close(); }
