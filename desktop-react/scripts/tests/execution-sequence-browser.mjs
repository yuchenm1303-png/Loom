import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const base = { threadId: "thread-1", turnId: "turn-1", status: "completed" };
const message = (id, text, extra = {}) => ({ ...base, id, type: "assistant_message", phase: "commentary", text, ...extra });
const tool = id => ({ ...base, id, type: "tool_call", toolName: "read_workspace_text", arguments: { path: `${id}.txt` }, result: id });
try {
  for (const theme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    const items = [{ ...base, id: "user", type: "user_message", text: "检查任务" }, message("intro", "先检查入口。"),
      tool("first"), message("finding", "入口已经确认。"), tool("second")];
    await page.evaluate(items => window.motionFixture.turn(items, true), items);
    await page.waitForFunction(() => document.querySelectorAll(".task-flow-row").length === 2);
    assert.equal(await page.locator(".task-flow-group").count(), 1);
    const ids = await page.locator(".task-flow-group [data-process-items]").evaluateAll(nodes => nodes.map(node => node.dataset.processItems));
    assert.deepEqual(ids, ["first", "finding", "second"], "retain prose between the correct tools");
    await page.locator(".task-flow-group-header").click();
    await page.waitForTimeout(350);
    assert.equal(await page.locator('[data-message-id="finding"]').count(), 1, "no duplicate preview message");
    await page.locator('[data-message-id="finding"]').waitFor({ state: "visible" });
    assert.equal(await page.locator(".task-flow-row").count(), 0);
    if (process.env.LOOM_EXECUTION_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_EXECUTION_SCREENSHOTS}/collapsed-${theme}.png` });
    await page.locator(".task-flow-group-header").click();
    await page.waitForTimeout(350);
    assert.equal(await page.locator(".task-flow-row").count(), 2);
    await page.evaluate(items => window.motionFixture.turn(items, true), [...items,
      message("streaming", "发现另一条入口。", { status: "streaming" })]);
    await page.locator('[data-message-id="streaming"]').waitFor({ state: "visible" });
    assert.equal(await page.locator(".task-flow-group.is-running").count(), 0,
      "streaming prose must not claim a completed tool is still running");
    // New prose and tools stay in the existing group without resetting disclosure.
    const group = await page.locator(".task-flow-group").elementHandle();
    const updated = [...items, message("streaming", "发现另一条入口。"), message("next", "验证结果一致。"), tool("third")];
    await page.evaluate(items => window.motionFixture.turn(items, true), updated);
    await page.waitForFunction(() => document.querySelectorAll(".task-flow-row").length === 3);
    assert.equal(await group.evaluate(node => node === document.querySelector(".task-flow-group")), true);
    await page.evaluate(items => window.motionFixture.turn(items, false), [...updated, message("final", "检查完成。", { phase: "final_answer" })]);
    await page.locator('[data-message-id="final"]').waitFor({ state: "visible" });
    assert.equal(await page.locator('.task-flow-group [data-message-id="final"]').count(), 0);
    assert.deepEqual(errors, []);
    if (process.env.LOOM_EXECUTION_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_EXECUTION_SCREENSHOTS}/execution-${theme}.png` });
    await page.close();
  }
  console.log("Execution sequence: ordering, collapse preview, stable append and final boundary passed in both themes.");
} finally { await browser.close(); }
