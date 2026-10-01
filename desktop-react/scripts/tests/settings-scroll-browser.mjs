import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/settings-scroll.html`);
  const nav = page.locator(".settings-nav");
  await page.waitForSelector("nav button");
  for (const zoom of [1, 1.25, 1.5]) {
    await page.evaluate(value => { document.documentElement.style.zoom = String(value); }, zoom);
    await nav.evaluate(el => { el.scrollTop = 0; });
    await page.mouse.move(950, 10);
    const baseline = await nav.evaluate(el => ({ width: el.clientWidth, height: el.clientHeight, extent: el.scrollWidth }));
    await page.locator("nav button").nth(2).hover();
    await page.waitForTimeout(250);
    const hovered = await nav.evaluate(el => ({ width: el.clientWidth, height: el.clientHeight, extent: el.scrollWidth }));
    assert.deepEqual(hovered, baseline, `hover must not change scroll geometry at zoom ${zoom}`);
    const samples = [];
    for (let i = 0; i < 8; i++) {
      await page.mouse.wheel(0, 80);
      await page.waitForTimeout(80);
      samples.push(await nav.evaluate(el => ({ top: el.scrollTop, left: el.scrollLeft, width: el.clientWidth, height: el.clientHeight })));
    }
    assert.ok(samples[0].top > 0, "wheel must scroll the sidebar");
    for (let i = 0; i < samples.length; i++) {
      assert.equal(samples[i].left, 0, "vertical scrolling must never drift sideways");
      assert.equal(samples[i].width, baseline.width);
      assert.equal(samples[i].height, baseline.height);
      if (i) assert.ok(samples[i].top >= samples[i - 1].top, "downward wheel must not bounce upward");
    }
    const position = samples.at(-1).top;
    await page.waitForTimeout(300);
    assert.equal(await nav.evaluate(el => el.scrollTop), position, "hover settling must not move the viewport");
    await page.mouse.wheel(0, -160);
    await page.waitForTimeout(150);
    assert.ok(await nav.evaluate(el => el.scrollTop) < position, "upward wheel must still work");
  }
  console.log("Settings sidebar hover and wheel geometry passed at 100%, 125%, and 150% zoom.");
} finally {
  await browser.close();
}
