import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/long-conversation.html?motion=1`);
  await page.locator(".turn-final-answer").nth(19).waitFor();
  await page.waitForTimeout(1200);
  const discovery = await page.evaluate(async () => {
    const original = window.getComputedStyle;
    let historicalStyleReads = 0;
    window.getComputedStyle = function(element, ...args) {
      const turn = element.closest(".turn-block");
      if (turn && turn.dataset.turnId !== "turn-19") historicalStyleReads++;
      return original.call(this, element, ...args);
    };
    try {
      // These are real App ancestor mutations on run boundaries and panel changes.
      for (let index = 0; index < 6; index++) {
        document.querySelector(".conversation-stage").classList.toggle("is-running");
        document.body.classList.toggle("loom-panel-motion");
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      return { historicalStyleReads };
    } finally { window.getComputedStyle = original; }
  });
  console.log(JSON.stringify({ discovery }));
  if (!process.env.LOOM_DISCOVERY_BASELINE) assert.ok(discovery.historicalStyleReads < 100,
    `ancestor class changes rediscovered offscreen history ${discovery.historicalStyleReads} times`);

  // Deferred discovery must still register old code when the reader reaches it.
  await page.evaluate(() => {
    const scroller = document.querySelector(".transcript-scroll");
    scroller.dispatchEvent(new WheelEvent("wheel", { deltaY: -100, bubbles: true }));
    scroller.scrollTop = 0;
  });
  await page.locator('.turn-block[data-turn-id="turn-0"] pre.loom-custom-scrollable').first().waitFor({ state: "attached" });
  const code = page.locator('.turn-block[data-turn-id="turn-0"] pre').first();
  await code.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const before = await code.evaluate(el => el.scrollLeft);
  await code.evaluate(el => { el.scrollLeft = 80; });
  assert.ok(await code.evaluate(el => el.scrollLeft) > before, "old code retains horizontal scrolling");
  await page.evaluate(() => window.longConversation.small());
  await page.locator(".markdown-body").filter({ hasText: "Small conversation" }).waitFor();
  await page.evaluate(() => window.longConversation.restore());
  await page.locator(".turn-final-answer").nth(19).waitFor();
  await page.waitForTimeout(1000);
  assert.ok(await page.locator(".transcript-scroll").evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight < 5));
  assert.deepEqual(errors, []);
  console.log("PASS: bounded ancestor discovery, old code scrolling and thread restoration");
} finally { await browser.close(); }
