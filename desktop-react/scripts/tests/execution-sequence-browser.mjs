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

/** Vertical offset between a row's glyph and its text, and the left edge of the text, in pixels. */
const rowGeometry = selector => document.querySelectorAll(selector).length && [...document.querySelectorAll(selector)].map(row => {
  const middle = rect => (rect.top + rect.bottom) / 2;
  const range = document.createRange();
  range.selectNodeContents(row.querySelector(".wv-verb"));
  const text = range.getBoundingClientRect();
  const glyph = row.querySelector(".wv-glyph svg").getBoundingClientRect();
  return { dy: Math.abs(middle(glyph) - middle(text)), textLeft: Math.round(text.left - row.getBoundingClientRect().left) };
});

try {
  for (const theme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    const render = (items, live = true) => page.evaluate(([items, live]) => window.motionFixture.turn(items, live), [items, live]);
    const rows = () => page.locator(".wv-step").count();

    // Per-step narration is not drawn by default; one switch shows it in its original order.
    const items = [user, message("intro", "先检查入口。"), tool("first"), message("finding", "入口已经确认。"), tool("second")];
    await render(items);
    await page.waitForFunction(() => document.querySelectorAll(".wv-step").length === 2);
    assert.equal(await page.locator(".wv-stage").count(), 1);
    assert.equal(await page.locator('[data-message-id="finding"]').count(), 0, "per-step narration is not drawn by default");
    assert.equal(await page.locator('[data-message-id="intro"]').count(), 1, "the reply to the user is");
    const switchNotes = page.locator(".process-notes-toggle");
    assert.match(await switchNotes.innerText(), /1/, "the switch says how many notes it holds");
    await switchNotes.click();
    await page.locator('[data-message-id="finding"]').waitFor({ state: "visible" });
    const ids = await page.locator(".wv-stage [data-process-items]").evaluateAll(nodes => nodes.map(node => node.dataset.processItems));
    assert.deepEqual(ids, ["first", "finding", "second"], "narration stays between the tools it came with");
    // Notes line up with the row labels, and the quiet type is not the message type.
    const alignment = await page.evaluate(() => {
      const left = element => { const range = document.createRange(); range.selectNodeContents(element); return range.getBoundingClientRect().left; };
      return { label: left(document.querySelector(".wv-stage .wv-verb")), note: left(document.querySelector(".wv-list .wv-note .assistant-message")) };
    });
    assert.ok(Math.abs(alignment.label - alignment.note) <= 1, `notes sit under the row labels (${alignment.note} vs ${alignment.label})`);
    await switchNotes.click();
    await page.waitForFunction(() => !document.querySelector('[data-message-id="finding"]'));

    // Streaming narration is not drawn either, and does not make a finished tool look busy.
    await render([...items, message("streaming", "发现另一条入口。", { status: "streaming" })]);
    await page.waitForTimeout(300);
    assert.equal(await page.locator('[data-message-id="streaming"]').count(), 0);
    assert.equal(await page.locator(".wv-stage.is-running").count(), 0,
      "streaming prose must not claim a completed tool is still running");

    // Glyphs and text share one centre line in every row, and the text column is the same.
    const plain = await page.evaluate(rowGeometry, ".wv-row");
    assert.ok(plain.every(row => row.dy <= 1), `glyph and text are centred together: ${JSON.stringify(plain)}`);
    assert.equal(new Set(plain.map(row => row.textLeft)).size, 1, "every row's text starts in the same column");

    // New steps join the existing stage without resetting it, and the log never turns one kind of step into
    // anything but rows while it is short: nothing is summarised until it is over or long.
    const stage = await page.locator(".wv-stage").elementHandle();
    const updated = [...items, message("next", "验证结果一致。"), tool("third")];
    await render(updated);
    await page.waitForFunction(() => document.querySelectorAll(".wv-step").length === 3);
    assert.equal(await stage.evaluate(node => node === document.querySelector(".wv-stage")), true);
    assert.equal(await page.locator(".wv-line").count(), 0, "a short live stage has no summary line");

    // A stage that grows long keeps its newest steps as rows and folds the older ones into one line.
    const burst = [user, message("intro", "先检查入口。"), ...["a", "b", "c", "d", "e", "f", "g", "h"].map(id => tool(id))];
    await render(burst);
    await page.waitForFunction(() => document.querySelector(".wv-earlier .wv-line"));
    await page.waitForFunction(() => document.querySelectorAll(".wv-step").length === 6, null, { timeout: 2000 });
    assert.equal(await rows(), 6, "only the newest six steps stay as rows; the folded ones are released once they have collapsed");
    const head = page.locator(".wv-earlier .wv-line");
    assert.match(await head.innerText(), /读取 2 个文件/, "the line says what it holds");
    assert.equal(await page.locator(".wv-earlier .wv-beads i").count(), 2, "one bead per folded step");
    assert.equal(await page.locator(".wv-earlier .wv-list").count(), 0, "folded rows are not mounted until the line is opened");
    const lineGeometry = await page.evaluate(() => {
      const middle = rect => (rect.left + rect.right) / 2;
      return { lead: middle(document.querySelector(".wv-earlier .wv-line-lead").getBoundingClientRect()), node: middle(document.querySelector(".wv-step .wv-node").getBoundingClientRect()) };
    });
    assert.ok(Math.abs(lineGeometry.lead - lineGeometry.node) <= 1.5, `the line and the rows share one glyph column (${lineGeometry.lead} vs ${lineGeometry.node})`);
    await head.click();
    await page.waitForFunction(() => document.querySelectorAll(".wv-earlier .wv-step").length === 2);
    assert.equal(await head.getAttribute("aria-expanded"), "true");
    await head.click();
    await page.waitForFunction(() => !document.querySelector(".wv-earlier .wv-step"));

    // The step that is running is always among the rows, where the reader is looking.
    await render([...burst.slice(0, -1), tool("h", { status: "running" })]);
    await page.waitForFunction(() => document.querySelector(".wv-step:last-child .wv-row.is-active"));
    assert.equal(await page.locator('.wv-step[data-retired="true"]').count() <= 3, true, "folding does not hide the step that is running");
    await render(burst);

    // The model's own report at a plan update is a message, not hidden narration. Like the first reply it is
    // body text: it stands between the tool groups, never inside one.
    const reported = [user, message("intro", "先检查入口。"), tool("a"),
      message("report", "第一阶段通过。", { stepId: "step-plan" }), tool("plan", { toolName: "update_plan", arguments: { plan: [] }, stepId: "step-plan" })];
    await render(reported);
    await page.locator('[data-message-id="report"]').waitFor({ state: "visible" });
    assert.equal(await page.locator('.wv-stage [data-message-id="report"]').count(), 0, "a message is not tucked into a tool group");
    assert.equal(await page.locator('[data-message-id="report"] .assistant-message').evaluate(node => getComputedStyle(node).fontSize), "15px");
    const edge = await page.evaluate(() => {
      const left = element => { const range = document.createRange(); range.selectNodeContents(element); return range.getBoundingClientRect().left; };
      return { intro: left(document.querySelector('[data-message-id="intro"] .assistant-message')), report: left(document.querySelector('[data-message-id="report"] .assistant-message')) };
    });
    assert.ok(Math.abs(edge.intro - edge.report) <= 1, `every message starts at the same left edge (${edge.report} vs ${edge.intro})`);

    await render([...updated, message("final", "检查完成。", { phase: "final_answer" })], false);
    await page.locator('[data-message-id="final"]').waitFor({ state: "visible" });
    assert.equal(await page.locator('.wv-stage [data-message-id="final"]').count(), 0);

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
    await page.waitForTimeout(300);
    assert.equal(await page.locator(".wv-stage").count(), 1, "the streaming command joins the same stage");
    assert.equal(await rows(), 4, "and is one more row in it, born where the others are");
    await render([...worked, message("n4", routine), tool("d", { status: "running" })]);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__shown), false, "routine narration was never on screen, not even while it streamed");
    // Asked for, it is a note from the first moment it streams, never a message that shrinks into one.
    await render([...worked, streaming("n4", routine), tool("d", { status: "streaming_arguments" })]);
    await page.locator(".process-notes-toggle").click();
    await page.locator('[data-message-id="n4"]').waitFor({ state: "visible" });
    assert.equal(await page.locator('.wv-note [data-message-id="n4"]').count(), 1, "shown as a note");
    await page.locator(".process-notes-toggle").click();

    // A step that thinks first and then writes a short routine sentence is the same: its header shows the thinking, and
    // the sentence is not typed out and then taken back when the step turns out to be narration.
    const afterThought = "这一步换个办法。";
    await page.evaluate(() => window.motionFixture.reset());
    await render(worked);
    await watch(afterThought);
    await render([...worked, streaming("n5", afterThought, { reasoning: "上一步不行，要换个办法。" })]);
    await page.locator(".live-reasoning").waitFor({ state: "attached", timeout: 500 });
    await page.waitForTimeout(250);
    await render([...worked, message("n5", afterThought, { reasoning: "上一步不行，要换个办法。" }), tool("d", { status: "running" })]);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__shown), false, "the sentence that came with reasoning was never on screen");

    // The model's reaction to a failed step is what the reader wants to hear: body text from its first word,
    // standing between the tool groups, and still there when its command has started and finished.
    const reaction = "argv 里中文 GBK 编码被吞。改用全英文查询。";
    const failed = [user, message("intro", "先看一下环境。"), tool("a", { status: "failed" })];
    // The page as a reader sees it, top to bottom: the user's message, messages and tool groups.
    const outline = () => page.evaluate(() => [...document.querySelectorAll(".wv-stage, [data-message-id]")]
      .map(node => node.matches(".wv-stage") ? "group" : (node.closest(".wv-stage") ? `in-group:${node.dataset.messageId}` : node.dataset.messageId)));
    await render(failed);
    await watch(reaction);
    await render([...failed, streaming("r", reaction)]);
    await page.locator('[data-message-id="r"]').waitFor({ state: "visible", timeout: 500 });
    const reactionNode = await page.locator('[data-message-id="r"]').elementHandle();
    assert.deepEqual(await outline(), ["user", "intro", "group", "r"]);
    await render([...failed, streaming("r", reaction), tool("b", { status: "streaming_arguments", arguments: "" })]);
    await page.waitForTimeout(250);
    assert.deepEqual(await outline(), ["user", "intro", "group", "r", "group"], "its command starts a group of its own below it");
    await render([...failed, message("r", reaction), tool("b", { status: "running" })]);
    await page.waitForTimeout(250);
    await render([...failed, message("r", reaction), tool("b", { status: "failed" }), message("r2", "引号也被吞了。", {}), tool("c")]);
    await page.waitForTimeout(250);
    assert.deepEqual(await outline(), ["user", "intro", "group", "r", "group", "r2", "group"], "failure, reaction, failure, reaction, next try");
    assert.equal(await reactionNode.evaluate(node => node.isConnected && node === document.querySelector('[data-message-id="r"]')), true, "the sentence never moved or was redrawn");
    assert.equal(await page.locator(".process-notes-toggle").count(), 0, "nothing was held back, so there is no switch");
    assert.equal(await page.locator('[data-message-id="r"]').evaluate(node => node.classList.contains("is-note") || getComputedStyle(node.querySelector(".assistant-message")).fontSize), "15px", "a message, not a note");
    await page.evaluate(() => window.__observer.disconnect());

    // What is long enough, or has waited long enough, is the answer on its way and is shown as it streams.
    await render([...worked, streaming("answer", "已经确认完毕。".repeat(30))]);
    await page.locator('[data-message-id="answer"]').waitFor({ state: "visible", timeout: 500 });
    assert.equal(await page.locator('.wv-note [data-message-id="answer"]').count(), 0, "a message, not a note");
    assert.equal(await page.locator('.wv-stage [data-message-id="answer"]').count(), 0, "a long one may be the answer, so it is not tucked into the log");
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
    assert.equal(await page.locator('.wv-note [data-message-id="lead"]').count(), 0);
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
    // A step that goes straight to a tool call writes no words. While its arguments stream (a large file can
    // take many seconds) the row speaks for it: no "thinking" header may sit next to a step that is already running.
    await page.evaluate(() => window.motionFixture.reset());
    await render([user, message("lead", "", { status: "streaming" }), tool("t", { status: "streaming_arguments", arguments: "" })]);
    await page.waitForTimeout(500);
    assert.equal(await page.locator(".wv-row").count(), 1, "the tool row is drawn");
    assert.equal(await page.locator(".live-reasoning, .inline-thinking").count(), 0, "and nothing says the model is thinking beside it");
    // While the run is live the model's messages are body text between the tool groups. When it completes the
    // whole process folds: the summary line and the final report are all that remain, and the messages and
    // tool groups come back, in order, only when the reader opens the log.
    await page.evaluate(() => window.motionFixture.reset());
    const stages = () => page.evaluate(() => [...document.querySelectorAll(".wv-stage")].map(stage => ({
      collapsed: stage.classList.contains("is-collapsible") && !stage.classList.contains("is-open"),
      summary: stage.querySelector(".wv-line-text")?.textContent ?? null, failed: stage.querySelector(".wv-line-failed")?.textContent ?? null,
      rows: stage.querySelectorAll(".wv-step").length })));
    const gridHeight = () => page.evaluate(() => document.querySelector(".turn-process-grid")?.getBoundingClientRect().height ?? null);
    const story = [user, message("a1", "先看一下环境。"), tool("t1", { status: "failed" }), message("a2", "引号被吞了，改用单引号。"), tool("t2"),
      message("quiet", "继续。"), tool("t3", { status: "failed" }), message("a3", "编码问题，改用 PowerShell。"), tool("t4")];
    const answer = message("final", "环境探完了。", { phase: "final_answer" });
    const liveOutline = ["user", "a1", "group", "a2", "group", "a3", "group"];
    await render(story);
    await page.waitForTimeout(400);
    assert.deepEqual(await outline(), liveOutline, "messages between tool groups, hidden narration nowhere");
    const liveStages = await stages();
    assert.deepEqual(liveStages[1], { collapsed: true, summary: "读取 2 个文件", failed: "失败 1", rows: 0 }, "the stage that is over is one line while the run is live");
    const liveHeight = await gridHeight();
    await render([...story, answer], false);
    assert.deepEqual(await stages(), liveStages, "completion opens and closes no stage: the layout is as the reader last saw it");
    assert.deepEqual(await outline(), [...liveOutline, "final"], "during the hold the finished layout is exactly as the reader last saw it");
    // The height of the process, frame by frame from completion: it holds, then folds as a motion, then is gone.
    const frames = await page.evaluate(async () => {
      const samples = [];
      const start = performance.now();
      while (performance.now() - start < 1300) {
        const grid = document.querySelector(".turn-process-grid");
        samples.push({ at: performance.now() - start, height: grid ? grid.getBoundingClientRect().height : null,
          folding: Boolean(document.querySelector(".turn-process.is-settle-fold")) });
        await new Promise(requestAnimationFrame);
      }
      return samples;
    });
    const holding = frames.filter(frame => frame.at < 250);
    assert.ok(holding.length > 5 && holding.every(frame => frame.height !== null && Math.abs(frame.height - liveHeight) <= 1),
      `the hold holds the layout at ${liveHeight}px: ${JSON.stringify(holding.map(frame => Math.round(frame.height ?? -1)))}`);
    assert.ok(frames.some(frame => frame.height !== null && frame.height > liveHeight * .08 && frame.height < liveHeight * .92), "the fold is a motion, not a jump");
    // Relative to the fold phase, not to the clock: once the process is in its fold, it is already closing.
    const foldStart = frames.find(frame => frame.folding)?.at;
    assert.ok(foldStart !== undefined, "the process has a fold phase");
    const closing = frames.filter(frame => frame.folding && frame.at > foldStart + 150);
    assert.ok(closing.length > 3 && closing.every(frame => frame.height === null || frame.height < liveHeight * .7),
      `the fold phase is the fold: ${JSON.stringify(closing.map(frame => Math.round(frame.height ?? -1)))}`);
    assert.ok(frames.at(-1).height === null, "and the process is gone when it ends");
    await page.waitForFunction(() => !document.querySelector(".turn-process-grid"), null, { timeout: 5000 });
    assert.deepEqual(await outline(), ["user", "final"], "folded: the final report is all that is left of the run");
    const folded = await page.evaluate(() => ({ summary: document.querySelector(".turn-process-summary")?.textContent ?? "", text: document.body.textContent ?? "",
      beads: [...document.querySelectorAll(".turn-process-header .wv-beads i")].map(bead => bead.dataset.tone) }));
    assert.match(folded.summary, /读取 4 个文件/, "the summary line says what was done");
    assert.deepEqual(folded.beads, ["failed", "done", "failed", "done"], "and its beads tell how it went, step by step");
    for (const gone of ["先看一下环境", "引号被吞了", "编码问题"]) {
      assert.equal(folded.text.includes(gone), false, `"${gone}": the model's messages fold away with the rest of the process`);
    }
    // Opening the log shows everything in order, and closing it folds everything again. A finished turn's log
    // is detail the reader asked for, so no stage is collapsed there.
    await page.locator(".turn-process-header").click();
    await page.waitForFunction(() => document.querySelector(".turn-process.is-open") && document.querySelector(".wv-stage"));
    await page.waitForTimeout(400);
    assert.deepEqual(await outline(), [...liveOutline, "final"], "opened: every message and tool group, in the order they happened");
    assert.deepEqual((await stages()).map(stage => stage.rows), [1, 2, 1], "opened: every stage shows its steps");
    await page.locator(".turn-process-header").click();
    await page.waitForFunction(() => !document.querySelector(".turn-process-grid"), null, { timeout: 5000 });
    assert.deepEqual(await outline(), ["user", "final"], "closed again: only the final report");
    // History mounts folded and plays nothing.
    await page.evaluate(() => window.motionFixture.reset());
    await render([...story, answer], false);
    assert.deepEqual(await outline(), ["user", "final"], "a finished turn opens already folded");
    assert.equal(await page.locator(".turn-process-grid").count(), 0);
    // A stage of one step is that step's row. A stage that is over (a later one exists while the turn is live)
    // is one summary line, and only the latest stage stays open. A click on a line opens it, and it stays as
    // the reader left it.
    await page.evaluate(() => window.motionFixture.reset());
    const first = [user, message("intro", "先看一下。"), tool("a"), tool("b", { status: "failed" }), message("r", "b 打不开，换个路径。")];
    // While it is the latest stage it is open. When the next one begins it closes, and its rows stay for
    // the length of the closing motion instead of vanishing under the reader's eyes.
    await render(first.slice(0, 4));
    await page.waitForTimeout(300);
    assert.deepEqual((await stages()).map(stage => [stage.collapsed, stage.rows]), [[false, 2]]);
    await render([...first, tool("c", { status: "running" })]);
    assert.equal(await page.locator(".wv-stage").first().locator(".wv-step").count(), 2, "its rows are still there as it starts to close");
    await page.waitForFunction(() => !document.querySelectorAll(".wv-stage")[0].querySelector(".wv-step"), null, { timeout: 2000 });
    await page.waitForTimeout(400);
    assert.deepEqual(await stages(), [
      { collapsed: true, summary: "读取 2 个文件", failed: "失败 1", rows: 0 },
      { collapsed: false, summary: null, failed: null, rows: 1 },
    ], "the stage that is over is one line with its counts and its failure; a stage of one step is just its row");
    assert.equal(await page.locator(".wv-stage:not(.is-collapsible) .wv-line").count(), 0, "a one-step stage has no line of its own");
    // The line and the row are set on the same centre line, like two lines of one list.
    const centre = await page.evaluate(() => {
      const middle = element => { const rect = element.getBoundingClientRect(); return (rect.left + rect.right) / 2; };
      return { line: middle(document.querySelector(".wv-line-lead")), row: middle(document.querySelectorAll(".wv-stage")[1].querySelector(".wv-node")) };
    });
    assert.ok(Math.abs(centre.line - centre.row) <= 1.5, `a summary line and a lone row share one glyph column (${centre.line} vs ${centre.row})`);
    // The latest stage stays open while it has several steps, even between two of them.
    await render([...first, tool("c"), tool("d")]);
    await page.waitForTimeout(300);
    assert.deepEqual((await stages()).map(stage => [stage.collapsed, stage.rows]), [[true, 0], [false, 2]]);
    // Opening an old stage shows its rows, and it stays open as the run goes on.
    await page.locator(".wv-line").first().click();
    await page.waitForFunction(() => document.querySelectorAll(".wv-stage")[0].querySelectorAll(".wv-step").length === 2);
    await render([...first, tool("c", { status: "failed" }), tool("d"), message("r2", "c 也失败了。"), tool("e", { status: "running" })]);
    await page.waitForTimeout(400);
    assert.deepEqual((await stages()).map(stage => [stage.collapsed, stage.rows]), [[false, 2], [true, 0], [false, 1]], "the stage the reader opened is still open");
    await page.locator(".wv-line").first().click();
    await page.waitForFunction(() => !document.querySelectorAll(".wv-stage")[0].querySelector(".wv-step"));
    // The log of a finished turn is not summarised: opening it shows every stage as it happened.
    await render([...first, tool("c"), message("final", "完成。", { phase: "final_answer" })], false);
    await page.waitForFunction(() => !document.querySelector(".turn-process-grid") && document.querySelector(".turn-process-header"), null, { timeout: 5000 });
    await page.locator(".turn-process-header").click();
    await page.waitForFunction(() => document.querySelector(".turn-process.is-open") && document.querySelector(".wv-stage"));
    await page.waitForTimeout(400);
    assert.deepEqual((await stages()).map(stage => [stage.collapsed, stage.rows]), [[false, 2], [false, 1]], "history is open: no stage is collapsed for the reader");
    // A stage that did several kinds of work names the first three, in the order it did them.
    await page.evaluate(() => window.motionFixture.reset());
    await render([user, message("intro", "先看一下。"), tool("a"), tool("p", { toolName: "exec", arguments: { argv: ["echo"] } }),
      tool("q", { toolName: "browser_open", arguments: { url: "http://127.0.0.1/" } }),
      tool("s", { toolName: "write_workspace_text", arguments: { path: "a.txt", text: "x" }, status: "failed" }),
      message("r", "写入失败，改用别的办法。"), tool("z", { status: "running" })]);
    await page.waitForTimeout(400);
    assert.equal((await stages())[0].summary, "读取 1 个文件 · 运行 1 条命令 · 操作浏览器 1 次 …", "three kinds at most, in the order done");
    assert.deepEqual(errors, []);
    if (process.env.LOOM_EXECUTION_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_EXECUTION_SCREENSHOTS}/execution-${theme}.png` });
    await page.close();
  }
  console.log("Execution sequence: hidden narration, ordered switch, folded older steps, running step, body messages between tool groups, whole-process fold on completion, one-line stages, alignment and final boundary passed in both themes.");
} finally { await browser.close(); }
