// The sidebar's toolbar (toggle, search, the quill: SidebarControls) is one set of buttons that never leaves the
// window's corner. Opening and closing the sidebar must move them continuously between the card's top row and the
// header's start: the same elements throughout (nothing unmounted and remounted elsewhere), fully drawn at every
// moment, the toggle still under a resting pointer, a second click turning them around where they are, focus kept,
// and no View Transition. The pixels are checked too: an earlier version passed on computed styles while Electron drew
// the buttons fading out and popping back in.
import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

const TOGGLE = '.sidebar-controls [data-sidebar-control="toggle"]';
const SEARCH = '.sidebar-controls [data-sidebar-control="search"]';
const QUILL = '.sidebar-controls [data-sidebar-control="compose"]';

try {
  const context = await browser.newContext({ deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  /** The toolbar's buttons and the header copy where they are painted, with the trail's transition paused at `at` ms. */
  const sampleAt = (at) => page.evaluate((time) => {
    const trail = document.querySelector(".sidebar-controls-trail");
    for (const animation of trail.getAnimations()) { animation.pause(); animation.currentTime = time; }
    const box = (selector) => {
      const element = document.querySelector(selector);
      const rect = element.getBoundingClientRect();
      let opacity = 1;
      for (let node = element; node && node.nodeType === 1; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
      return { x: rect.left, y: rect.top, opacity, visible: getComputedStyle(element).visibility === "visible" };
    };
    return { toggle: box('.sidebar-controls [data-sidebar-control="toggle"]'), search: box('.sidebar-controls [data-sidebar-control="search"]'), quill: box('.sidebar-controls [data-sidebar-control="compose"]') };
  }, at);
  const release = () => page.evaluate(() => {
    for (const animation of document.querySelector(".sidebar-controls-trail").getAnimations()) animation.play();
  });
  /** Accent pixels where the quill is drawn: the purple of the glyph against the canvas or the card. */
  const quillInk = async () => {
    const rect = await page.locator(QUILL).boundingBox();
    const shot = await page.screenshot({ clip: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } });
    return page.evaluate(async (data) => {
      const image = new Image();
      image.src = `data:image/png;base64,${data}`;
      await image.decode();
      const canvas = Object.assign(document.createElement("canvas"), { width: image.width, height: image.height });
      const ctx = canvas.getContext("2d");
      ctx.drawImage(image, 0, 0);
      const { data: px } = ctx.getImageData(0, 0, image.width, image.height);
      let ink = 0;
      for (let i = 0; i < px.length; i += 4) {
        const [r, g, b] = [px[i], px[i + 1], px[i + 2]];
        if (b - g > 30 && b >= r) ink += 1;
      }
      return ink;
    }, shot.toString("base64"));
  };

  for (const theme of ["dark", "light"]) {
    for (const width of [1440, 760]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${origin}/scripts/fixtures/live-app.html?lang=en&theme=${theme}`);
      await page.locator(TOGGLE).waitFor();
      await page.waitForTimeout(600);
      // The same three elements for the whole test: nothing is unmounted and drawn again somewhere else.
      await page.evaluate(() => {
        for (const element of document.querySelectorAll(".sidebar-controls [data-sidebar-control]")) element.__kept = true;
      });
      const restInk = await quillInk();
      assert.ok(restInk > 12, `the quill is drawn at rest (${theme} ${width}: ${restInk} accent pixels)`);
      const sidebarWidth = await page.evaluate(() => document.querySelector(".workspace-panels > .sidebar").getBoundingClientRect().right);

      for (const opening of [false, true]) {
        const toggle = await page.locator(TOGGLE).boundingBox();
        await page.mouse.move(toggle.x + toggle.width / 2, toggle.y + toggle.height / 2);
        const composerBefore = await page.evaluate(() => { const r = document.querySelector(".composer").getBoundingClientRect(); return { left: r.left, right: r.right }; });
        await page.mouse.down();
        await page.mouse.up();
        assert.equal(await page.evaluate(() => typeof document.startViewTransition === "function" && document.documentElement.matches(":active-view-transition")), false, "no View Transition");
        // The composer starts from where it was. Closing, a column that fills a narrow workspace grows: it glides from
        // its old left edge (the width it gained comes out from under the rail) instead of jumping the sidebar's width;
        // opening, it keeps its right edge and its left part goes under the arriving card.
        const composerStart = await page.evaluate(() => {
          const composer = document.querySelector(".composer");
          const flip = composer.getAnimations().find((animation) => animation.id === "loom-layout-anchor");
          if (flip) { flip.pause(); flip.currentTime = 0; }
          const r = composer.getBoundingClientRect();
          flip?.play();
          return { left: r.left, right: r.right };
        });
        if (opening) assert.ok(Math.abs(composerStart.right - composerBefore.right) < 1.5, `opening, the composer keeps its right edge (${composerBefore.right} -> ${composerStart.right})`);
        else assert.ok(Math.abs(composerStart.left - composerBefore.left) < 1.5, `closing, the composer starts at its left edge (${composerBefore.left} -> ${composerStart.left})`);
        const samples = [];
        for (const at of [0, 60, 130, 200, 290, 389]) samples.push(await sampleAt(at));
        // Mid-way the quill is fully drawn, in pixels, not only in computed styles.
        await sampleAt(130);
        const midInk = await quillInk();
        assert.ok(midInk > restInk * .6, `the quill stays drawn while it travels (${midInk} of ${restInk} accent pixels)`);
        await release();
        for (const sample of samples) {
          for (const control of [sample.toggle, sample.search, sample.quill]) {
            assert.equal(control.opacity, 1, "every control stays fully opaque while it moves");
            assert.equal(control.visible, true);
          }
        }
        const toggleXs = samples.map((sample) => sample.toggle.x);
        assert.ok(Math.max(...toggleXs) - Math.min(...toggleXs) < .5, "the toggle stays under the resting pointer");
        const quillXs = samples.map((sample) => sample.quill.x);
        assert.ok(Math.abs(quillXs.at(-1) - quillXs[0]) > 60, `the quill travels between the card and the header (${quillXs.join(", ")})`);
        for (let i = 1; i < quillXs.length; i += 1) {
          assert.ok(opening ? quillXs[i] >= quillXs[i - 1] - .1 : quillXs[i] <= quillXs[i - 1] + .1, "on a monotonic path");
        }
        const searchXs = samples.map((sample) => sample.search.x);
        assert.ok(searchXs.every((x, i) => Math.abs(quillXs[i] - x - 32) < .5), "search travels with the quill");
        await page.waitForFunction(() => !document.body.classList.contains("loom-panel-motion")
          && document.querySelector(".sidebar-controls-trail").getAnimations().length === 0);
        const rest = await sampleAt(0);
        assert.equal(Math.round(rest.toggle.x), 16, "the toggle sits 16px from the window's edge");
        if (opening) {
          assert.equal(Math.round(rest.quill.x), Math.round(sidebarWidth - 38), "open, the quill ends the card's top row");
          assert.equal(await page.locator(".thread-header-leading").count(), 0);
        } else {
          assert.deepEqual([Math.round(rest.search.x), Math.round(rest.quill.x)], [48, 80], "closed, search and the quill sit beside the toggle");
          const spacer = await page.locator(".thread-header-leading").boundingBox();
          assert.deepEqual([Math.round(spacer.x), Math.round(spacer.width)], [16, 94], "the header keeps their room");
          const title = await page.locator(".thread-header-copy").boundingBox();
          assert.ok(title.x >= 16 + 94 + 6, "the title starts after them");
        }
        assert.equal(await page.locator(`.app-shell.sidebar-${opening ? "open" : "closed"}`).count(), 1);
      }
      assert.equal(await page.evaluate(() => [...document.querySelectorAll(".sidebar-controls [data-sidebar-control]")].every((element) => element.__kept)), true, "the same buttons in both places");

      // A second click mid-way turns the trail around from where it is painted: the new transition starts at the
      // point the first one had reached (both held still to read it), and heads back.
      await page.locator(TOGGLE).click();
      await page.waitForTimeout(90);
      const turn = await page.evaluate(() => {
        const trail = document.querySelector(".sidebar-controls-trail");
        const quill = () => document.querySelector('.sidebar-controls [data-sidebar-control="compose"]').getBoundingClientRect().left;
        for (const animation of trail.getAnimations()) animation.pause();
        const before = quill();
        document.querySelector('.sidebar-controls [data-sidebar-control="toggle"]').click();
        const reversed = trail.getAnimations();
        for (const animation of reversed) { animation.pause(); animation.currentTime = 0; }
        const after = quill();
        for (const animation of reversed) animation.currentTime = 60;
        const onward = quill();
        for (const animation of reversed) animation.play();
        return { before, after, onward };
      });
      assert.ok(Math.abs(turn.after - turn.before) < 1, `a reversal starts where the quill is (${turn.before} -> ${turn.after})`);
      assert.ok(turn.onward > turn.after + 2, `and heads back toward the card's end (${turn.after} -> ${turn.onward})`);
      await page.waitForFunction(() => !document.body.classList.contains("loom-panel-motion") && document.querySelector(".app-shell.sidebar-open"));

      // The keyboard stays on the toggle: there is only one.
      await page.locator(TOGGLE).focus();
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => document.querySelector(".app-shell.sidebar-closed"));
      assert.equal(await page.evaluate(() => document.activeElement?.matches('.sidebar-controls [data-sidebar-control="toggle"]') && document.activeElement.__kept === true), true, "focus stays on the toggle");
      assert.equal(await page.locator(TOGGLE).getAttribute("aria-label"), "Show sidebar");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => document.querySelector(".app-shell.sidebar-open") && !document.body.classList.contains("loom-panel-motion"));
      assert.equal(await page.locator(TOGGLE).getAttribute("aria-label"), "Hide sidebar");

      // Search with the sidebar closed opens the sidebar on its field.
      await page.locator(TOGGLE).click();
      await page.waitForFunction(() => document.querySelector(".app-shell.sidebar-closed") && !document.body.classList.contains("loom-panel-motion"));
      await page.locator(SEARCH).click();
      await page.waitForFunction(() => document.querySelector(".app-shell.sidebar-open") && document.activeElement?.matches(".compact-search-shell.open input"));
      assert.equal(await page.locator(SEARCH).evaluate((element) => element.classList.contains("active")), true);
      await page.locator(SEARCH).click();
      await page.waitForFunction(() => !document.querySelector(".compact-search-shell.open"));

      // A composer popover lifts the workspace over the docks, and the toolbar with it.
      const lifted = await page.evaluate(() => {
        const shell = document.querySelector(".app-shell");
        shell.setAttribute("data-composer-popover", "");
        const z = getComputedStyle(document.querySelector(".sidebar-controls")).zIndex;
        shell.removeAttribute("data-composer-popover");
        return z;
      });
      assert.equal(lifted, "1703");
    }
  }

  // Ctrl+K and Ctrl+N from a closed sidebar; the quill strokes for a new conversation from the keyboard.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${origin}/scripts/fixtures/live-app.html?lang=en&theme=dark`);
  await page.locator(TOGGLE).waitFor();
  await page.locator(TOGGLE).click();
  await page.waitForFunction(() => document.querySelector(".app-shell.sidebar-closed") && !document.body.classList.contains("loom-panel-motion"));
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true })));
  await page.waitForFunction(() => document.querySelector(".app-shell.sidebar-open") && document.activeElement?.matches(".compact-search-shell.open input"));
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "n", ctrlKey: true, bubbles: true })));
  await page.waitForFunction(() => document.querySelector('.sidebar-controls [data-sidebar-control="compose"]').dataset.stroke === "odd");
  assert.equal(await page.locator(`${QUILL} .li-scribe`).evaluate((element) => getComputedStyle(element).animationName), "li-stroke-odd");

  // Reduced motion: the toolbar is simply in its other place.
  await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
  await page.locator(TOGGLE).click();
  assert.equal(await page.locator(".app-shell.sidebar-closed").count(), 1);
  const reduced = await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve(document.querySelector('.sidebar-controls [data-sidebar-control="compose"]').getBoundingClientRect().left))));
  assert.equal(Math.round(reduced), 80, "with reduced motion the quill is beside the toggle on the next frame");
  assert.deepEqual(errors, []);
  console.log("Sidebar toolbar motion passed: one set of buttons, continuous and fully drawn in both directions (dark/light, 1440/760), the toggle still, a reversal from where they are, focus kept, search and the shortcuts from a closed sidebar, the popover lift and reduced motion.");
} finally {
  await browser.close();
}
