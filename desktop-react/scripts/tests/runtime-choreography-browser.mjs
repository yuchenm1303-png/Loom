import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const base = { threadId: "thread-1", turnId: "turn-1", status: "completed" };
const user = { ...base, id: "user", type: "user_message", text: "Check the workspace" };
const wrapper = { ...base, id: "exec", type: "tool_call", toolName: "exec", arguments: { cmd: "npm test" } };
const processItem = { ...base, id: "process", type: "process", command: "npm test" };
const search = { ...base, id: "search", type: "tool_call", toolName: "search_workspace_text", arguments: { query: "login" } };
try {
  for (const language of ["zh-CN", "en"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(language => localStorage.setItem("loom.settings.language", language), language);
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=dark`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    await page.evaluate(() => window.motionFixture.strip([]));
    assert.equal(await page.locator(".run-progress-phase").textContent(), language === "en" ? "Thinking" : "正在思考");
    await page.evaluate(items => window.motionFixture.strip(items, "waiting_approval"), [user, wrapper, processItem, search]);
    await page.waitForFunction(() => Boolean(document.querySelector(".run-progress-phase .crossfade-out")));
    await page.waitForTimeout(80);
    const blend = await page.locator(".run-progress-phase").evaluate(el => ({
      old: Number(getComputedStyle(el.querySelector(".crossfade-out")).opacity),
      current: Number(getComputedStyle(el.querySelector(".crossfade-in")).opacity),
      oldTransform: getComputedStyle(el.querySelector(".crossfade-out")).transform,
    }));
    assert.ok(blend.old > 0 && blend.old < 1 && blend.current > 0 && blend.current < 1, "both phase labels really crossfade");
    assert.equal(blend.oldTransform, "none");
    assert.match(await page.locator(".run-progress-meta").textContent(), language === "en" ? /2 steps/ : /2 步/);
    assert.equal(await page.locator(".run-progress-meta-dot:visible").count(), 2);
    for (const width of [1180, 1000, 920]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.locator(".run-progress-token-group").isVisible(), false);
      assert.equal(await page.locator(".run-progress-meta-dot:visible").count(), 1, "hidden token count leaves no orphan separator");
    }
    await page.evaluate(() => document.documentElement.dataset.loomReducedMotion = "true");
    await page.evaluate(items => window.motionFixture.strip(items), [user, { ...search, status: "running" }]);
    await page.waitForFunction(() => !document.querySelector(".crossfade-out"));
    await page.evaluate(items => window.motionFixture.turn(items, false), [user, search]);
    assert.match(await page.locator(".turn-process-summary").textContent(), language === "en" ? /Searched 1 time/ : /搜索 1 次/);
    await page.setViewportSize({ width: 720, height: 900 });
    await page.evaluate(items => window.motionFixture.turn(items, true), [user, wrapper, processItem, { ...search, status: "running" }]);
    await page.locator(".task-flow-row").first().waitFor();
    const gap = await page.locator(".task-flow-row").first().evaluate(row => {
      const target = row.querySelector(".task-flow-primary").getBoundingClientRect();
      return row.querySelector(".task-flow-status").getBoundingClientRect().left - target.right;
    });
    assert.ok(gap >= 0 && gap <= 12, "the status follows the content at 720px");
    await page.close();
  }
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=light`);
  await page.waitForFunction(() => Boolean(window.liveFixture));
  await page.locator('[data-message-id="h-a1"]').waitFor();
  await page.evaluate(() => {
    const observer = new MutationObserver(() => {
      const outgoing = document.querySelector(".composer-mode-crossfade > .crossfade-out");
      if (outgoing) {
        window.composerExitSnapshot = { inert: outgoing.inert, hidden: outgoing.getAttribute("aria-hidden") };
        observer.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  });
  await page.evaluate(() => window.liveFixture.send());
  await page.waitForFunction(() => window.composerExitSnapshot);
  assert.deepEqual(await page.evaluate(() => window.composerExitSnapshot), { inert: true, hidden: "true" });
  const activeInput = page.locator(".composer-mode-crossfade > :not(.crossfade-out) textarea");
  await activeInput.fill("保留这个补充草稿");
  await page.waitForFunction(() => !document.querySelector(".composer-mode-crossfade > .crossfade-out"));
  await page.getByRole("button", { name: "Stop current turn", exact: true }).click();
  await page.waitForFunction(() => !document.querySelector(".composer.is-steering") && !document.querySelector(".composer-mode-crossfade > .crossfade-out"));
  assert.equal(await page.locator(".composer textarea").inputValue(), "保留这个补充草稿");
  assert.deepEqual(errors, []);
  await page.close();
  console.log("Run phases, merged counts, responsive separators, search copy, reduced motion and composer send/stop handoff passed.");
} finally { await browser.close(); }
