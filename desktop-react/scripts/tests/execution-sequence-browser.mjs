import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const base = { threadId: "thread-1", turnId: "turn-1", status: "completed" };
const user = { ...base, id: "user", type: "user_message", text: "检查任务" };
const message = (id, text, extra = {}) => ({ ...base, id, type: "assistant_message", phase: "commentary", text, ...extra });
const tool = (id, extra = {}) => ({ ...base, id, type: "tool_call", toolName: "read_workspace_text", arguments: { path: `${id}.txt` }, result: id, ...extra });
/** A step's sentence while it streams: the runtime only gives it a phase when the response completes. */
const streaming = (id, text, extra = {}) => ({ ...base, id, type: "assistant_message", status: "streaming", text, ...extra });

/** Vertical offset between a row's icon and its text, and the left edge of the text, in pixels. */
const rowGeometry = selector => document.querySelectorAll(selector).length && [...document.querySelectorAll(selector)].map(row => {
  const middle = rect => (rect.top + rect.bottom) / 2;
  const range = document.createRange();
  range.selectNodeContents(row.querySelector(".task-flow-verb"));
  const text = range.getBoundingClientRect();
  const icon = row.querySelector(".task-flow-row-icon svg").getBoundingClientRect();
  return { dy: Math.abs(middle(icon) - middle(text)), textLeft: Math.round(text.left - row.getBoundingClientRect().left) };
});

try {
  for (const theme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    const render = (items, live = true) => page.evaluate(([items, live]) => window.motionFixture.turn(items, live), [items, live]);
    const rows = () => page.locator(".task-flow-row-wrap").count();

    // Per-step narration is not drawn by default; one switch shows it in its original order.
    const items = [user, message("intro", "先检查入口。"), tool("first"), message("finding", "入口已经确认。"), tool("second")];
    await render(items);
    await page.waitForFunction(() => document.querySelectorAll(".task-flow-row-wrap").length === 2);
    assert.equal(await page.locator(".task-flow-group").count(), 1);
    assert.equal(await page.locator('[data-message-id="finding"]').count(), 0, "per-step narration is not drawn by default");
    assert.equal(await page.locator('[data-message-id="intro"]').count(), 1, "the reply to the user is");
    const switchNotes = page.locator(".process-notes-toggle");
    assert.match(await switchNotes.innerText(), /1/, "the switch says how many notes it holds");
    await switchNotes.click();
    await page.locator('[data-message-id="finding"]').waitFor({ state: "visible" });
    const ids = await page.locator(".task-flow-group [data-process-items]").evaluateAll(nodes => nodes.map(node => node.dataset.processItems));
    assert.deepEqual(ids, ["first", "finding", "second"], "narration stays between the tools it came with");
    // Notes line up with the row labels, and the quiet type is not the message type.
    const alignment = await page.evaluate(() => {
      const left = element => { const range = document.createRange(); range.selectNodeContents(element); return range.getBoundingClientRect().left; };
      return { label: left(document.querySelector(".task-flow-group .task-flow-verb")), note: left(document.querySelector(".task-flow-list .task-flow-commentary .assistant-message")) };
    });
    assert.ok(Math.abs(alignment.label - alignment.note) <= 1, `notes sit under the row labels (${alignment.note} vs ${alignment.label})`);
    await switchNotes.click();
    await page.waitForFunction(() => !document.querySelector('[data-message-id="finding"]'));

    // A closed group mounts nothing when it has no message to show; opening restores the rows.
    await page.locator(".task-flow-group-header").click();
    await page.waitForTimeout(350);
    assert.equal(await rows(), 0, "a closed group mounts no rows");
    if (process.env.LOOM_EXECUTION_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_EXECUTION_SCREENSHOTS}/collapsed-${theme}.png` });
    await page.locator(".task-flow-group-header").click();
    await page.waitForTimeout(350);
    assert.equal(await rows(), 2);

    // Streaming narration is not drawn either, and does not make a finished tool look busy.
    await render([...items, message("streaming", "发现另一条入口。", { status: "streaming" })]);
    await page.waitForTimeout(300);
    assert.equal(await page.locator('[data-message-id="streaming"]').count(), 0);
    assert.equal(await page.locator(".task-flow-group.is-running").count(), 0,
      "streaming prose must not claim a completed tool is still running");

    // Icons and text share one centre line in every row, and the text column is the same.
    const plain = await page.evaluate(rowGeometry, ".task-flow-row");
    assert.ok(plain.every(row => row.dy <= 1), `icon and text are centred together: ${JSON.stringify(plain)}`);
    assert.equal(new Set(plain.map(row => row.textLeft)).size, 1, "every row's text starts in the same column");

    // New steps join the existing group without resetting it. A third step of the same kind turns the
    // run into one summary line, because the narration between them is not drawn.
    const group = await page.locator(".task-flow-group").elementHandle();
    const updated = [...items, message("next", "验证结果一致。"), tool("third")];
    await render(updated);
    await page.waitForFunction(() => document.querySelector(".task-flow-cluster"));
    assert.equal(await group.evaluate(node => node === document.querySelector(".task-flow-group")), true);

    // Four steps of one kind become one line whose rows mount only when it is opened.
    const burst = [user, message("intro", "先检查入口。"), tool("a"), tool("b"), tool("c"), tool("d")];
    await render(burst);
    await page.waitForFunction(() => document.querySelector(".task-flow-cluster"));
    assert.equal(await page.locator(".task-flow-cluster").count(), 1);
    assert.equal(await rows(), 0, "a closed cluster mounts none of its rows");
    assert.equal(await page.locator(".task-flow-cluster-body").count(), 0);
    const head = page.locator(".task-flow-cluster .task-flow-row").first();
    assert.match(await head.innerText(), /4/, "the line says how many steps it holds");
    const clusterGeometry = await page.evaluate(rowGeometry, ".task-flow-cluster .task-flow-row");
    assert.ok(clusterGeometry[0].dy <= 1, "the summary line is centred like a row");
    await head.click();
    await page.waitForFunction(() => document.querySelectorAll(".task-flow-cluster-body .task-flow-row-wrap").length === 4);
    assert.equal(await head.getAttribute("aria-expanded"), "true");
    await head.click();
    await page.waitForFunction(() => !document.querySelector(".task-flow-cluster-body"));

    // A step that is still running stays visible under a closed line, where the opened body would be.
    await render([...burst.slice(0, -1), tool("d", { status: "running" })]);
    await page.waitForFunction(() => document.querySelector(".task-flow-cluster-live .task-flow-row.is-active"));
    assert.equal(await page.locator(".task-flow-cluster-live .task-flow-row").count(), 1);
    await head.click();
    await page.waitForFunction(() => !document.querySelector(".task-flow-cluster-live"));
    await head.click();
    await render(burst);

    // The model's own report at a plan update is a message, not hidden narration.
    const reported = [user, message("intro", "先检查入口。"), tool("a"),
      message("report", "第一阶段通过。", { stepId: "step-plan" }), tool("plan", { toolName: "update_plan", arguments: { plan: [] }, stepId: "step-plan" })];
    await render(reported);
    await page.locator('[data-message-id="report"]').waitFor({ state: "visible" });
    assert.equal(await page.locator(".task-flow-reply").count(), 1);
    assert.equal(await page.locator(".task-flow-reply .assistant-message").evaluate(node => getComputedStyle(node).fontSize), "15px");
    const title = await page.evaluate(() => {
      const left = element => { const range = document.createRange(); range.selectNodeContents(element); return range.getBoundingClientRect().left; };
      return { title: left(document.querySelector(".task-flow-group-title")), reply: left(document.querySelector(".task-flow-reply .assistant-message")) };
    });
    assert.ok(Math.abs(title.title - title.reply) <= 1, `a message in a group lines up with its title (${title.reply} vs ${title.title})`);

    await render([...updated, message("final", "检查完成。", { phase: "final_answer" })], false);
    await page.locator('[data-message-id="final"]').waitFor({ state: "visible" });
    assert.equal(await page.locator('.task-flow-group [data-message-id="final"]').count(), 0);

    // A step's sentence streams before the runtime knows its role. Routine narration is never drawn,
    // not even while it streams; the reaction to a failed step is a message from its first word, and
    // either way nothing is drawn and then taken back.
    await page.evaluate(() => window.motionFixture.reset());
    const watch = text => page.evaluate(text => {
      window.__shown = false;
      window.__observer?.disconnect();
      window.__observer = new MutationObserver(() => { if (document.body.textContent.includes(text)) window.__shown = true; });
      window.__observer.observe(document.body, { subtree: true, childList: true, characterData: true });
    }, text);
    const routine = "继续看下一处。";
    await watch(routine);
    const worked = [user, message("intro", "先看一下环境。"), tool("a"), tool("b"), tool("c")];
    await render(worked);
    await render([...worked, streaming("n4", routine)]);
    await page.waitForTimeout(200);
    await render([...worked, streaming("n4", routine), tool("d", { status: "streaming_arguments", arguments: "" })]);
    await page.waitForTimeout(200);
    assert.equal(await page.locator(".task-flow-group").count(), 1, "the streaming command joins the same group");
    assert.equal(await page.locator(".task-flow-cluster-latest").innerText(), "c.txt", "the line names the latest step whose arguments are known");
    await render([...worked, message("n4", routine), tool("d", { status: "running" })]);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__shown), false, "routine narration was never on screen, not even while it streamed");
    // Asked for, it is a note from the first moment it streams, never a message that shrinks into one.
    await render([...worked, streaming("n4", routine), tool("d", { status: "streaming_arguments" })]);
    await page.locator(".process-notes-toggle").click();
    await page.locator('[data-message-id="n4"]').waitFor({ state: "visible" });
    assert.equal(await page.locator('.task-flow-commentary [data-message-id="n4"]').count(), 1, "shown as a note");
    await page.locator(".process-notes-toggle").click();

    // The model's reaction to a failed step is what the reader wants to hear: drawn from its first word,
    // inside the same work log, and still there when its command has started and finished.
    const reaction = "argv 里中文 GBK 编码被吞。改用全英文查询。";
    const failed = [user, message("intro", "先看一下环境。"), tool("a", { status: "failed" })];
    await render(failed);
    await watch(reaction);
    await render([...failed, streaming("r", reaction)]);
    await page.locator('.task-flow-group .task-flow-reply [data-message-id="r"]').waitFor({ state: "visible", timeout: 500 });
    await render([...failed, streaming("r", reaction), tool("b", { status: "streaming_arguments", arguments: "" })]);
    await page.waitForTimeout(250);
    await render([...failed, message("r", reaction), tool("b", { status: "running" })]);
    await page.waitForTimeout(250);
    await render([...failed, message("r", reaction), tool("b", { status: "failed" }), message("r2", "引号也被吞了。", {}), tool("c")]);
    await page.waitForTimeout(250);
    assert.equal(await page.locator(".task-flow-group").count(), 1, "one work log, with the reactions inside it");
    assert.deepEqual(await page.locator(".task-flow-group .task-flow-reply [data-message-id]").evaluateAll(nodes => nodes.map(node => node.dataset.messageId)), ["r", "r2"]);
    assert.equal(await page.locator(".process-notes-toggle").count(), 0, "nothing was held back, so there is no switch");
    assert.equal(await page.locator('.task-flow-reply [data-message-id="r"]').evaluate(node => getComputedStyle(node.querySelector(".assistant-message")).fontSize), "15px", "a message, not a note");
    await page.evaluate(() => window.__observer.disconnect());

    // What is long enough, or has waited long enough, is the answer on its way and is shown as it streams.
    await render([...worked, streaming("answer", "已经确认完毕。".repeat(30))]);
    await page.locator('[data-message-id="answer"]').waitFor({ state: "visible", timeout: 500 });
    assert.equal(await page.locator('.task-flow-commentary [data-message-id="answer"]').count(), 0, "a message, not a note");
    assert.equal(await page.locator('.task-flow-group [data-message-id="answer"]').count(), 0, "a long one may be the answer, so it is not tucked into the log");
    await render([...worked, streaming("slow", "还在写的回答")]);
    await page.waitForTimeout(400);
    assert.equal(await page.locator('[data-message-id="slow"]').count(), 0, "held back while its role is unknown");
    await page.locator('[data-message-id="slow"]').waitFor({ state: "visible", timeout: 5000 });
    // Once drawn it stays, even when its step turns out to be narration.
    await render([...worked, message("slow", "还在写的回答"), tool("e", { status: "running" })]);
    await page.waitForTimeout(250);
    assert.equal(await page.locator('[data-message-id="slow"]').count(), 1, "a sentence that was drawn is not taken back");

    // The first sentence of a turn answers the person at once, and stays that message.
    await page.evaluate(() => window.motionFixture.reset());
    await render([user, streaming("lead", "先看一下环境。")]);
    await page.locator('[data-message-id="lead"]').waitFor({ state: "visible", timeout: 500 });
    await render([user, streaming("lead", "先看一下环境。"), tool("a", { status: "streaming_arguments" })]);
    await page.waitForTimeout(200);
    assert.equal(await page.locator('[data-message-id="lead"]').count(), 1);
    assert.equal(await page.locator('.task-flow-commentary [data-message-id="lead"]').count(), 0);
    // So is the first sentence after a message sent mid-run, even though tool work came before it.
    const at = seconds => new Date(Date.UTC(2026, 9, 9, 12, 0, seconds)).toISOString();
    await render([{ ...user, createdAt: at(0) }, message("intro", { createdAt: at(1) }), tool("a", { createdAt: at(2) }),
      { ...base, id: "steer", type: "user_message", source: "steering", text: "再看看日志", submittedAt: at(3), createdAt: at(3) },
      streaming("reply", "好，看日志。", { createdAt: at(4) })]);
    await page.locator('[data-message-id="reply"]').waitFor({ state: "visible", timeout: 500 });
    // Even before its first word: that reply is still thinking, and shows it instead of leaving a gap.
    await render([{ ...user, createdAt: at(0) }, message("intro", { createdAt: at(1) }), tool("a", { createdAt: at(2) }),
      { ...base, id: "steer", type: "user_message", source: "steering", text: "再看看日志", submittedAt: at(3), createdAt: at(3) },
      streaming("thinking", "", { createdAt: at(4) })]);
    await page.locator('[data-message-id="thinking"]').waitFor({ state: "attached", timeout: 500 });
    assert.deepEqual(errors, []);
    if (process.env.LOOM_EXECUTION_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_EXECUTION_SCREENSHOTS}/execution-${theme}.png` });
    await page.close();
  }
  console.log("Execution sequence: hidden narration, ordered switch, lazy summary lines, running step, plan report, alignment and final boundary passed in both themes.");
} finally { await browser.close(); }
