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
    // Work-log commentary reads as a quiet caption, not a second message: smaller than
    // the opening message, muted unless it is the newest note, and with no message toolbar.
    const notes = await page.evaluate(() => {
      const size = el => parseFloat(getComputedStyle(el.querySelector(".assistant-message")).fontSize);
      const ink = el => getComputedStyle(el.querySelector(".assistant-message")).color;
      const all = [...document.querySelectorAll(".task-flow-commentary")];
      return {
        intro: size(document.querySelector('[data-message-id="intro"]')),
        sizes: all.map(note => size(note)),
        latestFlags: all.map(note => note.classList.contains("is-latest")),
        mutedDiffersFromLatest: ink(all[0]) !== ink(all[all.length - 1]),
        toolbars: document.querySelectorAll(".task-flow-commentary .message-meta").length,
        shells: all.every(note => note.querySelector(".assistant-message-shell.is-note")),
      };
    });
    assert.ok(notes.sizes.length >= 2 && notes.sizes.every(value => value < notes.intro), "notes are smaller than the opening message");
    assert.deepEqual(notes.latestFlags.filter(Boolean).length, 1, "exactly one note is the newest");
    assert.equal(notes.latestFlags.at(-1), true, "the newest note is last in document order");
    assert.equal(notes.mutedDiffersFromLatest, true, "older notes are quieter than the newest");
    assert.equal(notes.toolbars, 0);
    assert.equal(notes.shells, true);
    // A long note keeps its full text behind one click and clamps again on demand.
    const long = Array.from({ length: 60 }, (_, index) => "第" + (index + 1) + "步已确认。").join("");
    await page.evaluate(items => window.motionFixture.turn(items, true), [...updated, message("long", long), tool("fourth")]);
    const shell = page.locator('[data-message-id="long"]');
    await shell.waitFor({ state: "visible" });
    assert.equal(await shell.evaluate(node => node.classList.contains("is-clamped")), true);
    const collapsed = await shell.evaluate(node => node.getBoundingClientRect().height);
    const toggle = shell.locator(".note-toggle");
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    await toggle.click();
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");
    assert.equal(await shell.evaluate(node => node.classList.contains("is-clamped")), false);
    assert.ok(await shell.evaluate(node => node.getBoundingClientRect().height) > collapsed, "expanding reveals the rest of the note");
    assert.ok((await shell.locator(".markdown-body").innerText()).endsWith("第60步已确认。"), "no text is dropped");
    await toggle.click();
    assert.equal(await shell.evaluate(node => node.classList.contains("is-clamped")), true);
    assert.equal(await page.locator('[data-message-id="next"] .note-toggle').count(), 0, "short notes never get a toggle");
    await page.evaluate(items => window.motionFixture.turn(items, true), updated);
    await page.waitForFunction(() => document.querySelectorAll(".task-flow-row").length === 3);
    await page.evaluate(items => window.motionFixture.turn(items, false), [...updated, message("final", "检查完成。", { phase: "final_answer" })]);
    await page.locator('[data-message-id="final"]').waitFor({ state: "visible" });
    assert.equal(await page.locator('.task-flow-group [data-message-id="final"]').count(), 0);
    assert.deepEqual(errors, []);
    if (process.env.LOOM_EXECUTION_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_EXECUTION_SCREENSHOTS}/execution-${theme}.png` });
    await page.close();
  }
  console.log("Execution sequence: ordering, collapse preview, stable append and final boundary passed in both themes.");
} finally { await browser.close(); }
