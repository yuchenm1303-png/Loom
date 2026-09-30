import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1000, height: 750 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/window-chrome.html`);
  await page.locator(".loom-titlebar").waitFor();
  await page.waitForFunction(() => document.querySelectorAll(".loom-scroll-track:not([hidden])").length === 2);
  for (const selector of [".loom-titlebar-drag", ".loom-titlebar button", ".loom-scroll-thumb"]) {
    assert.match(await page.locator(selector).first().evaluate((el) => getComputedStyle(el).cursor), /yukino-mouse.*0 0/);
  }
  await page.getByRole("button", { name: "Minimize window" }).click();
  await page.getByRole("button", { name: "Maximize window" }).click();
  await page.getByRole("button", { name: "Restore window" }).click();
  await page.locator(".loom-titlebar-drag").dblclick();
  await page.getByRole("button", { name: "Restore window" }).click();
  const title = await page.locator(".loom-titlebar-drag").boundingBox();
  await page.mouse.move(title.x + 80, title.y + 15); await page.mouse.down();
  await page.waitForTimeout(30);
  await page.mouse.move(title.x + 140, title.y + 35, { steps: 4 }); await page.mouse.up();
  await page.waitForFunction(() => window.windowActions.some((entry) => entry.action === "move"));
  await page.getByRole("button", { name: "Close window" }).click();
  const actions = await page.evaluate(() => window.windowActions.map((entry) => entry.action));
  assert.ok(actions.includes("minimize") && actions.includes("maximize") && actions.includes("close"));
  const thumb = page.locator(".loom-scroll-y .loom-scroll-thumb");
  const box = await thumb.boundingBox();
  await page.mouse.move(box.x + 4, box.y + 10); await page.mouse.down();
  await page.mouse.move(box.x + 4, box.y + 100, { steps: 6 }); await page.mouse.up();
  assert.ok(await page.locator(".test-scroll").evaluate((el) => el.scrollTop) > 300);
  await thumb.focus(); await page.keyboard.press("Home");
  assert.equal(await page.locator(".test-scroll").evaluate((el) => el.scrollTop), 0);
  await thumb.focus(); await page.keyboard.press("End");
  assert.equal(await page.locator(".test-scroll").evaluate((el) => el.scrollTop), 1400);
  await page.locator(".loom-scroll-x .loom-scroll-thumb").focus(); await page.keyboard.press("End");
  assert.equal(await page.locator(".test-scroll").evaluate((el) => el.scrollLeft), 900);
  assert.equal(await page.locator(".test-scroll").evaluate((el) => getComputedStyle(el).scrollbarWidth), "none");
  await page.locator(".test-scroll").evaluate((el) => el.remove());
  await page.waitForFunction(() => !document.querySelector(".loom-scroll-track"));
  assert.deepEqual(errors, []);
  console.log("PASS: titlebar controls/drag, cursor, vertical/horizontal scrollbar drag/keyboard and unmount cleanup");
} finally { await browser.close(); }
