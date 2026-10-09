import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const base = { threadId: "thread-1", turnId: "turn-1", status: "completed" };
const message = (id, text = "记录仍然可读。".repeat(20)) => ({ ...base, id, type: "assistant_message", phase: "commentary", text });
const user = { ...base, id: "user", type: "user_message", text: "检查任务" };
const slot = id => `.process-handoff-slot[data-process-items~="${id}"]`;
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const theme of ["light", "dark"]) {
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    const render = items => page.evaluate(items => window.motionFixture.turn(items, true), items);
    await render([user, message("first")]);
    await page.waitForTimeout(500);
    await page.evaluate(() => window.firstNode = document.querySelector('[data-message-id="first"]'));
    await render([user, message("first"), message("second")]);
    await page.waitForTimeout(700);
    // Per-step narration is hidden by default; these rules are about prose that is drawn.
    await page.locator(".process-notes-toggle").click();
    await page.waitForTimeout(300);
    assert.equal(await page.locator(slot("first")).getAttribute("data-handoff-phase"), "holding", "visible prose must not fold when commentary arrives");
    assert.equal(await page.locator(".earlier-process-toggle").count(), 0, "no empty history capsule while all records are retained");
    assert.ok(await page.evaluate(() => window.firstNode === document.querySelector('[data-message-id="first"]')));
    // Explicit upward input detaches before a long message can push records out.
    await page.locator(".transcript-scroll").dispatchEvent("wheel", { deltaY: -200 });
    await page.waitForFunction(() => document.querySelector(".transcript-scroll").dataset.following === "false");
    const long = message("long", Array.from({length: 45}, (_, i) => `段落 ${i}：保留完整工作记录。`).join("\n\n"));
    await render([user, message("first"), message("second"), long]);
    await page.waitForTimeout(900);
    assert.equal(await page.locator(slot("first")).count(), 1, "detached reading retains earlier records");
    // The newest narration is a quiet note, so it takes little room until opened. Open it so it is
    // the tall live record this geometry needs; the jump below follows the live edge again.
    await page.locator('[data-message-id="long"] .note-toggle').click();
    // Return through the actual controller; only fully offscreen slots retire.
    await page.locator(".transcript-jump-latest").evaluate(el => el.click());
    await page.waitForFunction(() => !document.querySelector('.process-handoff-slot[data-process-items~="first"]'));
    await page.waitForFunction(() => !document.querySelector('.process-handoff-slot[data-process-items~="second"]'));
    assert.equal(await page.locator('[data-message-id="long"]').evaluate(el => el.classList.contains("is-note")), true,
      "the newest narration is a note from its first frame, not a full message that later shrinks");
    await page.locator(".earlier-process-toggle").click();
    await page.locator('.earlier-process-history[data-motion-phase="entered"]').waitFor();
    assert.equal(await page.locator('.earlier-process-history [data-message-id="first"]').count(), 1);
    assert.deepEqual(await page.evaluate(() => ["first", "second"].map(id =>
      document.querySelector(`.earlier-process-history [data-message-id="${id}"]`)?.classList.contains("is-note"))), [false, true],
      "the reply that answers the user stays a message; later narration is a note");
    await page.locator(".earlier-process-toggle").click();
    await page.waitForTimeout(350);
    assert.equal(await page.locator('.earlier-process-history [data-message-id="first"]').count(), 0);
    // Reduced motion changes timing, never viewport eligibility.
    await page.evaluate(() => document.documentElement.dataset.loomReducedMotion = "true");
    await render([user, message("first"), message("second"), long, message("newest")]);
    await page.waitForTimeout(500);
    assert.equal(await page.locator(slot("long")).count(), 1, "partly visible records remain even with reduced motion");
    assert.equal(await page.locator(slot("newest")).count(), 1);
    if (process.env.LOOM_EARLIER_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_EARLIER_SCREENSHOTS}/earlier-${theme}.png` });
    await page.evaluate(() => { window.motionFixture.reset(); document.documentElement.dataset.loomReducedMotion = "false"; });
    const tool = (id, status = "completed") => ({ ...base, id, type: "tool_call", status,
      toolName: "exec", arguments: { cmd: `echo ${id}` } });
    const batch = [user, message("intro"), tool("t1"), tool("t2", "running")];
    await render(batch);
    await render([...batch, long]);
    await page.locator(".process-notes-toggle").click();
    await page.waitForTimeout(300);
    // A long note is clamped by design. Open it so it takes the height this handoff geometry needs,
    // then follow the live edge again: the click itself detaches the reader, which is not under test here.
    await page.locator('[data-message-id="long"] .note-toggle').click();
    await page.locator(".transcript-jump-latest").evaluate(el => el.click());
    await page.waitForTimeout(850);
    assert.equal(await page.locator(slot("t1")).count(), 1, "a mixed tool envelope keeps completed neighbours of live rows");
    assert.notEqual(await page.locator(slot("t1")).getAttribute("data-handoff-phase"), "folding");
    await render([...batch.map(item => item.id === "t2" ? tool("t2") : item), long]);
    await page.waitForFunction(() => document.querySelector('.process-handoff-slot[data-process-items~="t2"]')?.dataset.handoffPhase === "folding");
    await page.locator(".transcript-scroll").dispatchEvent("wheel", { deltaY: -100 });
    await page.waitForTimeout(500);
    assert.equal(await page.locator(slot("t2")).getAttribute("data-handoff-phase"), "holding", "scrolling up reverses a pending retirement");
    await page.locator(".transcript-jump-latest").evaluate(el => el.click());
    await page.waitForFunction(() => !document.querySelector('.process-handoff-slot[data-process-items~="t2"]'));
    await page.evaluate(() => window.motionFixture.reset());
    // The reply to a mid-run user message keeps the message presentation after it folds into
    // earlier steps, where it now follows tool work and would otherwise shrink into a note.
    // Steering is placed by submission time, so every record carries one, like a real run.
    const at = second => ({ createdAt: new Date(Date.UTC(2026, 9, 9, 7, 0, second)).toISOString() });
    const steer = { ...base, id: "steer", type: "user_message", source: "steering", text: "补测 current-browser", submittedAt: at(3).createdAt };
    await render([user, { ...message("intro"), ...at(1) }, { ...tool("s1"), ...at(2) }, steer,
      { ...message("reply"), ...at(4) }, { ...tool("s2"), ...at(5) }, { ...long, ...at(6) }]);
    await page.waitForTimeout(700);
    await page.locator(".process-notes-toggle").click();
    await page.waitForTimeout(300);
    await page.locator('[data-message-id="long"] .note-toggle').click();
    await page.waitForTimeout(400);
    await page.locator(".transcript-jump-latest").evaluate(el => el.click());
    await page.waitForFunction(() => !document.querySelector('.process-handoff-slot[data-process-items~="s2"]'));
    await page.locator(".earlier-process-toggle").click();
    await page.locator('.earlier-process-history[data-motion-phase="entered"]').waitFor();
    assert.deepEqual(await page.evaluate(() => ["intro", "reply"].map(id => {
      const el = document.querySelector(`.earlier-process-history [data-message-id="${id}"]`);
      return el ? { note: el.classList.contains("is-note"), size: getComputedStyle(el.querySelector(".assistant-message")).fontSize } : null;
    })), [{ note: false, size: "15px" }, { note: false, size: "15px" }], "replies to the user keep message size after folding");
    await page.locator(".earlier-process-toggle").click();
    await page.waitForTimeout(350);
    await page.evaluate(() => window.motionFixture.reset());
  }
  assert.deepEqual(errors, []);
  console.log("Earlier process: visible retention, detached reading, offscreen follow handoff, expansion and reduced motion passed in both themes.");
} finally { await browser.close(); }
