import assert from "node:assert/strict";
import fs from "node:fs";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const out = process.argv[2] || "../.cache/mobile-conversation-flow";
fs.mkdirSync(out, { recursive: true });
try {
  for (const [width, theme] of [[320, "dark"], [390, "light"]]) {
    const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true });
    page.setDefaultTimeout(15000);
    page.setDefaultNavigationTimeout(60000);
    await page.addInitScript(() => localStorage.setItem("loom.settings.language", "zh-CN"));
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?mobile=1&theme=${theme}`, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => Boolean(window.motionFixture));
    await page.evaluate(() => {
      window.flowItem = { id: "process-1", threadId: "thread-1", turnId: "turn-1", type: "process", command: "npm test", status: "running", stdout: "line-0\n", startedAt: new Date().toISOString() };
      window.motionFixture.turn([window.flowItem], true);
    });
    const row = page.locator(".wv-row").first();
    await row.waitFor();
    assert.ok(await row.evaluate(el => el.getBoundingClientRect().height >= 34), "compact tool rows keep a 34px thumb height");
    await page.locator(".wv-sub-text").waitFor();
    await page.evaluate(() => {
      window.tailNode = document.querySelector(".wv-sub-text");
      window.sequence = 0;
      window.outputTimer = setInterval(() => {
        window.flowItem = { ...window.flowItem, stdout: `line-${++window.sequence}\n` };
        window.motionFixture.turn([window.flowItem], true);
      }, 35);
    });
    await page.waitForFunction(() => Number(document.querySelector(".wv-sub-text")?.textContent.match(/line-(\d+)/)?.[1]) >= 3);
    assert.ok(await page.evaluate(() => window.tailNode === document.querySelector(".wv-sub-text")), "output deltas preserve the tail node, preventing repeated fade-in");
    await page.evaluate(() => clearInterval(window.outputTimer));
    assert.ok(!/shimmer/.test(await page.locator(".wv-verb").evaluate(el => getComputedStyle(el).animationName)), "tool copy stays still while its status mark moves: no shimmer, only its one birth fade");
    const ring = await row.locator(".wv-node").evaluate(el => getComputedStyle(el, "::before").animationName);
    assert.equal(ring, "none", "a running step on the phone has no spinning ring around its glyph");
    await row.click();
    await page.locator('.wv-detail[data-open="true"] .wv-sheet-output').waitFor();
    for (let i = 0; i < 4; i++) await row.click();
    assert.equal(await row.getAttribute("aria-expanded"), "true", "rapid reversals keep the final disclosure intent");
    await page.locator('.wv-detail[data-open="true"] .wv-sheet-output').waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: `${out}/tools-${theme}-${width}.png`, animations: "disabled" });
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert.equal(await row.locator(".wv-node").evaluate(el => getComputedStyle(el, "::before").animationName), "none");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
    assert.equal(await row.locator(".wv-node").evaluate(el => getComputedStyle(el, "::before").animationName), "none");
    await page.evaluate(() => {
      document.documentElement.dataset.loomReducedMotion = "false";
      window.motionFixture.turn([{ ...window.flowItem, status: "completed" }, { id: "answer", threadId: "thread-1", turnId: "turn-1", type: "assistant_message", phase: "final_answer", text: "检查完成，所有测试通过。", status: "completed" }], false);
    });
    await page.locator(".turn-final-answer").getByText("检查完成，所有测试通过。", { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector(".wv-row.is-executing"));
    assert.equal(await page.locator(".turn-final-answer").count(), 1);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("PASS: mobile live tool output, stable tail DOM, 34px compact rows, circular status motion, disclosure reversal, completion and both reduced-motion preferences");
} finally { await browser.close(); }
