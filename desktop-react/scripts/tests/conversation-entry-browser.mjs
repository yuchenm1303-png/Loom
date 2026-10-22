import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const base = { threadId: "restored", turnId: "turn-1", status: "completed" };
const tool = (id, status = "completed") => ({ ...base, id, type: "tool_call", status,
  toolName: "exec", arguments: { cmd: `echo ${id}` }, error: status === "failed" ? "Existing failure output" : undefined });
const history = Array.from({ length: 24 }, (_, i) => [
  { ...base, id: `user-${i}`, turnId: `old-${i}`, type: "user_message", text: `Question ${i}` },
  { ...base, id: `answer-${i}`, turnId: `old-${i}`, type: "assistant_message", phase: "final_answer", text: "Existing answer. ".repeat(50) },
]).flat();
const items = [...history,
  { ...base, id: "user", type: "user_message", text: "Continue the investigation" },
  { ...base, id: "intro", type: "assistant_message", phase: "commentary", text: "Checking the existing records." },
  ...Array.from({ length: 18 }, (_, i) => tool(`tool-${i}`)),
  { ...base, id: "next", type: "assistant_message", phase: "commentary", text: "Continuing the investigation." },
  ...Array.from({ length: 10 }, (_, i) => tool(`failed-${i}`, "failed")),
  tool("running", "running"),
];
try {
  for (const width of [1100, 540]) {
    const page = await browser.newPage({ viewport: { width, height: 800 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=dark`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    const frames = await page.evaluate(async items => {
      window.motionFixture.turn(items, true, "restored");
      const result = [];
      for (let i = 0; i < 65; i++) {
        await new Promise(requestAnimationFrame);
        // ResizeObserver pins layout changes after rAF, before this frame is painted.
        await new Promise(resolve => setTimeout(resolve, 0));
        const el = document.querySelector(".transcript-scroll");
        result.push({ gap: el.scrollHeight - el.clientHeight - el.scrollTop, height: el.scrollHeight,
          born: document.querySelectorAll('[data-born="live"], .is-sending').length,
          folding: document.querySelectorAll('[data-handoff-phase="folding"]').length,
          subOpen: [...document.querySelectorAll('.wv-sub-fold')].every(el => el.dataset.open === "true") });
      }
      return result;
    }, items);
    assert.ok(frames.every(frame => frame.gap <= 2), `entry must stay at latest: ${JSON.stringify(frames.slice(0, 8))}`);
    // The older turns of a window are mounted while the page is idle, so the page grows a little at a time; what it must
    // never do is shrink (a fold or an unfold of history would), and the newest turn keeps its place (the gap above).
    assert.ok(frames.every((frame, index) => !index || frame.height >= frames[index - 1].height), `restored history must not unfold then shrink: ${JSON.stringify(frames.slice(0, 20))}`);
    await page.waitForFunction(() => !document.querySelector(".transcript[data-older-pending]"));
    assert.ok(frames.every(frame => frame.born === 0 && frame.folding === 0 && frame.subOpen), "history must start settled");
    assert.ok(await page.locator('.wv-sub.is-failure').count() > 0, "restored failure output is exercised");
    if (process.env.LOOM_ENTRY_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_ENTRY_SCREENSHOTS}/entry-${width}.png` });
    // Reconciliation and late image/layout measurements are not new activity.
    await page.evaluate(items => window.motionFixture.turn([...items], true, "restored"), items);
    await page.evaluate(() => {
      const late = document.createElement("div");
      late.id = "late-layout";
      late.style.height = "700px";
      late.style.flexShrink = "0";
      document.querySelector(".transcript").append(late);
    });
    await page.waitForTimeout(60);
    const lateLayout = await page.locator(".transcript-scroll").evaluate(el => ({ gap: el.scrollHeight - el.clientHeight - el.scrollTop, following: el.dataset.following }));
    assert.ok(lateLayout.gap <= 2, `late layout remains pinned: ${JSON.stringify(lateLayout)}`);
    await page.evaluate(items => window.motionFixture.turn([...items, { ...items.at(-1), id: "fresh" }], true, "restored"), items);
    assert.equal(await page.locator('[data-born="live"]').count(), 1, "new live records retain their entrance");
    await page.locator(".transcript-scroll").dispatchEvent("wheel", { deltaY: -400 });
    await page.waitForFunction(() => document.querySelector(".transcript-scroll").dataset.following === "false");
    await page.evaluate(() => { document.querySelector("#late-layout").style.height = "1200px"; });
    await page.waitForTimeout(100);
    assert.equal(await page.locator(".transcript-scroll").getAttribute("data-following"), "false", "reader intent must remain respected");
    await page.evaluate(items => window.motionFixture.turn(items, true, "another-thread"), items);
    await page.waitForTimeout(80);
    assert.ok(await page.locator(".transcript-scroll").evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop <= 2));
    await page.evaluate(() => window.motionFixture.reset());
    const text = "Existing streaming answer. ".repeat(80);
    await page.evaluate(({ items, text }) => window.motionFixture.turn([...items, {
      threadId: "streaming-history", turnId: "turn-1", id: "streaming-answer", type: "assistant_message",
      phase: "final_answer", status: "streaming", text,
    }], true, "streaming-history"), { items, text });
    assert.ok((await page.locator('[data-message-id="streaming-answer"]').textContent()).includes(text.trim()), "existing streaming text is visible without typing replay");
    await page.evaluate(() => window.motionFixture.reset());
    await page.evaluate(() => window.motionFixture.turn([], true, "delayed-history"));
    await page.evaluate(items => window.motionFixture.turn(items, true, "delayed-history"), items);
    assert.ok(await page.locator(".transcript-scroll").evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop <= 2), "delayed history is also positioned before paint");
    assert.equal(await page.locator('[data-born="live"], .is-sending').count(), 0);
    await page.evaluate(() => window.motionFixture.reset());
    await page.evaluate(items => window.motionFixture.turn([{ ...items.find(item => item.id === "user"), id: "fresh-user", status: "sending" }], true, "fresh-thread"), items);
    assert.equal(await page.locator('.entry-user_message.is-sending').count(), 1, "a real fresh send retains its entrance");
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Conversation entry: settled first frame, stable bottom, new live updates and reader intent passed.");
} finally {
  await browser.close();
}
