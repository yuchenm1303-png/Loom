import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
// A whole change: an exit, the room closing on a long way, or the room opening, the cable and a birth.
const settle = page => page.waitForTimeout(1300);
// A card surface's birth cut (projectArrange.ts), read from its computed clip-path as px from each edge of its box. The
// edges are calc() of the live box (a share of its width, or the box less the shape) so a box that is still growing
// keeps the shape; a whole card's cut reaches past its box. Each length is evaluated against its side of the box.
const CUT_OF = `
  const text = getComputedStyle(card).clipPath || "";
  if (!text.startsWith("inset(")) return null;
  const body = text.slice(6, text.lastIndexOf(")")).split(" round ")[0];
  const parts = [];
  let depth = 0, token = "";
  for (const ch of body) {
    if (ch === " " && !depth) { if (token) parts.push(token); token = ""; continue; }
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    token += ch;
  }
  if (token) parts.push(token);
  const box = card.getBoundingClientRect();
  const value = (length, size) => Function("clamp", "return " + length.replace(/calc/g, "").replaceAll("min(", "Math.min(").replaceAll("max(", "Math.max(")
    .replace(/(-?[\\d.]+)%/g, (_, n) => "(" + n + "*" + size + "/100)").replace(/px/g, ""))((low, value, high) => Math.max(low, Math.min(value, high)));
  const [t, r = t, b = t, l = r] = parts;
  return { box, t: value(t, box.height), r: value(r, box.width), b: value(b, box.height), l: value(l, box.width) };
`;
const toggle = (page, id) => page.locator(`.pf-branch-toggle[data-branch="${id}"]`);
// Runs `act` and samples every painted frame for a whole change: the most one visible card overlaps another (boxes cut
// by their clip-path, faint ones ignored), and how far the named card moved while it was visible.
const watch = (page, act, follow) => page.evaluate(async ({ act, follow, CUT_OF }) => {
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
  const cutOf = new Function("card", CUT_OF);
  let overlap = 0, drift = 0, start = null;
  new Function(act)();
  const end = performance.now() + 1200;
  while (performance.now() < end) {
    await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
    // A card's surface is also cut to its birth shape (a dot, a pill, the card): what is painted is inside both cuts.
    const own = (node) => {
      const surface = node.querySelector(":scope > .pf-card");
      const cut = surface && cutOf(surface);
      if (!cut) return null;
      const { box, t, r, b, l } = cut;
      return { left: box.left + l, right: box.right - r, top: box.top + t, bottom: box.bottom - b };
    };
    const seen = [...document.querySelectorAll("[data-glide]")].filter((node) => shown(node) > 0.05).map((node) => {
      const r = node.getBoundingClientRect(), [top, bottom] = inset(node), s = own(node);
      const box = { node, left: r.left, right: r.right, top: r.top + top, bottom: r.bottom - bottom };
      if (s) Object.assign(box, { left: Math.max(box.left, s.left), right: Math.min(box.right, s.right), top: Math.max(box.top, s.top), bottom: Math.min(box.bottom, s.bottom) });
      // A scrolling column shows only what is inside it.
      const side = node.closest(".pf-side")?.getBoundingClientRect();
      if (side) Object.assign(box, { top: Math.max(box.top, side.top), bottom: Math.min(box.bottom, side.bottom) });
      return box;
    }).filter((box) => box.right > box.left && box.bottom > box.top);
    for (let i = 0; i < seen.length; i += 1) for (let j = i + 1; j < seen.length; j += 1) {
      const a = seen[i], b = seen[j];
      if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0) overlap = Math.max(overlap, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
    }
    const card = follow && seen.find((item) => item.node.dataset.glide === follow);
    if (card) { const y = card.node.getBoundingClientRect().top; start ??= y; drift = Math.max(drift, Math.abs(y - start)); }
  }
  return { overlap, drift };
}, { act, follow, CUT_OF });

try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&many=1&theme=${theme}`);
    await page.locator('[data-glide="repo"]').waitFor();
    await settle(page);

    // A plan may grow after its birth was measured. Its late opening frames must fill the live box.
    await toggle(page, "plan").click();
    await settle(page);
    const resizedBirth = await page.evaluate(async ({ CUT_OF }) => {
      const cutOf = new Function("card", CUT_OF);
      document.querySelector('[data-branch="plan"]').click();
      await new Promise(requestAnimationFrame);
      const card = document.querySelector('[data-glide="plan"] > .pf-card');
      const shell = card.getAnimations().find(animation => animation.id === "pf-shell"
        && "clipPath" in animation.effect.getKeyframes()[0]);
      shell.pause();
      const { delay, duration } = shell.effect.getTiming();
      shell.currentTime = delay + duration * 0.5;
      await new Promise(requestAnimationFrame);
      card.style.width = card.offsetWidth * 1.5 + "px";
      card.style.minHeight = card.offsetHeight * 1.5 + "px";
      shell.currentTime = delay + duration * 0.94;
      await new Promise(requestAnimationFrame);
      const cut = cutOf(card);
      const result = { width: cut.box.width, height: cut.box.height,
        visibleWidth: cut.box.width - Math.max(0, cut.l) - Math.max(0, cut.r),
        visibleHeight: cut.box.height - Math.max(0, cut.t) - Math.max(0, cut.b) };
      card.style.width = "";
      card.style.minHeight = "";
      shell.finish();
      return result;
    }, { CUT_OF });
    assert.ok(resizedBirth.visibleWidth > resizedBirth.width * 0.98 && resizedBirth.visibleHeight > resizedBirth.height * 0.98,
      `plan fills its resized box before the animation ends: ${JSON.stringify(resizedBirth)}`);
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

    // A card with a surface is born at its cable's tip: a dot on the plug, a pill along the card, then the card.
    for (const id of ["scope", "plan"]) {
      await toggle(page, id).click();
      await settle(page);
      const shapes = await page.evaluate(async ({ id, CUT_OF }) => {
        const cutOf = new Function("card", CUT_OF);
        document.querySelector(`[data-branch="${id}"]`).click();
        const seen = [];
        for (const end = performance.now() + 1200; performance.now() < end;) {
          await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
          const card = document.querySelector(`[data-glide="${id}"] > .pf-card`);
          const cut = cutOf(card);
          const box = card.getBoundingClientRect();
          // The last frame of a birth leaves room around the card for its shadow: that is the whole card.
          if (!cut || (cut.t <= 0 && cut.r <= 0 && cut.b <= 0 && cut.l <= 0)) { seen.push({ w: box.width, h: box.height, full: true, t: document.timeline.currentTime }); continue; }
          const { t, r, b, l } = cut;
          seen.push({ w: box.width - l - r, h: box.height - t - b, W: box.width, H: box.height, opacity: Number(getComputedStyle(card).opacity), t: document.timeline.currentTime });
        }
        return seen;
      }, { id, CUT_OF });
      const visible = shapes.filter((shape) => shape.w > 0.5 && shape.h > 0.5);
      const first = visible[0];
      assert.ok(first && !first.full && first.w <= 13 && first.h <= 13, `${id}: born as a dot: ${JSON.stringify(first)}`);
      assert.ok(visible.some((shape) => !shape.full && shape.w > shape.W * 0.6 && shape.h < shape.H * 0.4), `${id}: stretches into a pill before it opens`);
      assert.ok(visible.every((shape) => shape.full || shape.opacity === 1), `${id}: the surface never fades, it changes shape`);
      assert.ok(shapes.at(-1).full, `${id}: ends as the whole card`);
      // One movement: it never stands still on the way, and it doesn't reach most of its height early and crawl the rest,
      // which reads as a card that stops halfway.
      const opening = visible.filter((shape) => !shape.full);
      const whole = visible.find((shape) => shape.full);
      let still = 0;
      for (let at = 1, since = opening[0].t; at < opening.length; at += 1) {
        if (opening[at].h - opening[at - 1].h > 0.05 || opening[at].w - opening[at - 1].w > 0.05) since = opening[at].t;
        else if (opening[at].h < opening[at].H * 0.95) still = Math.max(still, opening[at].t - since);
      }
      assert.ok(still < 30, `${id}: the shape stood still for ${still} ms`);
      const most = opening.find((shape) => shape.h >= shape.H * 0.85);
      const tail = (whole.t - most.t) / (whole.t - opening[0].t);
      assert.ok(tail < 0.3, `${id}: the last 15% of the height took ${(tail * 100).toFixed(0)}% of the opening`);
      await settle(page);
    }
    // Contents only fade. A blur redrawn every frame over a whole card halved the frame rate as the shape opened.
    const blurred = await page.evaluate(async () => {
      const found = [];
      const look = () => {
        for (const running of document.querySelector(".pf-stage").getAnimations({ subtree: true })) {
          if (running.effect?.getKeyframes().some((frame) => frame.filter && frame.filter !== "none")) found.push(running.id);
        }
      };
      document.querySelector('[data-branch="scope"]').click();
      await new Promise(requestAnimationFrame);
      look();
      await new Promise((resolve) => setTimeout(resolve, 1300));
      document.querySelector('[data-branch="scope"]').click();
      await new Promise(requestAnimationFrame);
      look();
      await new Promise((resolve) => setTimeout(resolve, 1300));
      return found;
    });
    assert.deepEqual(blurred, [], "no blur is animated as a card opens or closes");
    // A card without a surface of its own (the timeline) hangs from the top of its room: its head is in sight from
    // the first frames, the rows unroll under it.
    for (const id of ["loom"]) {
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
    // Closing in waits for the leaving card to have mostly gone (EXIT_HOLD), then ripples from the nearest card.
    assert.ok(closing.files >= 150 && closing.knowledge > closing.files, `closing in, after the exit, the nearest card leads: ${JSON.stringify(closing)}`);
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
      for (const end = performance.now() + 900; performance.now() < end;) {
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

    // Every branch opens on its own, and none closes another: the model and the quota open beside the history, each
    // unrolling without being painted over a neighbour, and all three stay open whole, however little room is left.
    await toggle(page, "history").click();
    await settle(page);
    for (const [name, chip] of [["model", ".pf-model-chip"], ["quota", ".pf-usage"]]) {
      const opening = await watch(page, `document.querySelector('${chip}').click()`);
      assert.ok(opening.overlap <= 1, `opening the ${name} beside the others paints no card over another: ${JSON.stringify(opening)}`);
      await settle(page);
    }
    const allOpen = async () => [await toggle(page, "history").getAttribute("aria-pressed"),
      await page.locator('.pf-model-chip').getAttribute("aria-expanded"), await page.locator('.pf-usage').getAttribute("aria-expanded")];
    assert.deepEqual(await allOpen(), ["true", "true", "true"], "no branch closed another");
    const whole = async () => page.locator('.pf-right').evaluate((column) => ({
      scrolls: column.scrollHeight > column.clientHeight,
      list: column.querySelector('.pf-model-options').clientHeight, history: column.querySelector('.bh-list').clientHeight,
      cut: [...column.querySelectorAll(':scope > [data-glide] > .pf-card')].filter((card) => card.scrollHeight > card.clientHeight + 1).map((card) => card.parentElement.dataset.glide) }));
    for (const height of [1000, 700]) {
      await page.setViewportSize({ width: 1500, height });
      await settle(page);
      const fit = await whole();
      assert.ok(fit.scrolls && fit.list >= 94 && fit.history >= 100 && !fit.cut.length, `at ${height}px the column scrolls and squeezes no card: ${JSON.stringify(fit)}`);
    }
    // The column scrolls by the wheel, also over a list at its end, and its cables stay on their cards on the frame it moves.
    const lag = await page.evaluate(async () => {
      const column = document.querySelector('.pf-right'), stage = document.querySelector('.pf-stage').getBoundingClientRect();
      const view = column.getBoundingClientRect();
      let worst = 0;
      for (let i = 0; i < 8; i += 1) {
        column.scrollTop += i < 4 ? 50 : -50;
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        for (const cable of column.closest('.pf-stage').querySelectorAll(".pf-cable[data-side='right']")) {
          const card = document.querySelector(`[data-glide="${CSS.escape(cable.dataset.cable)}"]`);
          if (!card) continue;
          const r = (card.querySelector('.pf-card') || card).getBoundingClientRect();
          const want = Math.max(view.top + 16, Math.min(view.bottom - 16, Math.min(r.top + 34, r.top + r.height / 2))) - stage.top;
          worst = Math.max(worst, Math.abs(cable.querySelector('.pf-cable-body').getPointAtLength(0).y - want));
        }
      }
      column.scrollTop = column.scrollHeight;
      return worst;
    });
    assert.ok(lag < 1, `cables follow a scrolled column: ${lag}`);
    await settle(page);
    assert.equal(await page.locator('.pf-right').evaluate((column) => column.hasAttribute('data-more-up') && !column.hasAttribute('data-more-down')), true, "the edge it continues past is faded");
    // Scrolled away, a card just opened comes into sight.
    await toggle(page, "history").click();
    await settle(page);
    await page.locator('.pf-right').evaluate((column) => { column.scrollTop = column.scrollHeight; });
    await page.waitForTimeout(100);
    await toggle(page, "history").click();
    await settle(page);
    const sight = await page.locator('.pf-right').evaluate((column) => {
      const view = column.getBoundingClientRect(), card = column.querySelector('[data-glide="history"]').getBoundingClientRect();
      return card.top >= view.top && card.top < view.bottom - 40;
    });
    assert.equal(sight, true, "a card opened above the scrolled part is brought into sight");
    // Each closes alone too, and takes no other with it.
    await page.locator('.pf-usage').click();
    await settle(page);
    assert.deepEqual(await allOpen(), ["true", "true", "false"]);
    await page.locator('.pf-model-chip').click();
    await settle(page);
    assert.deepEqual(await allOpen(), ["true", "false", "false"]);
    await toggle(page, "history").click();
    await settle(page);
    await page.setViewportSize({ width: 1500, height: 1000 });
    await settle(page);

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
      for (const end = performance.now() + 1100; performance.now() < end;) {
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

    // One thing after the other, and a cable towed by its card. Traced through the model card opening and closing, the
    // hardest case (the agents shrink on a size transition as they make room, and grow back as they close it).
    const trace = (act) => page.evaluate(async (act) => {
      const stage = document.querySelector(".pf-stage");
      const read = () => {
        const cables = {};
        for (const g of document.querySelectorAll(".pf-cable[data-side='right']")) {
          const body = g.querySelector(".pf-cable-body");
          const n = body.getAttribute("d").match(/-?[\d.]+/g).map(Number);
          cables[g.dataset.cable] = { turn: n[4], plug: n.at(-1), offset: parseFloat(getComputedStyle(body).strokeDashoffset) };
        }
        const cards = {};
        for (const slot of document.querySelectorAll("[data-glide^='agent:'], [data-glide='model']")) cards[slot.dataset.glide] = slot.getBoundingClientRect().top;
        const model = document.querySelector("[data-glide='model'] > .pf-card");
        const cut = model ? getComputedStyle(model).clipPath : "";
        return { t: document.timeline.currentTime, cables, cards, born: Boolean(model) && !cut.startsWith("inset(100%") && model.getAnimations().some((a) => a.id === "pf-shell" && a.currentTime > a.effect.getTiming().delay),
                 beads: stage.querySelector(".pf-cables").hasAttribute("data-moving") };
      };
      const frames = [];
      const zero = document.timeline.currentTime;
      new Function(act)();
      for (const end = performance.now() + 1200; performance.now() < end;) {
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        frames.push(read());
      }
      return frames.map((frame) => ({ ...frame, t: frame.t - zero }));
    }, act);
    const moves = (frames, get) => {
      const at = [];
      for (let i = 1; i < frames.length; i += 1) {
        const a = get(frames[i - 1]), b = get(frames[i]);
        if (a != null && b != null && Math.abs(b - a) > 0.4) at.push({ t: frames[i].t, by: Math.abs(b - a) / Math.max(1, (frames[i].t - frames[i - 1].t) / 16.67) });
      }
      return at;
    };
    for (const [name, kind] of [["model open", "open"], ["model close", "close"]]) {
      const frames = await trace("document.querySelector('.pf-model-chip').click()");
      const agents = Object.keys(frames[0].cards).filter((id) => id.startsWith("agent:"));
      const started = Math.min(...agents.map((id) => moves(frames, (f) => f.cards[id])[0]?.t ?? Infinity));
      assert.ok(Number.isFinite(started), `${name}: the agents move`);
      for (const id of agents) {
        const turn = moves(frames, (f) => f.cables[id]?.turn), plug = moves(frames, (f) => f.cables[id]?.plug);
        const card = moves(frames, (f) => f.cards[id]);
        // The cable moves with its card: not before it (a turn that set off 180 ms ahead read as a jump), not in one go.
        if (turn[0]) assert.ok(turn[0].t >= (card[0]?.t ?? started) - 34, `${name}: ${id}'s turn set off at ${turn[0].t}, its card at ${card[0]?.t}`);
        if (plug[0]) assert.ok(plug[0].t >= (card[0]?.t ?? started) - 34, `${name}: ${id}'s plug set off at ${plug[0].t}, its card at ${card[0]?.t}`);
        assert.ok(Math.max(0, ...turn.map((step) => step.by)) < 9, `${name}: ${id}'s turn jumped ${JSON.stringify(turn)}`);
        assert.ok(Math.max(0, ...plug.map((step) => step.by)) < 14, `${name}: ${id}'s plug jumped ${JSON.stringify(plug)}`);
      }
      if (kind === "open") {
        // Room first: the cable waits for the agents to be well on their way, the card for the cable. The beads leave the wire meanwhile.
        const grows = frames.find((f) => f.cables.model && f.cables.model.offset > -99);
        const born = frames.find((f) => f.born);
        assert.ok(grows && grows.t >= started + 150, `the model's cable grew at ${grows?.t}, the agents set off at ${started}`);
        assert.ok(born && born.t >= grows.t + 120, `the model card was born at ${born?.t}, its cable grew at ${grows?.t}`);
        // ...but takes over from it: born while the cable is still on its way, not after it has crawled to a stop at the
        // card (a cable that spent its last fifth crawling while a dot waited at its end read as a card stopping halfway).
        const most = frames.find((f) => f.t > grows.t && f.cables.model && f.cables.model.offset > -20);
        assert.ok(most && born.t - most.t < 40, `the model card was born at ${born.t}, ${born.t - most?.t} ms after its cable was 80% there`);
        assert.ok(frames.some((f) => f.t < grows.t && f.beads), "the beads are off the wire while the hub makes room");
      } else {
        // The dot goes first, then the room closes: the agents wait for the exit.
        assert.ok(started >= 200, `the agents started closing the room at ${started}`);
      }
      assert.equal(frames.at(-1).beads, false, `${name}: the beads are back when the hub is still`);
      await settle(page);
    }
    // A cable that stays stays: closing a card next to it does not make it grow again (React moved its node to match the
    // order cables are dealt in, and a node put back in the page starts over from `@starting-style`).
    await toggle(page, "history").click();
    await settle(page);
    await page.locator(".pf-model-chip").click();
    await settle(page);
    const stayed = await page.evaluate(async () => {
      const bodies = new Map([...document.querySelectorAll(".pf-cable")].filter((g) => g.dataset.cable !== "model").map((g) => [g.dataset.cable, g.querySelector(".pf-cable-body")]));
      const hidden = new Set();
      document.querySelector(".pf-model-chip").click();
      for (const end = performance.now() + 1200; performance.now() < end;) {
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        for (const [id, body] of bodies) {
          if (!body.isConnected || parseFloat(getComputedStyle(body).strokeDashoffset) < -1) hidden.add(id);
        }
      }
      return [...hidden];
    });
    assert.deepEqual(stayed, [], "closing the model card leaves the other cables where they are");
    await toggle(page, "history").click();
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

  // In a live run, what mounts on a cable (beads as an agent starts, a pulse as it finishes a step) is drawn on the cable as
  // it is now, not as it was when the list of cables last changed, however the hub has moved since.
  {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
    await page.goto(`${origin}/scripts/fixtures/project-flow.html?workers=3&theme=dark`);
    await page.locator('[data-glide="hub"]').waitFor();
    await page.waitForTimeout(1200);
    const off = await page.evaluate(async () => {
      const chip = document.querySelector(".pf-model-chip");
      let worst = null, mounted = 0;
      const known = new WeakSet();
      for (const end = performance.now() + 14000; performance.now() < end;) {
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        if (chip.getAttribute("aria-expanded") !== "true") chip.click();
        for (const path of document.querySelectorAll(".pf-flow, .pf-pulse")) {
          const body = document.querySelector(`.pf-cable-body[data-of="${CSS.escape(path.dataset.of)}"]`);
          if (!known.has(path)) { known.add(path); mounted += 1; }
          if (body && path.getAttribute("d") !== body.getAttribute("d")) worst ??= { of: path.dataset.of, path: path.getAttribute("d"), body: body.getAttribute("d") };
        }
      }
      return { worst, mounted };
    });
    assert.ok(off.mounted >= 2, `beads or pulses mounted during the run: ${JSON.stringify(off)}`);
    assert.equal(off.worst, null, `a path mounted off its cable: ${JSON.stringify(off.worst)}`);
    await page.close();
  }

  // As a build starts, the draft's plan card leaves where it stood and the run's arrives higher up, as the hub above
  // them shrinks. The newcomer is born in sight, from its dot: it once cut itself against the leaving card held below
  // it, stayed hidden through its birth and came into sight nine tenths born.
  {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&draft=1&proposal=1&theme=dark`);
    await page.locator('[data-glide="plan-draft"]').waitFor();
    await page.waitForTimeout(1200);
    const seen = await page.evaluate(async ({ CUT_OF }) => {
      const cutOf = new Function("card", CUT_OF);
      const insets = node => {
        const cut = cutOf(node);
        return cut ? [cut.t, cut.r, cut.b, cut.l].map(value => Math.max(0, value)) : [0, 0, 0, 0];
      };
      const shown = (node) => { const box = node.getBoundingClientRect(), [t, r, b, l] = insets(node); return { x0: box.left + l, y0: box.top + t, x1: box.right - r, y1: box.bottom - b }; };
      const frames = [];
      window.projectControlsFixture.activate();
      for (const end = performance.now() + 2400; performance.now() < end;) {
        await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
        const slot = document.querySelector('[data-glide="plan"]:not([data-leaving])'), card = slot?.querySelector(":scope > .pf-card");
        if (!card) continue;
        const a = shown(card), b = shown(slot);
        frames.push(Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0)));
      }
      return frames;
    }, { CUT_OF });
    const whole = seen.at(-1);
    const pops = seen.filter((area, index) => index && seen[index - 1] < whole * 0.05 && area > whole * 0.3);
    assert.ok(whole > 10000 && pops.length === 0, `the run's plan card is born in sight: ${JSON.stringify({ whole, pops, seen: seen.map(Math.round) })}`);
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
  // An ancestor's in-flight transform can change a sampled bound by a floating
  // point fraction (319.99997 vs 320); the allocated room must remain pixel-stable.
  assert.ok(Math.abs(middle - full) < 0.5 && thread > 0, `workspace disclosure takes its room at once: ${middle}, ${full}`);
  await button.click();
  await settle(page);
  assert.equal(await page.locator('.pf-disclosure').evaluate(node => node.inert && node.getBoundingClientRect().height < 1), true);
  await page.close();
  console.log("Project motion: no card painted over a neighbour, exit anchoring, reversal, agent compression, simultaneous folds, short windows, reduced motion and workspace disclosure passed in both themes.");
} finally { await browser.close(); }
