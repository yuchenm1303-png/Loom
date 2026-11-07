import assert from "node:assert/strict";
import fs from "node:fs";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
const out = process.argv[2] || "../.cache/mobile-branches";
fs.mkdirSync(out, { recursive: true });
try {
  for (const width of [320, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 844 }, hasTouch: true, isMobile: true });
    const errors = []; page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("http://127.0.0.1:5199/scripts/fixtures/mobile.html");
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "建造", exact: true }).click();
    await page.getByText("Loom 账号页重做", { exact: true }).click();
    const ports = page.getByRole("navigation", { name: "建造支路" }); await ports.waitFor();
    await page.getByRole("button", { name: "仅允许这次", exact: true }).waitFor();
    assert.equal(await page.locator(".conversation-stage, .m-stage.is-blank").isVisible(), true);
    for (const name of ["项目", "资料", "任务", "Agent", "预算"]) {
      await ports.getByRole("button", { name, exact: true }).click();
      assert.equal(await ports.locator('[aria-expanded="true"]').count(), 1);
      assert.equal(await page.locator(".conversation-stage, .m-stage.is-blank").isVisible(), false);
      assert.equal(await page.getByRole("textbox", { name: "插话", exact: true }).isVisible(), true);
    }
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("loom:mobile-back", { cancelable: true })));
    await page.locator(".conversation-stage, .m-stage.is-blank").waitFor({ state: "visible" });
    assert.equal(await ports.locator('[aria-expanded="true"]').count(), 0);
    await page.getByRole("button", { name: "仅允许这次", exact: true }).click();
    await page.getByRole("button", { name: "仅允许这次", exact: true }).waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: `${out}/hub-${width}.png`, animations: "disabled" });
    await ports.getByRole("button", { name: "预算", exact: true }).click();
    await page.screenshot({ path: `${out}/budget-${width}.png`, animations: "disabled" });
    assert.deepEqual(errors, []); await page.close();
  }
  console.log("Build hub: conversation default, five exclusive branches, persistent composer, urgent approvals, Back and narrow layouts passed");
} finally { await browser.close(); }
