import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const settle = page => page.waitForTimeout(650);
const toggle = (page, id) => page.locator(`.pf-branch-toggle[data-branch="${id}"]`);

try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&many=1&theme=${theme}`);
    await page.locator('[data-glide="repo"]').waitFor();
    await settle(page);

    // Exit stays at its visible location even though the centred column immediately shrinks.
    const continuity = await page.evaluate(async () => {
      const card = document.querySelector('[data-glide="scope"]');
      const before = card.getBoundingClientRect();
      const sibling = document.querySelector('[data-glide="files"]');
      const siblingBefore = sibling.getBoundingClientRect();
      document.querySelector('[data-branch="scope"]').click();
      await new Promise(requestAnimationFrame);
      const after = card.getBoundingClientRect();
      const siblingAfter = sibling.getBoundingClientRect();
      return { jump: Math.hypot(after.x - before.x, after.y - before.y), pinned: card.dataset.pinned, inert: card.inert,
        siblingJump: Math.hypot(siblingAfter.x - siblingBefore.x, siblingAfter.y - siblingBefore.y),
        gliding: sibling.getAnimations().some(a => a.id === "pf-glide") };
    });
    assert.ok(continuity.jump < 1, `exit stays in place: ${JSON.stringify(continuity)}`);
    assert.equal(continuity.pinned, "1");
    assert.equal(continuity.inert, true);
    assert.ok(continuity.siblingJump < 5, `neighbour starts from its visible position: ${JSON.stringify(continuity)}`);
    assert.equal(continuity.gliding, true, "neighbour glides into the freed space");
    await settle(page);
    assert.equal(await page.locator('[data-glide="scope"]').count(), 0);

    // Reversing before retention expires keeps the same DOM and returns it to normal flow.
    await toggle(page, "scope").click();
    await settle(page);
    const sameNode = await page.locator('[data-glide="scope"]').elementHandle();
    await page.evaluate(async () => {
      const button = document.querySelector('[data-branch="scope"]');
      button.click();
      await new Promise(resolve => setTimeout(resolve, 65));
      button.click();
    });
    await settle(page);
    assert.equal(await sameNode.evaluate(node => node === document.querySelector('[data-glide="scope"]') && !node.dataset.pinned && !node.inert), true);
    assert.equal(await page.locator('[data-pinned]').count(), 0);

    // Model opening compresses the agent's own details continuously rather than display:none.
    const expanded = await page.locator('.pf-agent').first().evaluate(node => node.getBoundingClientRect().height);
    await page.locator('.pf-model-chip').click();
    await page.waitForTimeout(90);
    const middle = await page.locator('.pf-agent').first().evaluate(node => node.getBoundingClientRect().height);
    await settle(page);
    const compact = await page.locator('.pf-agent').first().evaluate(node => node.getBoundingClientRect().height);
    assert.ok(compact < middle && middle < expanded, `agent height interpolates: ${expanded}, ${middle}, ${compact}`);
    await page.locator('.pf-model-slot').press("Escape");
    await settle(page);
    assert.ok(Math.abs(await page.locator('.pf-agent').first().evaluate(node => node.getBoundingClientRect().height) - expanded) < 1);

    // Multiple branches and the timeline can close together; all return without stale pinning.
    await page.evaluate(() => ["repo", "files", "agents", "plan", "loom"].forEach(id => document.querySelector(`[data-branch="${id}"]`).click()));
    await settle(page);
    assert.equal(await page.locator('[data-leaving], [data-pinned]').count(), 0);
    await page.evaluate(() => ["repo", "files", "agents", "plan", "loom"].forEach(id => document.querySelector(`[data-branch="${id}"]`).click()));
    await settle(page);
    assert.equal(await page.locator('[data-glide^="agent:"]').count(), 3);
    assert.equal(await page.locator('[data-pinned]').count(), 0);

    // A short window still contains every card; lists take the overflow.
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.locator('.pf-model-chip').click();
    await settle(page);
    assert.equal(await page.locator('.pf-stage').evaluate(stage => {
      const box = stage.getBoundingClientRect();
      return [...stage.querySelectorAll('.pf-slot > .pf-card')].every(card => {
        const r = card.getBoundingClientRect();
        return r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
      });
    }), true, "cards remain inside a short stage");

    // The shared chat/build morph still fades the outer slots on landing.
    await page.locator('.project-mode-stage').evaluate(node => { node.dataset.morph = "arrive"; });
    await settle(page);
    assert.equal(await page.locator('[data-glide="repo"]').evaluate(node => Number(getComputedStyle(node).opacity)), 0);
    await page.locator('.project-mode-stage').evaluate(node => { node.dataset.morph = "landed"; });
    await settle(page);
    assert.equal(await page.locator('[data-glide="repo"]').evaluate(node => Number(getComputedStyle(node).opacity)), 1);

    // System reduced motion also releases departures immediately and skips WAAPI reorders.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await toggle(page, "files").click();
    await page.waitForTimeout(50);
    assert.equal(await page.locator('[data-glide="files"]').count(), 0);
    assert.equal(await page.evaluate(() => document.getAnimations().some(a => a.id === "pf-glide")), false);
    assert.deepEqual(errors, []);
    await page.close();
  }

  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&draft=1&dirty=1`);
  await settle(page);
  const button = page.locator('[aria-controls="pf-dirty-details"]');
  await button.click();
  await page.waitForTimeout(90);
  const middle = await page.locator('.pf-disclosure').evaluate(node => node.getBoundingClientRect().height);
  await settle(page);
  const full = await page.locator('.pf-disclosure').evaluate(node => node.getBoundingClientRect().height);
  assert.ok(middle > 0 && middle < full, `workspace disclosure interpolates: ${middle}, ${full}`);
  await button.click();
  await settle(page);
  assert.equal(await page.locator('.pf-disclosure').evaluate(node => node.inert && node.getBoundingClientRect().height < 1), true);
  await page.close();
  console.log("Project motion: exit anchoring, reversal, agent compression, simultaneous folds, short windows, reduced motion and workspace disclosure passed in both themes.");
} finally { await browser.close(); }
