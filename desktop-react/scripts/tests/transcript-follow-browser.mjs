import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const bundledFixture = process.env.LOOM_TEST_BUNDLED_FIXTURE
  ? JSON.parse(readFileSync(process.env.LOOM_TEST_BUNDLED_FIXTURE, "utf8")) : null;
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const server = process.env.LOOM_TEST_START_SERVER
  ? await (await import("vite")).createServer({ server: { port: 0 } }) : null;
await server?.listen();
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
try {
  const errors = [];
  const origin = process.env.LOOM_TEST_ORIGIN || server?.resolvedUrls.local[0] || "http://127.0.0.1:5198/";
  const fixture = `${origin.replace(/\/$/, "")}/scripts/fixtures/long-conversation.html?motion=1&sections=1`;
  const open = async (viewport = { width: 1280, height: 800 }) => {
    const opened = await browser.newPage({ viewport });
    opened.on("pageerror", error => errors.push(error.message));
    if (bundledFixture) {
      await opened.route("**/*", route => route.request().isNavigationRequest()
        ? route.fulfill({ contentType: "text/html", body: `<html><head><style>${bundledFixture.css}</style></head><body><div id="root"></div></body></html>` })
        : route.abort());
    }
    await opened.goto(fixture, { waitUntil: "domcontentloaded" });
    if (bundledFixture) await opened.addScriptTag({ content: bundledFixture.js });
    await opened.waitForFunction(() => {
      const el = document.querySelector(".transcript-scroll");
      return el && el.scrollHeight - el.scrollTop - el.clientHeight < 3;
    });
    return opened;
  };
  const stateOf = target => target.evaluate(() => {
    const el = document.querySelector(".transcript-scroll");
    return {
      bottom: el.scrollHeight - el.scrollTop - el.clientHeight,
      top: el.scrollTop,
      following: el.dataset.following,
      jump: document.querySelector(".transcript-jump-latest").classList.contains("is-visible"),
    };
  });

  // The view glides to the live edge rather than jumping, so "at the edge" is something to wait for, within a bound.
  const atEdge = async (target, within = 1500) => {
    // Held for a run of frames: a page still growing (a step unfolding) crosses the edge and leaves it again.
    await target.waitForFunction(() => {
      const el = document.querySelector(".transcript-scroll");
      const near = el.scrollHeight - el.scrollTop - el.clientHeight < 3;
      window.__edgeRun = near ? (window.__edgeRun ?? 0) + 1 : 0;
      return window.__edgeRun >= 10;
    }, null, { timeout: within, polling: "raf" });
    return stateOf(target);
  };

  const page = await open();
  const scroller = page.locator(".transcript-scroll");
  const bottom = () => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
  // Navigation in a sibling pane must not change transcript follow intent.
  await page.evaluate(() => {
    const sibling = document.createElement("button");
    document.body.append(sibling);
    sibling.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    sibling.remove();
  });
  assert.equal(await scroller.getAttribute("data-following"), "true");
  await page.evaluate(() => window.longConversation.start());
  await page.waitForTimeout(500);
  // A large asynchronous layout change must reach the live edge promptly even
  // while the live answer is running (images/tool output can grow this way).
  // The view glides there rather than jumping, so "promptly" is a time: a 700px
  // arrival is home within a quarter of a second, and never overshoots.
  const arrival = await page.evaluate(async () => {
    const scroller = document.querySelector(".transcript-scroll");
    const probe = document.createElement("div");
    probe.id = "follow-probe";
    probe.style.height = "700px";
    document.querySelector(".transcript").append(probe);
    const started = performance.now();
    let overshoot = 0;
    while (performance.now() - started < 600) {
      await new Promise(requestAnimationFrame);
      const gap = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      overshoot = Math.max(overshoot, -gap);
      if (gap < 3) return { ms: performance.now() - started, overshoot };
    }
    return { ms: Infinity, overshoot };
  });
  assert.ok(arrival.ms < 250, `live output reached the edge in ${arrival.ms}ms`);
  assert.ok(arrival.overshoot < 1.5, `and did not overshoot it (${arrival.overshoot}px)`);
  assert.ok(await bottom() < 3, `live output lagged ${await bottom()}px`);
  // A layout in the middle of a DOM commit sees a briefly shorter page and clamps the view to it (React removes a node,
  // forcing layout, then inserts its replacement). The page is tall enough again when the frame is built, so the view
  // must not be left where the clamp put it.
  await page.waitForTimeout(700); // let the glide and the stream's own growth settle: this is about one commit
  const clamp = await page.evaluate(async () => {
    const scroller = document.querySelector(".transcript-scroll");
    const content = document.querySelector(".transcript");
    const before = scroller.scrollTop;
    document.querySelector("#follow-probe").remove();
    void scroller.scrollHeight; // layout against the shorter page: the browser clamps scrollTop
    const clamped = scroller.scrollTop;
    const replacement = document.createElement("div");
    replacement.id = "follow-probe";
    replacement.style.height = "760px"; // 60px taller than what it replaces
    content.append(replacement);
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    return { before, clamped, after: scroller.scrollTop };
  });
  assert.ok(clamp.clamped < clamp.before - 100, `the clamp happened (${clamp.before} -> ${clamp.clamped}), so this test means something`);
  assert.ok(clamp.after > clamp.before - 3, `the view was put back by the time it was drawn (${clamp.before} -> clamp ${clamp.clamped} -> ${clamp.after})`);
  await page.waitForTimeout(600);
  assert.ok(await bottom() < 3, "and it still ends on the live edge");
  // Zoom changes the browser's attainable fractional scroll offsets. After
  // follow settles, stationary content must not keep bouncing between writes.
  for (const zoom of [0.8, 1.25, 1.5]) {
    await page.evaluate(zoom => { document.querySelector('.conversation-stage').style.zoom = String(zoom); }, zoom);
    await atEdge(page, 2500);
    const positions = await page.evaluate(async () => {
      const scroller = document.querySelector('.transcript-scroll');
      const samples = [];
      for (let frame = 0; frame < 40; frame++) {
        await new Promise(requestAnimationFrame);
        samples.push(scroller.scrollTop);
      }
      return samples;
    });
    assert.ok(Math.max(...positions) - Math.min(...positions) < 1.5, `stationary zoom ${zoom} oscillated ${Math.max(...positions) - Math.min(...positions)}px`);
  }
  await page.evaluate(() => { document.querySelector('.conversation-stage').style.zoom = ''; });
  await atEdge(page, 2500);
  // The actual overlay scrollbar uses these ownership events because its
  // pointer target is outside the transcript subtree.
  await scroller.evaluate(el => {
    el.dispatchEvent(new CustomEvent('loom:scrollbar-drag-start', { bubbles: true }));
    el.scrollTop -= 40;
    el.dispatchEvent(new CustomEvent('loom:scrollbar-drag-end', { bubbles: true }));
  });
  assert.equal(await scroller.getAttribute('data-following'), 'false', 'a short upward scrollbar drag remains detached even near the edge');
  await scroller.evaluate(el => {
    el.dispatchEvent(new CustomEvent('loom:scrollbar-drag-start', { bubbles: true }));
    el.scrollTop -= 250;
  });
  const dragTop = await scroller.evaluate(el => el.scrollTop);
  await page.evaluate(() => { document.querySelector('#follow-probe').style.height = '1000px'; });
  await page.waitForTimeout(250);
  assert.equal(await scroller.getAttribute('data-following'), 'false');
  assert.ok(Math.abs(await scroller.evaluate(el => el.scrollTop) - dragTop) < 3, 'automatic follow must not fight scrollbar ownership');
  await scroller.evaluate(el => {
    el.scrollTop = el.scrollHeight;
    el.dispatchEvent(new CustomEvent('loom:scrollbar-drag-end', { bubbles: true }));
  });
  await atEdge(page, 2500);
  await page.evaluate(() => { document.querySelector('#follow-probe').style.height = '760px'; });
  await atEdge(page, 2500);
  // Upward intent pauses follow even within the near-bottom threshold, and
  // always exposes a way to resume instead of silently hiding the control.
  await scroller.evaluate(el => {
    el.dispatchEvent(new WheelEvent("wheel", { deltaY: -40, bubbles: true }));
    el.scrollTop -= 40;
  });
  await page.waitForTimeout(50);
  assert.equal(await scroller.getAttribute("data-following"), "false");
  assert.ok(await page.locator(".transcript-jump-latest").evaluate(el => el.classList.contains("is-visible")));
  const detachedTop = await scroller.evaluate(el => el.scrollTop);
  await page.evaluate(() => document.querySelector("#follow-probe").style.height = "900px");
  await page.waitForTimeout(100);
  assert.ok(Math.abs(await scroller.evaluate(el => el.scrollTop) - detachedTop) < 3);
  // At the physical bottom a downward gesture must resume even if it cannot
  // produce another scroll event.
  await scroller.evaluate(el => {
    el.scrollTop = el.scrollHeight - el.clientHeight - 60;
    el.dispatchEvent(new WheelEvent('wheel', {deltaY: 10, bubbles: true}));
  });
  const manualTop = await scroller.evaluate(el => el.scrollTop);
  await page.waitForTimeout(300);
  assert.equal(await scroller.getAttribute('data-following'), 'false', 'approaching the edge must not start a second slow scroll');
  assert.ok(Math.abs(await scroller.evaluate(el => el.scrollTop) - manualTop) < 1.5);
  await scroller.evaluate(el => el.scrollTop = el.scrollHeight);
  await page.waitForTimeout(50);
  await scroller.evaluate(el => el.dispatchEvent(new WheelEvent("wheel", { deltaY: 40, bubbles: true })));
  assert.equal(await scroller.getAttribute("data-following"), "true");
  await page.waitForTimeout(50);
  assert.ok(await bottom() < 2, 'manual arrival adopts the edge immediately');
  await page.evaluate(() => window.longConversation.finish());
  await atEdge(page, 2500);
  assert.ok(await bottom() < 3);
  await page.close();

  // Opening a step of the live run is not leaving the live edge: the run keeps pushing the view down.
  {
    const live = await open();
    await live.evaluate(() => { window.longConversation.start(); window.longConversation.step(1); window.longConversation.step(2); });
    await live.locator(".turn-block.is-active .wv-row.is-expandable").last().waitFor();
    await atEdge(live);
    await live.locator(".turn-block.is-active .wv-row.is-expandable").last().click();
    await live.locator('.turn-block.is-active .wv-detail[data-open="true"]').waitFor();
    let state = await atEdge(live);
    assert.equal(state.following, "true", "opening a step of the live run must not end follow");
    assert.equal(state.jump, false);
    assert.ok(state.bottom < 3, `the opened step pushed the live edge out of view by ${state.bottom}px`);
    await live.evaluate(() => window.longConversation.step(3));
    state = await atEdge(live);
    assert.equal(state.following, "true");
    assert.ok(state.bottom < 3, `the run kept growing after the click and the view lagged ${state.bottom}px`);
    await live.close();
  }

  // ...unless following would carry the step the reader just opened out of view: then they are reading it.
  {
    const live = await open({ width: 1280, height: 420 });
    await live.addStyleTag({ content: ".wv-sheet-output { max-height: 320px !important; }" });
    await live.evaluate(() => { window.longConversation.start(); window.longConversation.step(1, 30); window.longConversation.step(2, 30); });
    await live.locator(".turn-block.is-active .wv-row.is-expandable").last().waitFor();
    await live.waitForTimeout(600);
    const first = live.locator(".turn-block.is-active .wv-row.is-expandable").first();
    await first.click();
    await live.locator('.turn-block.is-active .wv-detail[data-open="true"]').first().waitFor();
    await live.waitForTimeout(700);
    const state = await stateOf(live);
    assert.equal(state.following, "false", "the reader stays on the step that would have been pushed out of view");
    assert.equal(state.jump, true, "and keeps a way back");
    const headerTop = await first.evaluate(el => el.getBoundingClientRect().top - document.querySelector(".transcript-scroll").getBoundingClientRect().top);
    assert.ok(headerTop >= 0 && headerTop < 120, `the opened step's header is in view (${headerTop}px from the top)`);
    await live.evaluate(() => window.longConversation.step(3));
    await live.waitForTimeout(500);
    assert.ok(Math.abs((await stateOf(live)).top - state.top) < 3, "the run growing underneath does not carry the reader away");
    await live.locator(".transcript-jump-latest").click();
    await live.waitForFunction(() => document.querySelector(".transcript-scroll").dataset.following === "true");
    await atEdge(live, 2500);
    assert.ok((await stateOf(live)).bottom < 3);
    await live.close();
  }

  // Opening something that is not the live run (a finished turn, a decision) is reading history in place.
  {
    const idle = await open();
    await idle.evaluate(() => {
      const entry = document.createElement("div");
      entry.innerHTML = '<button id="history-probe" aria-expanded="false">Details</button><div class="probe-detail" style="height:0;overflow:hidden"></div>';
      document.querySelector(".transcript").append(entry);
      const button = entry.querySelector("button");
      const detail = entry.querySelector(".probe-detail");
      button.onclick = () => {
        const open = button.getAttribute("aria-expanded") !== "true";
        button.setAttribute("aria-expanded", String(open));
        detail.style.height = open ? "500px" : "0";
      };
      const scroller = document.querySelector(".transcript-scroll");
      const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, "scrollTop");
      window.scrollWrites = 0;
      Object.defineProperty(scroller, "scrollTop", { get() { return descriptor.get.call(this); }, set(value) { window.scrollWrites++; descriptor.set.call(this, value); } });
    });
    await idle.waitForTimeout(500);
    await idle.evaluate(() => { window.scrollWrites = 0; });
    await idle.locator("#history-probe").click();
    await idle.waitForTimeout(450);
    assert.equal((await stateOf(idle)).following, "false");
    for (let i = 0; i < 6; i++) { await idle.locator("#history-probe").press("Enter"); await idle.waitForTimeout(40); }
    await idle.waitForTimeout(450);
    assert.equal(await idle.evaluate(() => window.scrollWrites), 0, "a fold the reader is operating is never chased");
    await idle.locator(".transcript-jump-latest").click();
    await idle.waitForFunction(() => document.querySelector(".transcript-scroll").dataset.following === "true");
    await idle.close();
  }

  // A navigation key with nothing focused still scrolls the pane the reader last clicked: the follow loop must
  // not fight it.
  {
    const keyed = await open();
    await keyed.evaluate(() => window.longConversation.start());
    await keyed.waitForTimeout(500);
    const box = await keyed.locator(".transcript-scroll").boundingBox();
    await keyed.mouse.click(box.x + 6, box.y + box.height / 2);
    assert.ok(await keyed.evaluate(() => document.activeElement === document.body), "the click left nothing focused");
    const start = (await stateOf(keyed)).top;
    await keyed.keyboard.press("PageUp");
    await keyed.waitForFunction(() => document.querySelector(".transcript-scroll").dataset.following === "false");
    await keyed.waitForTimeout(500); // the key's own scroll animation
    const read = await stateOf(keyed);
    assert.ok(read.top < start - 100 && read.jump, "the key scrolled the transcript and the way back is offered");
    await keyed.evaluate(() => {
      const probe = document.createElement("div");
      probe.style.height = "600px";
      document.querySelector(".transcript").append(probe);
    });
    await keyed.waitForTimeout(300);
    assert.ok(Math.abs((await stateOf(keyed)).top - read.top) < 3, "growth is not chased back over the reader's keyboard scroll");
    await keyed.keyboard.press("End");
    await keyed.waitForFunction(() => document.querySelector(".transcript-scroll").dataset.following === "true");
    await keyed.waitForTimeout(500);
    assert.ok((await stateOf(keyed)).bottom < 3);
    await keyed.close();
  }

  // A gesture that cannot move the transcript does not leave the live edge.
  {
    const short = await open();
    await short.evaluate(() => window.longConversation.small());
    await short.waitForTimeout(400);
    assert.ok(await short.evaluate(() => { const el = document.querySelector(".transcript-scroll"); return el.scrollHeight <= el.clientHeight + 1; }));
    const box = await short.locator(".transcript-scroll").boundingBox();
    await short.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await short.mouse.wheel(0, -200);
    await short.waitForTimeout(250);
    const state = await stateOf(short);
    assert.equal(state.following, "true", "pulling at a transcript with nothing to scroll must not stop the follow");
    assert.equal(state.jump, false);
    await short.close();

    // A command's output pane scrolls on its own: the transcript only moves when the pane has nothing left.
    const nested = await open();
    await nested.evaluate(() => window.longConversation.start());
    await nested.waitForTimeout(500);
    await nested.evaluate(() => {
      const pane = document.createElement("div");
      pane.id = "output-pane";
      pane.style.cssText = "height:90px;overflow:auto";
      pane.innerHTML = '<div style="height:500px">output</div>';
      document.querySelector(".transcript").append(pane);
    });
    await nested.waitForTimeout(300);
    await nested.evaluate(() => { document.querySelector("#output-pane").scrollTop = 200; });
    await nested.evaluate(() => { document.querySelector("#output-pane").dispatchEvent(new CustomEvent('loom:scrollbar-drag-start', { bubbles: true })); });
    assert.equal((await stateOf(nested)).following, 'true', 'a nested output scrollbar does not take transcript ownership');
    const pane = await nested.locator("#output-pane").boundingBox();
    await nested.mouse.move(pane.x + 40, pane.y + 40);
    await nested.mouse.wheel(0, -60);
    await nested.waitForTimeout(300);
    assert.ok(await nested.evaluate(() => document.querySelector("#output-pane").scrollTop < 200), "the pane took the gesture");
    assert.equal((await stateOf(nested)).following, "true", "scrolling inside an output pane is not leaving the live edge");
    await nested.evaluate(() => { document.querySelector("#output-pane").scrollTop = 0; });
    await nested.waitForTimeout(700);
    await nested.mouse.wheel(0, -200);
    await nested.waitForFunction(() => document.querySelector(".transcript-scroll").dataset.following === "false", null, { timeout: 3000 });
    await nested.close();
  }
  assert.deepEqual(errors, []);
  console.log("PASS: live layout sync, scoped keyboard intent, visible resume control, detached reading and bottom return, live steps opened without losing the edge, keyboard and nested-pane scrolling");
} finally {
  await browser.close();
  await server?.close();
}
