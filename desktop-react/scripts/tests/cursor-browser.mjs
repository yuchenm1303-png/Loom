import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/cursor.html`);
  for (const selector of [".stop", ".stop svg", ".stop rect", "input", "#portal button"]) {
    await page.locator(selector).hover();
    const cursor = await page.locator(selector).evaluate((el) => getComputedStyle(el).cursor);
    assert.match(cursor, /yukino-mouse.*35 35/);
  }
  const pseudo = await page.locator(".stop").evaluate((el) => getComputedStyle(el, "::after").cursor);
  assert.match(pseudo, /yukino-mouse.*35 35/);
  await page.locator(".stop").evaluate((el) => { el.disabled = true; });
  assert.match(await page.locator(".stop").evaluate((el) => getComputedStyle(el).cursor), /yukino-mouse.*35 35/);
  assert.ok(await page.locator(".stop").evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return innerHeight - (rect.top + rect.height / 2) >= 35 && innerWidth - (rect.left + rect.width / 2) >= 35;
  }), "centred cursor fits at the bottom stop button");
  console.log("PASS: stop button, SVG children, hover, disabled, pseudo-element, input and portal cursor");
} finally { await browser.close(); }
