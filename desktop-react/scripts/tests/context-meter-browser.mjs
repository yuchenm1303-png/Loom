import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 750 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("loom.settings.language", "zh-CN"));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/context-meter.html`);
  await page.waitForFunction(() => Boolean(window.renderMeter));
  for (const theme of ["dark", "light"]) {
    await page.evaluate(theme => document.documentElement.setAttribute("data-loom-theme", theme), theme);
    await page.evaluate(() => window.renderMeter("unknown"));
    const chip = page.locator(".context-meter-chip");
    assert.match(await chip.innerText(), /378k tokens/);
    assert.equal(await chip.locator(".context-meter-track").count(), 0);
    assert.match(await chip.getAttribute("aria-label"), /容量未声明/);
    assert.ok(!await chip.evaluate(el => el.classList.contains("hot")));
    await chip.click();
    await page.locator(".context-meter-panel").waitFor({ state: "visible" });
    const panel = await page.locator(".context-meter-panel").innerText();
    assert.ok(!panel.includes("100%"));
    assert.ok(!panel.includes("272k"));
    assert.match(panel, /未声明/);
    await page.keyboard.press("Escape");
    await page.locator(".context-meter-panel").waitFor({ state: "hidden" });
    await page.evaluate(() => window.renderMeter("known"));
    assert.match(await chip.innerText(), /13%/);
    assert.equal(await chip.locator(".context-meter-track").count(), 1);
    await page.evaluate(() => window.renderMeter("compacting"));
    assert.match(await chip.innerText(), /生成摘要/);
    assert.equal(await chip.locator(".context-meter-compaction-track").count(), 1);
  }
  assert.deepEqual(errors, []);
  console.log("Context meter: unknown/known/compacting states passed in light and dark themes.");
} finally { await browser.close(); }
