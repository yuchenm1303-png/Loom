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

    // An arriving card hangs from the top of its room: its head is in sight from the first frames, the rows unroll under it.
    for (const id of ["scope", "plan", "loom"]) {
      await toggle(page, id).click();
      await settle(page);
      const head = await page.evaluate(async (id) => {
        document.querySelector(`[data-branch="${id}"]`).click();
        const seen = [];
        for (let i = 0; i < 6; i += 1) {
          await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
          const node = document.querySelector(`[data-glide="${id}"]`);
          const match = /inset\(([\d.]+)px/.exec(node.style.clipPath || "");
          if (!match) continue;
          const top = node.getBoundingClientRect().top + parseFloat(match[1]);
          seen.push(node.querySelector(".pf-card-head").getBoundingClientRect().top - top);
        }
        return seen;
      }, id);
      assert.ok(head.length && head.every((gap) => gap >= -1 && gap < 24), `${id}: the head leads the unroll: ${head.map((gap) => gap.toFixed(1))}`);
      await settle(page);
    }
    assert.equal(await page.locator('[data-glide] [style*="translate"]').count(), 0, "no hung part is left displaced");

    // A column moves as a ripple, the card in front (in the direction of travel) first: closing in, the card next to
    // the change sets off first; making room, the farthest one. The arriving card's rows come in one after another.
    const ripple = (id) => page.evaluate(async (id) => {
      document.querySelector('[data-branch="scope"]').click();
      await new Promise(requestAnimationFrame);
      const delay = (glide) => document.querySelector(`[data-glide="${glide}"]`).getAnimations().find((a) => a.id === "pf-glide")?.effect.getTiming().delay;
      const rows = [...(document.querySelector(`[data-glide="${id}"]`)?.getAnimations({ subtree: true }) ?? [])]
        .filter((a) => a.id === "pf-row").map((a) => a.effect.getTiming().delay);
      return { files: delay("files"), knowledge: delay("knowledge"), rows };
    }, id);
    const closing = await ripple("scope");
    assert.ok(closing.files === 0 && closing.knowledge > 0, `closing in, the nearest card leads: ${JSON.stringify(closing)}`);
    await settle(page);
    const opening = await ripple("scope");
    assert.ok(opening.knowledge === 0 && opening.files > 0, `making room, the farthest card leads: ${JSON.stringify(opening)}`);
    assert.ok(opening.rows.length >= 3 && opening.rows.every((delay, index) => !index || delay > opening.rows[index - 1]), `rows come in one after another: ${opening.rows}`);
    await settle(page);

    // Model opening compresses the agent's own details continuously rather than display:none.
    const expanded = await page.locator('.pf-agent').first().evaluate(node => node.getBoundingClientRect().height);
    // Clicked and read inside the page, every frame: a locator click waits for the hub to be still first, which can
    // outlast the fold. The chip takes focus as a real click gives it, so Escape reaches it below.
    const heights = await page.evaluate(async () => {
      const chip = document.querySelector('.pf-model-chip');
      chip.focus();
      chip.click();
      const seen = [];
      for (const end = performance.now() + 500; performance.now() < end;) {
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        seen.push(document.querySelector('.pf-agent').getBoundingClientRect().height);
      }
      return seen;
    });
    await settle(page);
    const compact = await page.locator('.pf-agent').first().evaluate(node => node.getBoundingClientRect().height);
    const between = heights.filter((height) => height > compact + 4 && height < expanded - 4);
    assert.ok(compact < expanded && between.length >= 3, `agent height interpolates: ${expanded} -> ${compact} via ${heights.map(Math.round)}`);
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

    // The right column's two pickers take turns: the model card unrolls under the history card as it rolls up,
    // never painted over it, and the history button lets go.
    await toggle(page, "history").click();
    await settle(page);
    const swap = await watch(page, "document.querySelector('.pf-model-chip').click()");
    assert.ok(swap.overlap <= 1, `picker hand-over paints no card over another: ${JSON.stringify(swap)}`);
    await settle(page);
    assert.equal(await toggle(page, "history").getAttribute("aria-pressed"), "false");
    const back = await watch(page, `document.querySelector('[data-branch="history"]').click()`);
    assert.ok(back.overlap <= 1, `and back: ${JSON.stringify(back)}`);
    await settle(page);
    assert.equal(await page.locator('.pf-model-chip').getAttribute("aria-expanded"), "false");

    // Cables are drawn on the frame the cards move: each leaves its card's shown head, and a plug eases to
    // its new place on the hub instead of jumping there.
    const cables = await page.evaluate(async () => {
      const ends = () => Object.fromEntries([...document.querySelectorAll(".pf-cable[data-side='left'], .pf-cable[data-side='right']")]
        .filter((g) => !g.hasAttribute("data-leaving")).map((g) => {
          const path = g.querySelector(".pf-cable-body");
          const a = path.getPointAtLength(0), b = path.getPointAtLength(path.getTotalLength());
          return [g.dataset.cable, { a: a.y, b: b.y }];
        }));
      const head = (id) => {
        const slot = document.querySelector(`[data-glide="${CSS.escape(id)}"]`);
        if (!slot) return null;
        const card = slot.querySelector(".pf-card") || slot, r = card.getBoundingClientRect(), s = slot.getBoundingClientRect();
        const cut = /inset\(([\d.]+)px \S+ ([\d.]+)px/.exec(slot.style.clipPath || "");
        const top = Math.max(r.top, cut ? s.top + Number(cut[1]) : -Infinity), bottom = Math.min(r.bottom, cut ? s.bottom - Number(cut[2]) : Infinity);
        return Math.min(top + 34, top + Math.max(0, bottom - top) / 2) - document.querySelector(".pf-stage").getBoundingClientRect().top;
      };
      let lag = 0, last = ends();
      const step = {}, path = {};
      document.querySelector('[data-branch="repo"]').click();
      for (const end = performance.now() + 500; performance.now() < end;) {
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        const now = ends();
        for (const [id, { a, b }] of Object.entries(now)) {
          const want = head(id);
          if (want != null) lag = Math.max(lag, Math.abs(want - a));
          if (last[id]) { const move = Math.abs(b - last[id].b); step[id] = Math.max(step[id] ?? 0, move); path[id] = (path[id] ?? 0) + move; }
        }
        last = now;
      }
      // A snap moves a plug its whole way in one frame; an eased one a part of it (the spring peaks near a ninth).
      const jump = Math.max(0, ...Object.entries(step).map(([id, most]) => most / (path[id] + 4)));
      return { lag, jump };
    });
    assert.ok(cables.lag < 1.5 && cables.jump < 0.35, `cables follow their cards and plugs ease: ${JSON.stringify(cables)}`);
    await toggle(page, "repo").click();
    await settle(page);

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
  await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&draft=1&dirty=1&proposal=1`);
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
