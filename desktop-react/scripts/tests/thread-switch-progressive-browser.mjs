// Switching to a long conversation paints its newest turns first and mounts the rest while the page is idle, with the
// view where it was and the transcript the same in the end. The in-memory host of scripts/fixtures/perf-session.tsx
// serves several conversations of 25 turns each (a window is the newest 20).
import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const WINDOW = 20;

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript(() => localStorage.setItem("loom.sidebar.expandedProjects", JSON.stringify(["loom", "agent"])));
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/perf-session.html?turns=25&tools=4&heavy=4&motion=1&seed=7`);
  await page.locator(".turn-block").first().waitFor({ state: "attached" });
  await page.waitForFunction((count) => document.querySelectorAll(".turn-block").length === count, WINDOW, { timeout: 20000 });
  await page.waitForTimeout(400);

  // A recorder that sees every animation frame from the click on: how many blocks are mounted and where the newest one sits.
  await page.evaluate(() => {
    const state = window.__switch = { frames: [], running: false };
    const tick = () => {
      if (state.running) {
        const scroller = document.querySelector(".conversation-stage > .transcript-scroll");
        const blocks = [...document.querySelectorAll(".turn-block")];
        const last = blocks.at(-1);
        state.frames.push({
          at: performance.now(),
          blocks: blocks.length,
          first: blocks[0]?.dataset.turnId ?? "",
          gap: scroller && last ? Math.round((scroller.getBoundingClientRect().bottom - last.getBoundingClientRect().bottom) * 10) / 10 : null,
          switching: document.querySelector(".workspace")?.classList.contains("is-thread-switching") ?? false,
        });
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  const open = async (id) => {
    const row = page.locator(`.compact-thread-row[data-thread-id="perf-${id}"] .compact-thread-main`);
    await row.scrollIntoViewIfNeeded();
    await page.evaluate(() => { window.__switch.frames = []; window.__switch.running = true; });
    await row.click({ position: { x: 30, y: 12 } });
    await page.waitForFunction((prefix) => document.querySelector(".turn-block")?.dataset.turnId?.startsWith(prefix), `perf-${id}-turn-`, { timeout: 20000 });
    await page.waitForFunction((count) => document.querySelectorAll(".turn-block").length === count, WINDOW, { timeout: 20000 });
    await page.waitForTimeout(500);
    return page.evaluate(() => { window.__switch.running = false; return window.__switch.frames; });
  };

  const frames = await open(1);
  const mounted = frames.filter((frame) => frame.blocks > 0 && frame.first.startsWith("perf-1-turn-"));
  assert.ok(mounted.length > 3, "the new conversation was seen");
  const opening = mounted[0];
  assert.ok(opening.blocks < WINDOW / 2, `the first frame of a conversation holds its newest turns only: ${opening.blocks} blocks`);
  assert.ok(opening.blocks >= 2);
  // A reader who does nothing is left alone for a while: the pinning the page does to itself as it settles builds nothing early.
  const quiet = mounted.filter((frame) => frame.at - opening.at < 800);
  assert.ok(quiet.length > 5 && quiet.every((frame) => frame.blocks === opening.blocks), `the older turns wait for the page to be quiet: ${[...new Set(quiet.map((frame) => frame.blocks))]}`);
  // Blocks only ever join; none is replaced or removed while the older ones are added.
  for (let index = 1; index < mounted.length; index += 1) assert.ok(mounted[index].blocks >= mounted[index - 1].blocks, "mounted blocks never go away");
  assert.equal(mounted.at(-1).blocks, WINDOW, "every turn of the window ends up mounted");
  assert.ok(mounted.some((frame) => frame.blocks > opening.blocks && frame.blocks < WINDOW), "the older turns arrive over several frames, not all at once");

  // The view stays on the live edge while they do: the newest turn does not move relative to the bottom of the scroller.
  const gaps = mounted.map((frame) => frame.gap).filter((gap) => gap !== null);
  const spread = Math.max(...gaps) - Math.min(...gaps);
  assert.ok(spread <= 1.5, `the newest turn kept its place while older turns were added above it (moved ${spread}px)`);

  // The transcript is the one that was read: turn order, no duplicates, nothing missing.
  const order = await page.evaluate(() => [...document.querySelectorAll(".turn-block")].map((block) => block.dataset.turnId));
  assert.deepEqual(order, Array.from({ length: WINDOW }, (_, index) => `perf-1-turn-${index + 5}`));
  assert.equal(await page.locator(".conversation-stage > .transcript-scroll").evaluate((scroller) => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 3), true, "pinned to the live edge");

  // Scrolling the page does itself is not the reader's: nothing is built early for it.
  {
    const row = page.locator('.compact-thread-row[data-thread-id="perf-3"] .compact-thread-main');
    await row.scrollIntoViewIfNeeded();
    await row.click({ position: { x: 30, y: 12 } });
    await page.waitForFunction(() => document.querySelector(".turn-block")?.dataset.turnId?.startsWith("perf-3-turn-"));
    await page.waitForTimeout(80);
    const before = await page.locator(".turn-block").count();
    assert.ok(before < WINDOW);
    await page.evaluate(async () => {
      const scroller = document.querySelector(".conversation-stage > .transcript-scroll");
      for (let index = 0; index < 5; index += 1) {
        scroller.scrollTop = 400 + index; // within reach of the unmounted turns but not at the top, with no wheel, key or drag behind it: the view still follows the live edge
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
    });
    await page.waitForTimeout(400);
    assert.equal(await page.locator(".turn-block").count(), before, "scroll events the reader did not make build nothing early");
  }

  // A reader who scrolls up at once is given the older turns without waiting for idle time, and keeps their place.
  const early = await (async () => {
    const row = page.locator('.compact-thread-row[data-thread-id="perf-2"] .compact-thread-main');
    await row.scrollIntoViewIfNeeded();
    await row.click({ position: { x: 30, y: 12 } });
    await page.waitForFunction(() => document.querySelector(".turn-block")?.dataset.turnId?.startsWith("perf-2-turn-"));
    const box = await page.locator(".conversation-stage > .transcript-scroll").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    for (let step = 0; step < 8; step += 1) {
      await page.mouse.wheel(0, -700);
      await page.waitForTimeout(40);
    }
    await page.waitForFunction((count) => document.querySelectorAll(".turn-block").length === count, WINDOW, { timeout: 20000 });
    await page.waitForTimeout(300);
    return page.evaluate(() => ({
      order: [...document.querySelectorAll(".turn-block")].map((block) => block.dataset.turnId),
      top: document.querySelector(".conversation-stage > .transcript-scroll").scrollTop,
    }));
  })();
  assert.deepEqual(early.order, Array.from({ length: WINDOW }, (_, index) => `perf-2-turn-${index + 5}`));

  // Going back to a conversation that was open before opens it the same way, whatever state the first visit left.
  const again = await open(1);
  const second = again.filter((frame) => frame.blocks > 0 && frame.first.startsWith("perf-1-turn-"));
  assert.ok(second[0].blocks < WINDOW / 2 && second.at(-1).blocks === WINDOW);
  // This one was read before, so it is applied from memory; the click is still answered first: a frame shows the
  // selection and the dimmed surface while the conversation that is being left is still the one on screen.
  assert.ok(again.some((frame) => frame.switching && frame.first.startsWith("perf-2-turn-")),
    "the click was painted before the conversation from memory replaced the old one");

  // A conversation that is running is not rebuilt around the reader: building three turns is a task of 100 ms or more, a
  // stutter in the stream being watched. Its older turns wait for the run to end, and then for the page to be quiet.
  // The idle callbacks are held by the test (a slice follows the one before it within milliseconds, so what is "between
  // two slices" would otherwise depend on the speed of the machine).
  {
    await page.evaluate(() => {
      const queue = window.__idleQueue = [];
      window.requestIdleCallback = (callback) => queue.push(callback);
      window.cancelIdleCallback = (id) => { queue[id - 1] = null; };
      window.__flushIdle = () => {
        const next = queue.findIndex(Boolean);
        if (next < 0) return false;
        const callback = queue[next];
        queue[next] = null;
        callback({ didTimeout: false, timeRemaining: () => 50 });
        return true;
      };
    });
    const waiting = () => page.evaluate(() => window.__idleQueue.filter(Boolean).length);
    const blocks = () => page.locator(".turn-block").count();

    const row = page.locator('.compact-thread-row[data-thread-id="perf-0"] .compact-thread-main');
    await row.scrollIntoViewIfNeeded();
    await row.click({ position: { x: 30, y: 12 } });
    await page.waitForFunction(() => /^turn-\d+$/.test(document.querySelector(".turn-block")?.dataset.turnId ?? "")
      && !document.querySelector(".workspace")?.classList.contains("is-thread-switching"), null, { timeout: 20000 });
    const opened = await blocks();
    assert.ok(opened < WINDOW / 2, `the running conversation opens with its newest turns (${opened})`);
    assert.equal(await waiting(), 0, "nothing is asked of an idle moment straight away");
    await page.waitForTimeout(1400);
    assert.equal(await waiting(), 1, "after a quiet moment the first slice is asked for");
    assert.equal(await blocks(), opened, "and nothing is built before the page is idle");

    // The first slice happens; the next one is asked for at once.
    assert.equal(await page.evaluate(() => window.__flushIdle()), true);
    await page.waitForFunction((count) => document.querySelectorAll(".turn-block").length > count, opened, { timeout: 10000 });
    const sliced = await blocks();
    assert.ok(sliced > opened && sliced < WINDOW);
    await page.waitForFunction(() => window.__idleQueue.filter(Boolean).length === 1);

    // The turn starts: the slice that was asked for is withdrawn, and none is asked for while it runs.
    const started = Date.now();
    let finished = 0;
    const run = page.evaluate(() => window.perfFixture.startLive({ tools: 5, deltaMs: 20, toolMs: 80, finalChars: 600 })).then(() => { finished = Date.now(); });
    await page.waitForSelector(".turn-block.is-active", { timeout: 10000 });
    const counts = [];
    const asked = [];
    while (!finished) {
      counts.push(await blocks());
      asked.push(await waiting());
      await page.waitForTimeout(100);
    }
    await run;
    assert.ok(finished - started > 2000, `the run lasted as long as the quiet moment would have taken (${finished - started} ms)`);
    assert.ok(counts.length > 15 && Math.max(...counts) <= sliced + 1,
      `no older turn was built while the turn ran: ${[...new Set(counts)]} blocks (${sliced} before it, plus the live turn)`);
    assert.ok(asked.slice(1).every((count) => count === 0), `no slice was asked for while the turn ran: ${[...new Set(asked)]}`);

    // After the run the page is left alone for a while again, as it is after opening: nothing lands on top of its end.
    while (Date.now() - finished < 900) {
      assert.equal(await waiting(), 0, `no slice is asked for in the first moments after the run (${Date.now() - finished} ms)`);
      await page.waitForTimeout(100);
    }
    await page.waitForFunction(() => window.__idleQueue.filter(Boolean).length === 1, null, { timeout: 10000 });
    // Slice after slice (each asks for the next once React has committed it), until nothing is left to mount.
    for (let attempt = 0; attempt < 500; attempt += 1) {
      if (await page.evaluate(() => !document.querySelector(".transcript[data-older-pending]"))) break;
      await page.evaluate(() => window.__flushIdle());
      await page.waitForTimeout(30);
    }
    await page.waitForFunction(() => !document.querySelector(".transcript[data-older-pending]"), null, { timeout: 5000 });
    assert.equal(await blocks(), WINDOW + 1, "once the run is over every turn of the window is mounted, and the live one");
    assert.equal(await page.locator(".conversation-stage > .transcript-scroll").evaluate((scroller) => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 3), true, "and the view is still at the live edge");
  }

  // A short conversation is mounted whole at once; nothing is held back that would not fill the page twice.
  await page.goto(`${origin}/scripts/fixtures/perf-session.html?turns=3&tools=2&heavy=2&motion=1&seed=7`);
  await page.locator(".turn-block").first().waitFor({ state: "attached" });
  await page.waitForTimeout(600);
  assert.equal(await page.locator(".turn-block").count(), 3);

  assert.deepEqual(errors, []);
  console.log(`Progressive conversation opening passed: ${opening.blocks} of ${WINDOW} turns in the first frame, view moved ${spread}px while the rest arrived.`);
} finally { await browser.close(); }
