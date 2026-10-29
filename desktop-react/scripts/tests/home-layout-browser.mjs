import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  for (const [width, height, language] of [[1280, 640, "en"], [1280, 720, "en"], [1366, 768, "zh-CN"], [1440, 900, "en"], [1920, 1080, "zh-CN"], [2557, 1420, "en"]]) {
    const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce", colorScheme: "dark" });
    await page.addInitScript(lang => localStorage.setItem("loom.settings.language", lang), language);
    await page.goto(origin);
    const demo = page.locator(".home-demo");
    await demo.waitFor();
    await page.waitForFunction(() => document.querySelector(".editorial-shell")?.dataset.typeReady === "true");
    const firstHeight = (await demo.boundingBox()).height;
    if (width >= 1920) {
      const grid = await page.locator(".editorial-hero-grid").boundingBox();
      assert.ok(grid.width >= Math.min(width * .83, 1760) - 1, "large displays use an expanded page frame");
      const text = await page.locator(".editorial-hero-text").boundingBox();
      const picture = await page.locator(".editorial-session").boundingBox();
      assert.ok(picture.x - (text.x + text.width) >= 80, "the two hero columns have a clear breathing space");
    }
    for (let step = 0; step < 4; step++) {
      await demo.locator(".home-demo-playback button").nth(step).click();
      const rects = await page.evaluate(() => {
        const rect = selector => {
          const r = document.querySelector(selector).getBoundingClientRect();
          return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height };
        };
        return { session: rect(".editorial-session"), copy: rect(".editorial-hero-text"), demo: rect(".home-demo"), footer: rect(".home-demo-footer"), composer: rect(".home-demo-composer"), sidebar: rect(".home-demo-sidebar"), body: rect(".home-demo-workspace") };
      });
      assert.ok(rects.session.bottom <= height - 12, `${width}×${height} ${language}: full session fits (${rects.session.bottom})`);
      assert.ok(rects.copy.bottom <= height - 12, "headline, copy and primary actions fit");
      assert.ok(Math.abs(rects.demo.height - firstHeight) < 1, "steps do not resize the demo window");
      assert.ok(rects.composer.bottom < rects.footer.top, "composer stays above playback controls");
      assert.ok(rects.sidebar.bottom <= rects.body.bottom + 1, "sidebar stays inside the workspace");
      if (step === 3) {
        const approve = await demo.locator(".home-demo-approve").boundingBox();
        assert.ok(approve.y + approve.height <= rects.composer.top, "approval action is visible above the composer");
      }
    }
    if ((width === 1440 || width === 1920) && process.env.LOOM_DEMO_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_DEMO_SCREENSHOTS}/home-layout-${width}.png` });
    await page.locator("#loom-computer-use").evaluate(node => node.scrollIntoView({ block: "start" }));
    const computer = await page.locator(".computer-demo-window").boundingBox();
    assert.ok(computer.y >= 72 && computer.y + computer.height <= height - 8, `${width}×${height}: complete Computer Use window fits after section navigation`);
    for (const selector of [".editorial-session", "#loom-computer-use", "#loom-download", "#loom-start"]) {
      const box = await page.locator(selector).boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= width + 1, `${selector} fits the page width`);
    }
    await page.close();
  }
  console.log("Home layout: full hero and controls fit at six desktop sizes; stable demo height, Chinese copy, sidebar containment and complete Computer Use section passed.");
} finally { await browser.close(); }
