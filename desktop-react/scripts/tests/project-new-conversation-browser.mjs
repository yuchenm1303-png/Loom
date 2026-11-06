// A new conversation from the build hub: one hub all the way, the old conversation leaving piece by piece.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

const open = async (query, options = {}) => {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  if (options.reduced) await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`${origin}/scripts/fixtures/live-app.html?project=live&newThreads=1&${query}`);
  await page.waitForTimeout(600);
  if (!(await page.locator(".pf-stage").count())) await page.locator(".project-mode-toggle button, .pf-toggle button").last().click();
  await page.locator(".pf-new-thread").waitFor({ timeout: 15000 });
  await page.locator('[data-glide^="agent:"]').first().waitFor({ timeout: 15000 });
  await page.waitForTimeout(1200);
  return { page, errors };
};
// Clicks the quill and reads every painted frame for `ms`.
const renew = (page, ms = 1500) => page.evaluate(async (ms) => {
  const stage = document.querySelector(".pf-stage"), hub = document.querySelector(".pf-composer");
  const said = document.querySelector(".pf-thread .pf-said, .pf-thread .pf-bubble, .pf-thread .pf-reply");
  const words = said?.textContent;
  const top = hub.getBoundingClientRect().top;
  const seen = { noHub: 0, otherHub: 0, biggestStep: 0, oldWordsAfterFade: 0, narrationWhileRenewing: 0, sooner: null, cardsLeft: null, pulses: 0, fade: [] };
  let last = top;
  const start = performance.now();
  document.querySelector(".pf-new-thread").click();
  // The click answers in its own frame: the fade has started before anything else moves.
  await new Promise(requestAnimationFrame);
  seen.renewingAtOnce = stage.hasAttribute("data-renewing");
  for (;;) {
    await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
    const at = performance.now() - start;
    if (at > ms) break;
    const now = document.querySelector(".pf-stage"), node = document.querySelector(".pf-composer");
    if (!now) { seen.noHub += 1; continue; }
    if (now !== stage || node !== hub) seen.otherHub += 1;
    const y = node.getBoundingClientRect().top;
    seen.biggestStep = Math.max(seen.biggestStep, Math.abs(y - last));
    last = y;
    const thread = now.querySelector(".pf-thread"), opacity = Number(getComputedStyle(thread).opacity);
    if (at < 400) seen.fade.push(opacity);
    if (at > 250 && words && thread.textContent.includes(words) && opacity > 0.02) seen.oldWordsAfterFade += 1;
    const narration = now.querySelector(".pf-narration");
    if (narration && now.dataset.phase === "draft" && now.hasAttribute("data-renewing") && opacity > 0.05) seen.narrationWhileRenewing += 1;
    if (narration && !now.querySelector('[data-glide^="agent:"]:not([data-leaving])') && opacity > 0.3 && seen.sooner == null) seen.sooner = at;
    if (seen.cardsLeft == null && !now.querySelector('[data-leaving]') && !now.querySelector('[data-glide^="agent:"]')) seen.cardsLeft = at;
    if (now.dataset.phase === "draft") seen.pulses += now.querySelectorAll('.pf-pulse[data-back]').length;
    // A card that leaves goes with what it showed.
    const leavingScope = now.querySelector('[data-glide="scope"][data-leaving]');
    if (leavingScope) seen.scopeRows = Math.min(seen.scopeRows ?? 99, leavingScope.querySelectorAll(".pf-well").length);
    const leavingPlan = now.querySelector('[data-glide="plan"][data-leaving]');
    if (leavingPlan) seen.planRows = Math.min(seen.planRows ?? 99, leavingPlan.querySelectorAll(".pf-task").length);
  }
  return { ...seen, hubMoved: Math.abs(last - top), active: document.activeElement?.tagName };
}, ms);

try {
  for (const theme of ["dark", "light"]) {
    const { page, errors } = await open(`theme=${theme}&latency=120`);
    await page.locator(".pf-input textarea").fill("这句话留给旧对话");
    const seen = await renew(page);
    assert.equal(seen.renewingAtOnce, true, "the click answers at once");
    assert.equal(seen.noHub, 0, "there is never a frame without a hub");
    assert.ok(seen.scopeRows >= 1 && seen.planRows >= 1, `a card leaves with what it showed: ${seen.scopeRows}, ${seen.planRows}`);
    assert.equal(seen.otherHub, 0, "it is the same hub all the way");
    assert.ok(seen.fade.some((value) => value > 0.05 && value < 0.95), `what is written fades: ${seen.fade.map((value) => value.toFixed(2))}`);
    assert.equal(seen.oldWordsAfterFade, 0, "the old conversation's words are gone once faded");
    assert.equal(seen.narrationWhileRenewing, 0, "the new words wait for the new conversation");
    assert.equal(seen.pulses, 0, "no task 'landed' because the count went back to nothing");
    assert.ok(seen.biggestStep < 40, `the hub glides: ${seen.biggestStep}`);
    assert.equal(seen.active, "TEXTAREA", "the caret is in the field");
    await page.waitForTimeout(300);
    assert.equal(await page.locator(".pf-input textarea").inputValue(), "", "what was being written stays behind");
    assert.equal(await page.locator('[data-glide^="agent:"], [data-glide="scope"], [data-glide="loom"], [data-leaving], [data-pinned]').count(), 0, "the old conversation's cards have left");
    assert.equal(await page.locator('[data-glide="repo"]').count(), 1, "the project's own card stayed");
    assert.equal(await page.locator(".pf-stage").getAttribute("data-phase"), "draft");
    assert.equal(await page.locator(".pf-stage").getAttribute("data-renewing"), null);
    assert.equal(await page.locator('[data-glide][style*="clip-path"]').count(), 0, "no cut is left behind");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // A slow Host: the old conversation has already left, nothing can be sent into the gap, and then it lands.
  {
    const { page, errors } = await open("latency=1500");
    await page.evaluate(() => document.querySelector(".pf-new-thread").click());
    await page.waitForTimeout(900);
    assert.equal(await page.locator(".pf-stage").getAttribute("data-renewing"), "true", "still being made");
    assert.equal(await page.locator('.pf-thread .pf-said, .pf-thread .pf-reply').count(), 0, "the old conversation left without waiting for the Host");
    assert.equal(await page.evaluate(() => document.querySelector(".pf-send").disabled), true, "nothing can be sent into the gap");
    // A second click is not a second conversation.
    const before = await page.locator(".pf-new-thread").count();
    await page.evaluate(() => document.querySelector(".pf-new-thread").click());
    await page.waitForTimeout(100);
    assert.equal(await page.locator(".pf-stage").getAttribute("data-phase"), "draft", "the second click changes nothing");
    assert.equal(await page.locator(".pf-stage").getAttribute("data-renewing"), "true");
    await page.waitForTimeout(1300);
    assert.equal(before, 1);
    assert.equal(await page.locator(".pf-stage").getAttribute("data-renewing"), null, "landed");
    assert.equal(await page.evaluate(async () => (await window.loom.call("thread/list", {})).threads.filter((entry) => entry.id.startsWith("new-")).length), 1);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // In a conversation nothing has been said in, new stays there: no thread is made, the hub does not stir, the field is ready.
  {
    const { page, errors } = await open("latency=0");
    await page.evaluate(() => document.querySelector(".pf-new-thread").click());
    await page.waitForTimeout(1800);
    const created = () => page.evaluate(async () => (await window.loom.call("thread/list", {})).threads.filter((entry) => entry.id.startsWith("new-")).length);
    assert.equal(await created(), 1);
    await page.locator(".pf-input textarea").fill("正在写的话");
    await page.locator('[data-branch="history"]').focus();
    const stayed = await page.evaluate(async () => {
      const stage = document.querySelector(".pf-stage");
      document.querySelector(".pf-new-thread").click();
      let renewed = false;
      for (let i = 0; i < 20; i += 1) {
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        renewed ||= stage.hasAttribute("data-renewing") || document.querySelector("[data-leaving]") !== null;
      }
      return { renewed, active: document.activeElement?.tagName };
    });
    assert.deepEqual(stayed, { renewed: false, active: "TEXTAREA" }, "the empty conversation stays");
    assert.equal(await created(), 1, "no second empty conversation");
    assert.equal(await page.locator(".pf-input textarea").inputValue(), "正在写的话", "what is being written stays");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Refused: what the hub showed comes back, and it says why.
  {
    const { page } = await open("newFails=1&latency=120");
    await page.evaluate(() => document.querySelector(".pf-new-thread").click());
    await page.waitForTimeout(1600);
    assert.ok(await page.locator('[data-glide^="agent:"]').count() >= 1, "the cards came back");
    assert.equal(await page.locator(".pf-stage").getAttribute("data-phase") === "draft", false);
    assert.equal(await page.locator(".pf-stage").getAttribute("data-renewing"), null);
    assert.ok(await page.getByText("The Host refused the new conversation.").count() >= 1, "the failure is shown");
    await page.close();
  }

  // Reduced motion: at once, with no glides.
  {
    const { page, errors } = await open("latency=0", { reduced: true });
    await page.evaluate(() => document.querySelector(".pf-new-thread").click());
    await page.waitForTimeout(200);
    assert.equal(await page.locator('[data-glide^="agent:"]').count(), 0);
    assert.equal(await page.locator(".pf-stage").getAttribute("data-phase"), "draft");
    assert.equal(await page.evaluate(() => document.getAnimations().some((animation) => animation.id === "pf-glide")), false);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // The shortcut (like the sidebar) asks for a new one in this space: the same one hub.
  {
    const { page, errors } = await open("latency=0");
    const same = await page.evaluate(async () => {
      const stage = document.querySelector(".pf-stage");
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "n", ctrlKey: true, bubbles: true }));
      let blank = 0;
      for (let i = 0; i < 40; i += 1) {
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        if (document.querySelector(".pf-stage") !== stage) blank += 1;
      }
      return { blank, phase: stage.dataset.phase };
    });
    assert.deepEqual(same, { blank: 0, phase: "draft" }, "the shortcut goes on in the same hub");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Opening a conversation that exists: the same hub again, words out first and in last, the cards rearranged between.
  const go = (page, id, ms = 1600) => page.evaluate(async ({ id, ms }) => {
    const stage = document.querySelector(".pf-stage"), hub = document.querySelector(".pf-composer");
    const words = stage.querySelector(".pf-thread").textContent;
    const seen = { noHub: 0, otherHub: 0, travelled: 0, oldUnderNew: 0, fade: [], pulses: 0, washes: 0, sendWhileGoing: null };
    let y = null;
    document.querySelector(`[data-thread-id="${id}"] button`).click();
    const start = performance.now();
    while (performance.now() - start < ms) {
      await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
      const now = document.querySelector(".pf-stage");
      if (!now) { seen.noHub += 1; continue; }
      if (now !== stage || now.querySelector(".pf-composer") !== hub) seen.otherHub += 1;
      const thread = now.querySelector(".pf-thread"), opacity = Number(getComputedStyle(thread).opacity), bottom = thread.getBoundingClientRect().bottom;
      if (y != null && opacity > 0.05) seen.travelled += Math.abs(bottom - y);
      y = bottom;
      if (now.dataset.phase === "live" && now.hasAttribute("data-renewing")) seen.fade.push(opacity);
      if (now.hasAttribute("data-renewing") && seen.sendWhileGoing == null) seen.sendWhileGoing = now.querySelector(".pf-send").disabled;
      seen.pulses += now.querySelectorAll('.pf-pulse[data-back]').length;
      seen.washes += now.querySelectorAll('[data-glide="repo"] .pf-task-wash').length;
    }
    return { ...seen, phase: stage.dataset.phase, renewing: stage.hasAttribute("data-renewing"), active: document.activeElement?.tagName, changed: stage.querySelector(".pf-thread").textContent !== words };
  }, { id, ms });
  const hubIsWhole = (seen, what) => {
    assert.equal(seen.noHub, 0, `${what}: never a frame without a hub`);
    assert.equal(seen.otherHub, 0, `${what}: the same hub all the way`);
    assert.ok(seen.travelled < 1, `${what}: words fade, they do not travel: ${seen.travelled}`);
    assert.equal(seen.renewing, false, `${what}: at rest in the end`);
    assert.equal(seen.changed, true, `${what}: the words are the other conversation's`);
    assert.equal(seen.pulses + seen.washes, 0, `${what}: nothing 'landed' because the conversation changed`);
  };
  for (const theme of ["dark", "light"]) {
    const { page, errors } = await open(`theme=${theme}&latency=40`);
    await page.locator(".pf-input textarea").fill("写给第一个对话");
    const toEmpty = await go(page, "live-1");
    hubIsWhole(toEmpty, "to an empty one");
    assert.equal(toEmpty.phase, "draft");
    assert.ok(toEmpty.fade.some((value) => value < 0.95), `what is written fades at once: ${toEmpty.fade}`);
    assert.equal(toEmpty.sendWhileGoing, true, "nothing can be sent while the hub goes over");
    assert.equal(await page.locator(".pf-input textarea").inputValue(), "", "what was being written stays behind");
    assert.equal(await page.locator('[data-glide^="agent:"], [data-leaving], [data-pinned]').count(), 0, "its cards have left");
    assert.equal(await page.locator('[data-glide="repo"]').count(), 1);
    const back = await go(page, "live-0");
    hubIsWhole(back, "back, already read");
    assert.equal(back.phase, "live");
    assert.notEqual(back.active, "TEXTAREA", "a conversation with something in it does not take the caret");
    assert.ok(await page.locator('[data-glide^="agent:"]').count() >= 1, "its cards came back");
    assert.equal(await page.locator('[data-glide][style*="clip-path"]').count(), 0, "no cut is left behind");
    assert.deepEqual(errors, []);
    await page.close();
  }
  // Not read yet: what the hub shows stays, without its words and its buttons, until the other's overview is here.
  {
    const { page, errors } = await open("latency=0&overviewLatency=900");
    const waiting = page.evaluate(async () => {
      document.querySelector('[data-thread-id="live-1"] button').click();
      await new Promise((resolve) => setTimeout(resolve, 500));
      const stage = document.querySelector(".pf-stage");
      return { phase: stage.dataset.phase, renewing: stage.hasAttribute("data-renewing"), agents: stage.querySelectorAll('[data-glide^="agent:"]').length,
               opacity: Number(getComputedStyle(stage.querySelector(".pf-thread")).opacity), pause: stage.querySelector('.pf-control')?.disabled };
    });
    const during = await waiting;
    assert.equal(during.phase, "live", "the hub holds what it shows");
    assert.equal(during.renewing, true);
    assert.ok(during.agents >= 1 && during.opacity < 0.05, `its cards stay, its words are gone: ${JSON.stringify(during)}`);
    assert.equal(during.pause, true, "its buttons no longer reach anything");
    await page.waitForTimeout(1900);
    assert.equal(await page.locator(".pf-stage").getAttribute("data-phase"), "draft");
    assert.equal(await page.locator(".pf-stage").getAttribute("data-renewing"), null);
    assert.deepEqual(errors, []);
    await page.close();
  }
  // Asked for two in a row: it ends at the last, with nothing left halfway.
  {
    const { page, errors } = await open("latency=80&overviewLatency=100");
    await page.evaluate(async () => {
      document.querySelector('[data-thread-id="live-1"] button').click();
      await new Promise((resolve) => setTimeout(resolve, 60));
      document.querySelector('[data-thread-id="live-2"] button').click();
    });
    await page.waitForTimeout(2200);
    assert.equal(await page.locator(".pf-stage").getAttribute("data-phase"), "live", "at the last one asked for");
    assert.equal(await page.locator(".pf-stage").getAttribute("data-renewing"), null);
    assert.equal(await page.locator('[data-leaving], [data-pinned]').count(), 0);
    assert.deepEqual(errors, []);
    await page.close();
  }
  // The other's overview cannot be read: it says so, with the way to try again, not the first one's cards under its title.
  {
    const { page } = await open("latency=0");
    await page.evaluate(() => {
      const call = window.loom.call;
      window.loom.call = (method, args) => method === "projectRun/overview" && args.threadId === "live-1" ? Promise.reject(new Error("no")) : call.call(window.loom, method, args);
      document.querySelector('[data-thread-id="live-1"] button').click();
    });
    await page.waitForTimeout(800);
    assert.equal(await page.locator(".pf-loading-note button").count(), 1, "the way to try again");
    await page.close();
  }
  // Reduced motion: at once.
  {
    const { page, errors } = await open("latency=0", { reduced: true });
    await page.evaluate(() => document.querySelector('[data-thread-id="live-1"] button').click());
    await page.waitForTimeout(300);
    assert.equal(await page.locator(".pf-stage").getAttribute("data-phase"), "draft");
    assert.equal(await page.locator('[data-glide^="agent:"]').count(), 0);
    assert.equal(await page.evaluate(() => document.getAnimations().some((animation) => animation.id === "pf-glide" || animation.id === "pf-contents")), false);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Project conversations: one hub with no blank frame for a new one and for opening one, fade then rearrange then words, caret and cleared field, slow reads, double clicks, two in a row, refusal, failed read and reduced motion passed in both themes.");
} finally { await browser.close(); }
