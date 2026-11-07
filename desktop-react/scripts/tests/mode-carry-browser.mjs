// The 对话/建造 switch does not wait: the shell leaves whatever the Host is doing, the views swap only once it has arrived,
// nothing shows through the swap, and the switch's thumb travels.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const BUILD = ["visual-1", "visual-2", "visual-3"];

// Every painted frame from the click until the switch is over: rAF, then a task, so what the frame showed has been applied.
const trace = (index) => page => page.evaluate(async (index) => {
  const box = (node) => { if (!node) return null; const r = node.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
  const shows = (node) => { if (!node) return null; let o = 1; for (let n = node; n; n = n.parentElement) o *= Number(getComputedStyle(n).opacity); return o; };
  const frames = [];
  const t0 = performance.now();
  document.querySelectorAll(".thread-header-actions .pf-toggle [role=radio]")[index].click();
  await new Promise((resolve) => {
    let quiet = 0;
    const tick = () => setTimeout(() => {
      const shell = document.querySelector(".mode-shell");
      const morph = document.querySelector("[data-morph], [data-mode-morph]");
      const hub = document.querySelector(".project-mode-stage .pf-composer");
      const composer = document.querySelector(".composer-stage .composer");
      const scroller = document.querySelector(".transcript-scroll");
      frames.push({ t: performance.now() - t0, phase: morph ? morph.dataset.morph || morph.dataset.modeMorph : null, shell: box(shell),
        hubMounted: Boolean(document.querySelector(".project-mode-stage")), chatMounted: Boolean(document.querySelector(".conversation-stage")),
        hubShown: hub && getComputedStyle(hub).visibility === "visible" ? box(hub) : null,
        composerShown: composer && getComputedStyle(composer).visibility === "visible" ? box(composer) : null,
        transcript: shows(scroller), scroll: scroller?.scrollTop,
        thumb: new DOMMatrix(getComputedStyle(document.querySelector(".pf-toggle-thumb")).transform).m41 });
      quiet = !morph && !shell && performance.now() - t0 > 100 ? quiet + 1 : 0;
      if (quiet > 10 || performance.now() - t0 > 6000) resolve(); else requestAnimationFrame(tick);
    }, 0);
    requestAnimationFrame(tick);
  });
  return frames;
}, index);

async function open(page) {
  await page.addInitScript((ids) => localStorage.setItem("loom.projectMode", JSON.stringify(ids)), BUILD);
  await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=dark&motion=1&performance=1`);
  await page.locator(".thread-header-actions .pf-toggle").waitFor();
  await page.locator(".compact-thread-row").first().waitFor();
  await page.waitForTimeout(400);
  // What the real Host takes (a read of a large conversation; git behind an overview), and more.
  await page.evaluate(() => {
    const base = window.loom;
    window.hostFixture = { slow: { "projectRun/overview": 700, "thread/read": 500 }, failRead: false };
    window.loom = new Proxy(base, { get: (target, key) => key !== "call" ? target[key] : async (method, args) => {
      if (method === "thread/read" && window.hostFixture.failRead) throw new Error("Fixture could not read");
      const result = await target.call(method, args);
      const wait = window.hostFixture.slow[method];
      if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
      return result;
    } });
  });
}

const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const shellMoves = (frames) => {
  const flying = frames.filter((frame) => frame.shell);
  assert.ok(flying.length, "a shell carries the box");
  return flying.find((frame) => frame.shell.join() !== flying[0].shell.join());
};
try {
  await open(page);
  for (const [index, name] of [[1, "to build"], [0, "to chat"], [1, "to build again"], [0, "to chat again"]]) {
    const frames = await trace(index)(page);
    const flying = frames.filter((frame) => frame.shell);
    const moved = shellMoves(frames);
    // With the Host taking 500-700 ms, a shell that waits for it stands still for that long.
    assert.ok(moved.t - flying[0].t < 80, `${name}: the shell sets off at once, not when the Host answers (still for ${Math.round(moved.t - flying[0].t)} ms)`);
    assert.ok(moved.t < 300, `${name}: moving ${Math.round(moved.t)} ms after the click`);

    // No view is built under a moving shell: that is what stopped it in the air.
    const leaving = index === 1 ? "chatMounted" : "hubMounted";
    const arriving = index === 1 ? "hubMounted" : "chatMounted";
    const swap = frames.find((frame) => frame[arriving]);
    assert.ok(swap, `${name}: the other view opens`);
    assert.ok(frames.filter((frame) => frame.t < swap.t).every((frame) => frame[leaving]), `${name}: there is never a frame with neither view`);
    const atSwap = flying.filter((frame) => frame.t <= swap.t).map((frame) => frame.shell);
    const parked = atSwap.at(-1);
    const before = atSwap.at(-2);
    assert.ok(Math.hypot(parked[0] - before[0], parked[1] - before[1]) < 1.5, `${name}: the views swap with the shell at rest, not in flight`);

    // The hand-over has no gap: from the shell's first frame, it or the box it became is always there.
    const first = frames.indexOf(flying[0]);
    const shown = (frame) => frame.shell || (index === 1 ? frame.hubShown : frame.composerShown);
    frames.slice(first).forEach((frame) => assert.ok(shown(frame), `${name}: nothing holds the box at ${Math.round(frame.t)} ms (${frame.phase})`));

    // The transcript is hidden from its birth until it fades in, so its going to its end is never seen.
    if (index === 0) {
      const born = frames.find((frame) => frame.transcript !== null);
      assert.ok(born.transcript < .05, `${name}: the transcript is hidden the frame it mounts (shows ${born.transcript})`);
      const seen = frames.filter((frame) => frame.transcript > .02);
      assert.ok(seen.length, `${name}: the transcript comes back`);
      assert.ok(seen.every((frame) => frame.scroll === seen[0].scroll), `${name}: it does not jump while it shows`);
      assert.ok(seen.some((frame) => frame.transcript > .1 && frame.transcript < .9), `${name}: it fades in`);
    }

    // The thumb travels to the chosen item.
    const thumbs = frames.map((frame) => frame.thumb);
    const travel = Math.abs(thumbs.at(-1) - thumbs[0]);
    assert.ok(travel > 10, `${name}: the thumb ends on the other item`);
    assert.ok(Math.max(...thumbs.slice(1).map((value, at) => Math.abs(value - thumbs[at]))) < travel * .6, `${name}: the thumb travels, it does not jump`);
    await page.waitForTimeout(300);
  }
  await page.close();

  // A row of the new list chosen while the shell is in the air is where this goes: the landing must not overrule it.
  const second = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  second.on("pageerror", (error) => errors.push(error.message));
  await open(second);
  await second.evaluate(() => document.querySelectorAll(".thread-header-actions .pf-toggle [role=radio]")[1].click());
  await second.waitForSelector(".mode-shell", { state: "attached" });
  const rows = await second.locator(".compact-thread-row").evaluateAll((nodes) => nodes.map((node) => node.dataset.threadId));
  assert.ok(rows.every((id) => BUILD.includes(id)), "the list is already the build list");
  await second.evaluate((id) => document.querySelector(`.compact-thread-row[data-thread-id="${id}"] .compact-thread-main`).click(), "visual-2");
  await second.waitForFunction(() => !document.querySelector(".mode-shell, [data-morph], [data-mode-morph]") && document.querySelector(".project-mode-stage"), null, { timeout: 8000 });
  assert.equal(await second.locator(".compact-thread-row.active").getAttribute("data-thread-id"), "visual-2", "the chosen row stays open");

  await second.close();

  // A conversation that cannot be read: the switch says so, stands where the app is, and leaves nothing behind.
  const third = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  third.on("pageerror", (error) => errors.push(error.message));
  await open(third);
  await third.evaluate(() => { window.hostFixture.failRead = true; });
  const before = await third.locator(".pf-toggle [role=radio][aria-checked=true]").getAttribute("aria-label");
  await third.evaluate(() => document.querySelectorAll(".thread-header-actions .pf-toggle [role=radio]")[1].click());
  await third.getByRole("alert").filter({ hasText: "Fixture could not read" }).waitFor({ timeout: 5000 });
  await third.waitForFunction(() => !document.querySelector(".mode-shell, [data-morph], [data-mode-morph]"));
  assert.equal(await third.locator(".pf-toggle [role=radio][aria-checked=true]").getAttribute("aria-label"), before, "the switch goes back");
  assert.equal(await third.locator(".project-mode-stage").count(), 0, "the chat stays");
  assert.ok(await third.locator(".conversation-stage").isVisible());
  assert.equal(await third.evaluate(() => Number(getComputedStyle(document.querySelector(".conversation-stage > *")).opacity)), 1, "and shows again");
  await third.close();
  assert.deepEqual(errors, []);
  console.log("Mode carry: the shell leaves at once, views swap at rest, the hand-over has no gap, the transcript fades in hidden, the thumb travels, a chosen row stays");
} finally { await browser.close(); }
