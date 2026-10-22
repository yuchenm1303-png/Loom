import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const theme of ["dark", "light"]) {
    for (const width of [1440, 760]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${origin}/scripts/fixtures/live-app.html?lang=en&theme=${theme}`);
      await page.locator(".codex-sidebar-topbar .sidebar-toggle").waitFor();
      for (const opening of [false, true]) {
        await page.evaluate(() => document.querySelector('.app-shell.sidebar-open > .sidebar [data-sidebar-control="toggle"], .app-shell.sidebar-closed .thread-header-leading [data-sidebar-control="toggle"]').click());
        await page.waitForFunction(() => document.getAnimations().some(a => a.effect?.pseudoElement === "::view-transition-group(loom-sidebar-compose)"));
        const result = await page.evaluate(async () => {
          const animations = document.getAnimations().filter(a => a.effect?.pseudoElement?.startsWith("::view-transition"));
          animations.forEach(a => a.pause());
          const groups = ["toggle", "compose"].map(id => {
            const pseudo = `::view-transition-group(loom-sidebar-${id})`;
            const animation = animations.find(a => a.effect.pseudoElement === pseudo);
            return { pseudo, frames: animation.effect.getKeyframes(), duration: animation.effect.getTiming().duration };
          });
          const samples = [];
          for (const time of [0, 98, 195, 292, 389]) {
            animations.forEach(a => { a.currentTime = time; });
            await new Promise(requestAnimationFrame);
            samples.push(groups.map(({ pseudo }) => {
              const style = getComputedStyle(document.documentElement, pseudo);
              const image = getComputedStyle(document.documentElement, pseudo.replace("group", "new"));
              const matrix = new DOMMatrixReadOnly(style.transform);
              return { x: matrix.m41, y: matrix.m42, opacity: image.opacity };
            }));
          }
          const panel = animations.find(a => a.effect.pseudoElement === "::view-transition-group(loom-sidebar)");
          const panelFrames = panel?.effect.getKeyframes();
          animations.forEach(a => a.finish());
          return { groups, samples, panelFrames };
        });
        for (const group of result.groups) assert.equal(group.duration, 390);
        for (const sample of result.samples) for (const control of sample) assert.equal(control.opacity, "1", "shared controls never fade out during handoff");
        const xs = result.samples.map(s => s[1].x);
        assert.ok(Math.abs(xs.at(-1) - xs[0]) > 30, "quill travels between toolbars");
        for (let i = 1; i < xs.length; i++) assert.ok(opening ? xs[i] >= xs[i - 1] - .1 : xs[i] <= xs[i - 1] + .1, "quill follows a continuous monotonic path");
        const toggleXs = result.samples.map(s => s[0].x);
        assert.ok(Math.max(...toggleXs) - Math.min(...toggleXs) < 1, "toggle stays under the resting pointer");
        assert.ok(!result.panelFrames || result.panelFrames.every(f => f.width === result.panelFrames[0].width), "card snapshot never collapses its controls into a zero-width track");
        await page.waitForFunction(() => !document.documentElement.dataset.loomLayoutTransition);
        assert.equal(await page.locator(`.app-shell.sidebar-${opening ? "open" : "closed"}`).count(), 1);
      }
      // Reverse an in-progress transition; the latest click owns the final state.
      await page.evaluate(async () => {
        document.querySelector('.sidebar [data-sidebar-control="toggle"]').click();
        await new Promise(resolve => setTimeout(resolve, 90));
        document.querySelector('.thread-header-leading [data-sidebar-control="toggle"]')?.click();
      });
      await page.waitForFunction(() => !document.documentElement.dataset.loomLayoutTransition && document.querySelector('.app-shell.sidebar-open'));
      await page.evaluate(() => {
        document.documentElement.dataset.loomReducedMotion = "true";
        document.querySelector('.sidebar [data-sidebar-control="toggle"]').click();
      });
      assert.equal(await page.locator('.app-shell.sidebar-closed').count(), 1);
      assert.equal(await page.evaluate(() => document.getAnimations().some(a => a.effect?.pseudoElement?.startsWith("::view-transition"))), false);
      // Without View Transitions, remounted buttons still get their FLIP path.
      await page.evaluate(() => {
        document.documentElement.dataset.loomReducedMotion = "false";
        document.startViewTransition = undefined;
        document.querySelector('.thread-header-leading [data-sidebar-control="toggle"]').click();
      });
      assert.equal(await page.locator('.sidebar [data-sidebar-control="compose"]').evaluate(el => el.getAnimations().some(a => a.id === "loom-layout-anchor")), true);
      await page.waitForFunction(() => !document.body.classList.contains("loom-panel-motion"));
    }
  }
  assert.deepEqual(errors, []);
  console.log("Sidebar toolbar motion passed: both themes, narrow/wide layouts, continuous opaque controls, stable toggle, rapid reversal, reduced motion and FLIP fallback.");
} finally {
  await browser.close();
}
