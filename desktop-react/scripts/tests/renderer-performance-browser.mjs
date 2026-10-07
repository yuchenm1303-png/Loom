// Real App, production styles and notification path; no live Host or model.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?performance=1&navigation=1&motion=1`);
  await page.locator(".turn-final-answer").nth(19).waitFor();
  await page.waitForTimeout(500);
  assert.equal(await page.locator(".runtime-event").count(), 0, "a closed inspector must not construct 2,000 invisible events");
  const beforeNodes = await page.evaluate(() => document.querySelectorAll("*").length);
  assert.ok(beforeNodes < 3000, `folded history created ${beforeNodes} live DOM elements`);
  const beforeReads = await page.evaluate(() => window.performanceFixture.historyReads());
  await page.evaluate(async () => {
    for (let index = 0; index < 40; index++) {
      window.performanceFixture.backgroundUpdate(index);
      await new Promise(resolve => setTimeout(resolve, 25));
    }
  });
  assert.equal(await page.evaluate(() => window.performanceFixture.historyReads()), beforeReads,
    "background thread updates must not scan or rebuild the active transcript");
  assert.equal(await page.locator('.compact-thread-row[data-thread-id="visual-1"] .compact-thread-dot.running').count(), 1,
    "background status still reaches the sidebar");
  const inspector = page.locator(".thread-inspector-button");
  await inspector.click();
  await page.waitForFunction(() => document.querySelectorAll(".runtime-event").length === 2000);
  await page.locator(".runtime-event-main").first().evaluate(el => el.click());
  assert.match(await page.locator(".runtime-event-detail.open pre").textContent(), /fixture output/);
  await inspector.click();
  await page.waitForTimeout(60);
  await inspector.click();
  assert.equal(await page.locator(".runtime-event").count(), 2000, "reversing an exit preserves the panel until the new lifecycle settles");
  await inspector.click();
  await page.waitForFunction(() => !document.querySelector(".runtime-event"));
  assert.ok(await page.evaluate(() => document.querySelectorAll("*").length) < 3000);
  const client = await page.context().newCDPSession(page);
  const memory = async () => {
    await client.send("HeapProfiler.collectGarbage");
    return client.send("Memory.getDOMCounters");
  };
  // Compare the same small conversation before and after repeatedly opening long history.
  const select = id => page.locator(`.compact-thread-row[data-thread-id="${id}"] .compact-thread-main`).evaluate(el => el.click());
  await select("visual-2");
  await page.locator(".markdown-body").filter({ hasText: "Loaded visual-2" }).waitFor();
  await page.waitForTimeout(500);
  const baseline = await memory();
  for (let index = 0; index < 12; index++) {
    await select("visual-0");
    await page.locator(".turn-final-answer").nth(19).waitFor();
    await select("visual-2");
    await page.locator(".markdown-body").filter({ hasText: "Loaded visual-2" }).waitFor();
  }
  await page.waitForTimeout(500);
  const final = await memory();
  assert.equal(await page.evaluate(() => window.performanceFixture.listenerCount()), 1);
  assert.equal(final.jsEventListeners, baseline.jsEventListeners, "switching must release old listeners");
  assert.ok(final.nodes <= baseline.nodes + 100, `old DOM accumulated: ${baseline.nodes} -> ${final.nodes}`);
  assert.equal(final.documents, baseline.documents);
  await page.locator(".sidebar-new-conversation").click();
  await page.locator(".empty-state").waitFor();
  const emptyReads = await page.evaluate(() => window.performanceFixture.historyReads());
  await page.evaluate(async () => {
    for (let index = 0; index < 10; index++) {
      window.performanceFixture.backgroundUpdate(index);
      await new Promise(resolve => setTimeout(resolve, 25));
    }
  });
  assert.equal(await page.evaluate(() => window.performanceFixture.historyReads()), emptyReads,
    "background work must not revisit the previous history on the new-conversation screen");
  assert.equal(await page.locator(".runtime-event").count(), 0);
  assert.equal(await page.locator(".turn-block").count(), 0);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ beforeNodes, baseline, final }));
  console.log("PASS: hidden inspector teardown, background update isolation, exit reversal, 12 long-history switches and new conversation");
} finally { await browser.close(); }
