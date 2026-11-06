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

  // Opening a conversation that already exists is still a hub of its own.
  {
    const { page } = await open("latency=0");
    await page.evaluate(() => document.querySelector(".pf-new-thread").click());
    await page.waitForTimeout(1200);
    await page.evaluate(() => { window.__hub = document.querySelector(".pf-stage"); });
    await page.locator('[data-branch="history"]').click();
    await page.waitForTimeout(500);
    const others = page.locator(".bh-row:not([data-current]) .bh-open");
    if (await others.count()) {
      await others.first().click();
      await page.waitForTimeout(800);
      assert.equal(await page.evaluate(() => document.querySelector(".pf-stage") === window.__hub), false, "another conversation is another hub");
    }
    await page.close();
  }
  console.log("Project new conversation: one hub with no blank frame, fade then leave then first words, caret and cleared field, slow Host, double click, refusal, reduced motion and opening an existing one passed in both themes.");
} finally { await browser.close(); }
