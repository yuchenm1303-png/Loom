import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/cursor.html`);
  for (const selector of [".stop", ".stop svg", ".stop rect", "input", "#portal button"]) {
    await page.locator(selector).hover();
    const cursor = await page.locator(selector).evaluate((el) => getComputedStyle(el).cursor);
    assert.match(cursor, /yukino-mouse.*0 0/);
  }
  const pseudo = await page.locator(".stop").evaluate((el) => getComputedStyle(el, "::after").cursor);
  assert.match(pseudo, /yukino-mouse.*0 0/);
  await page.locator(".stop").evaluate((el) => { el.disabled = true; });
  assert.match(await page.locator(".stop").evaluate((el) => getComputedStyle(el).cursor), /yukino-mouse.*0 0/);
  for (const [x, y] of [[990, 200], [999, 749], [500, 749], [500, 200]]) {
    await page.setViewportSize({ width: 1000, height: 750 });
    await page.mouse.move(x, y);
    assert.equal(await page.locator(".loom-custom-pointer-image").count(), 0, "pointer must not depend on a moving DOM image");
    assert.match(await page.locator("body").evaluate((el) => getComputedStyle(el).cursor), /yukino-mouse.*0 0/);
  }
  await page.evaluate(() => { document.documentElement.style.zoom = "1.3"; });
  await page.mouse.move(450, 300);
  assert.match(await page.locator("body").evaluate((el) => getComputedStyle(el).cursor), /yukino-mouse.*0 0/);
  await page.evaluate(() => { document.documentElement.style.removeProperty("zoom"); });
  await page.evaluate(() => {
    const end = performance.now() + 400;
    while (performance.now() < end) { /* simulate a heavy synchronous render */ }
  });
  assert.equal(await page.locator(".loom-custom-pointer-layer").count(), 0);
  await page.evaluate(() => {
    window.clicks = 0;
    document.querySelector("#portal button").addEventListener("click", () => window.clicks++);
  });
  await page.locator("#portal button").click();
  assert.equal(await page.evaluate(() => window.clicks), 1, "ripple must not intercept the button click");
  assert.equal(await page.locator(".loom-pointer-click-ripple").count(), 1);
  assert.equal(await page.locator(".loom-pointer-click-layer").evaluate((el) => getComputedStyle(el).pointerEvents), "none");
  await page.waitForFunction(() => !document.querySelector(".loom-pointer-click-ripple"));
  await page.evaluate(() => {
    for (let index = 0; index < 30; index++) document.dispatchEvent(new PointerEvent("pointerdown", {
      button: 0, isPrimary: true, pointerType: "mouse", clientX: 100, clientY: 100,
    }));
  });
  assert.ok(await page.locator(".loom-pointer-click-ripple").count() <= 8);
  await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
  await page.waitForFunction(() => !document.querySelector(".loom-pointer-click-ripple"));
  await page.mouse.click(150, 150);
  assert.equal(await page.locator(".loom-pointer-click-ripple").count(), 0);
  await page.evaluate(() => { delete document.documentElement.dataset.loomReducedMotion; });
  await page.mouse.click(150, 150, { button: "right" });
  assert.equal(await page.locator(".loom-pointer-click-ripple").count(), 0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.mouse.click(150, 150);
  assert.equal(await page.locator(".loom-pointer-click-ripple").count(), 0);
  console.log("PASS: stop button, SVG children, hover, disabled, pseudo-element, input and portal cursor");
  console.log("PASS: click ripple, click passthrough, cleanup, bounded rapid clicks, right-click and reduced motion");
} finally { await browser.close(); }
