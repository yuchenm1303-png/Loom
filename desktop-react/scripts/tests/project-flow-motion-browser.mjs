import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const settle = page => page.waitForTimeout(650);
const toggle = (page, id) => page.locator(`.pf-branch-toggle[data-branch="${id}"]`);
// Runs `act` and samples every painted frame for 500 ms: the most one visible card overlaps another (boxes cut
// by their clip-path, faint ones ignored), and how far the named card moved while it was visible.
const watch = (page, act, follow) => page.evaluate(async ({ act, follow }) => {
  const inset = (node) => {
    const match = /inset\(([^r)]*)/.exec(node.style.clipPath || "");
    if (!match) return [0, 0];
    const v = match[1].trim().split(/\s+/).map(parseFloat);
    return [v[0], v.length > 2 ? v[2] : v[0]];
  };
  const shown = (node) => {
    let o = 1;
    for (let n = node.querySelector(":scope > .pf-card") || node; n && n !== document.body; n = n.parentElement) o *= Number(getComputedStyle(n).opacity);
    return o;
  };
  let overlap = 0, drift = 0, start = null;
  new Function(act)();
  const end = performance.now() + 500;
  while (performance.now() < end) {
    await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
    const seen = [...document.querySelectorAll("[data-glide]")].filter((node) => shown(node) > 0.05).map((node) => {
      const r = node.getBoundingClientRect(), [top, bottom] = inset(node);
      return { node, left: r.left, right: r.right, top: r.top + top, bottom: r.bottom - bottom };
    });
    for (let i = 0; i < seen.length; i += 1) for (let j = i + 1; j < seen.length; j += 1) {
      const a = seen[i], b = seen[j];
      if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0) overlap = Math.max(overlap, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
    }
    const card = follow && seen.find((item) => item.node.dataset.glide === follow);
    if (card) { const y = card.node.getBoundingClientRect().top; start ??= y; drift = Math.max(drift, Math.abs(y - start)); }
  }
  return { overlap, drift };
}, { act, follow });

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

    // Nothing is painted over a neighbour while the hub rearranges: an arriving card unrolls into the room its
    // neighbours make, a leaving one is folded into their closing gap, and a leaving card holds its place.
    for (const [name, act, follow] of [
      ["fold scope", `document.querySelector('[data-branch="scope"]').click()`],
      ["unfold scope", `document.querySelector('[data-branch="scope"]').click()`],
      ["fold plan", `document.querySelector('[data-branch="plan"]').click()`],
      ["unfold plan", `document.querySelector('[data-branch="plan"]').click()`],
      ["fold timeline", `document.querySelector('[data-branch="loom"]').click()`],
      ["unfold timeline", `document.querySelector('[data-branch="loom"]').click()`],
      ["open model", `document.querySelector('.pf-model-chip').click()`],
      ["close model", `document.querySelector('.pf-model-chip').click()`, "model"],
    ]) {
      const seen = await watch(page, act, follow);
      assert.ok(seen.overlap <= 1, `${name}: a card painted ${seen.overlap.toFixed(1)}px over its neighbour`);
      assert.ok(seen.drift <= 1, `${name}: the leaving card drifted ${seen.drift.toFixed(1)}px`);
      await settle(page);
    }
    assert.equal(await page.locator('[data-glide][style*="clip-path"]').count(), 0, "no cut is left behind");

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
  // Opening, the details take their room at once and are uncovered as the hub glides away from them.
  const thread = await page.locator('.pf-thread').evaluate(node => node.getBoundingClientRect().height);
  assert.ok(middle === full && thread > 0, `workspace disclosure takes its room at once: ${middle}, ${full}`);
  await button.click();
  await settle(page);
  assert.equal(await page.locator('.pf-disclosure').evaluate(node => node.inert && node.getBoundingClientRect().height < 1), true);
  await page.close();
  console.log("Project motion: no card painted over a neighbour, exit anchoring, reversal, agent compression, simultaneous folds, short windows, reduced motion and workspace disclosure passed in both themes.");
} finally { await browser.close(); }
