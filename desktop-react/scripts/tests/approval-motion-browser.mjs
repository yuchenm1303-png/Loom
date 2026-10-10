import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

// An approval arrives between two tool groups and is answered while the run is live. It grows into its place on
// the log's own height motion and, once answered, folds away on the same one: nothing below it jumps, and
// nothing animates twice (the card's own entrance and the entry's inherited one used to stack on top of it).
try {
  for (const theme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${theme}&scenario=approval`);
    await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"));
    await page.locator('[data-message-id="h-a1"]').waitFor();
    await page.evaluate(() => {
      window.__approval = { grow: [], leave: [], animations: [] };
      const sampleGrowth = () => {
        const entry = document.querySelector(".entry-approval");
        if (!entry) return requestAnimationFrame(sampleGrowth);
        const start = performance.now();
        const loop = () => {
          const target = document.querySelector(".entry-approval");
          if (!target) return;
          window.__approval.grow.push(target.getBoundingClientRect().height);
          if (target.classList.contains("is-leaving")) return window.__approvalLeave(target);
          // Which animations run on the entry and its card at once.
          if (performance.now() - start < 400) window.__approval.animations.push(target.getAnimations({ subtree: true }).filter(a => a.playState === "running").map(a => a.animationName || a.transitionProperty));
          requestAnimationFrame(loop);
        };
        loop();
      };
      window.__approvalLeave = target => {
        const loop = () => {
          const el = document.querySelector(".entry-approval");
          window.__approval.leave.push(el ? el.getBoundingClientRect().height : -1);
          if (el) requestAnimationFrame(loop);
        };
        loop();
      };
      requestAnimationFrame(sampleGrowth);
    });
    await page.evaluate(() => window.liveFixture.send());
    await page.locator(".entry-approval").waitFor({ state: "attached", timeout: 20000 });
    await page.waitForFunction(() => !document.querySelector(".entry-approval") && window.__approval.leave.length > 3, null, { timeout: 20000 });
    const { grow, leave, animations } = await page.evaluate(() => window.__approval);

    const full = Math.max(...grow);
    assert.ok(full > 40, `the card has a height to grow into: ${full}`);
    assert.ok(grow[0] < full * .5, `it starts small: ${grow[0]} of ${full}`);
    const growSteps = grow.slice(1).map((height, index) => height - grow[index]);
    assert.ok(Math.max(...growSteps) < full * .6, `it arrives over several frames, not one: ${growSteps.map(value => value.toFixed(1)).join(" ")}`);
    assert.ok(grow.some(height => height > full * .15 && height < full * .85), "and passes through intermediate heights");
    // One entrance: the entry's height, and the copy's fade. Not three.
    const names = new Set(animations.flat());
    assert.ok(![...names].includes("approval-in") && ![...names].includes("message-in") && ![...names].includes("loom-task-copy-arrive"), `the stacked entrances are gone: ${[...names].join(", ")}`);

    // Answered: it folds away over several frames to nothing, rather than taking its height back at once.
    const shrinking = leave.filter(height => height >= 0);
    assert.ok(shrinking.length > 3, `it stays while it folds: ${leave.length} frames`);
    assert.ok(shrinking[0] > full * .5, "the fold starts from the whole card");
    assert.ok(shrinking.some(height => height > full * .1 && height < full * .9), `through intermediate heights: ${shrinking.map(value => Math.round(value)).join(" ")}`);
    assert.ok(Math.max(...shrinking.slice(1).map((height, index) => shrinking[index] - height)) < full * .8, "and never in one frame");
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Approval cards grow into place on the log's height motion and fold away when answered, with no stacked entrances, in both themes.");
} finally { await browser.close(); }
