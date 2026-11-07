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
    assert.equal(await ports.getByRole("button").count(), 11);
    await ports.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
    assert.equal(await ports.locator('[aria-expanded="true"]').count(), 0, "scrolling must not select a branch");
    await page.getByRole("button", { name: "仅允许这次", exact: true }).waitFor();
    assert.equal(await page.locator(".conversation-stage, .m-stage.is-blank").isVisible(), true);
    for (const name of ["项目", "资料", "任务", "Agent", "运行", "模型", "额度", "范围", "知识", "历史", "交付"]) {
      await ports.getByRole("button", { name, exact: true }).click();
      assert.equal(await ports.locator('[aria-expanded="true"]').count(), 1);
      assert.equal(await page.locator(".conversation-stage, .m-stage.is-blank").isVisible(), false);
      assert.equal(await page.getByRole("textbox", { name: "插话", exact: true }).isVisible(), true);
      if (name === "额度") await page.getByText("每周 · 剩余 68%", { exact: true }).waitFor();
      if (name === "模型") {
        await page.getByRole("button", { name: "调整模型、推理与对话权限", exact: true }).click();
        await page.getByRole("dialog").waitFor();
        await page.getByRole("dialog").getByRole("button", { name: /^关闭/ }).click();
      }
    }
    await page.waitForFunction(() => {
      const nav = document.querySelector('[aria-label="建造支路"]');
      const selected = nav.querySelector('[aria-expanded="true"]');
      const pill = nav.querySelector('.m-branch-capsule');
      const a = selected.getBoundingClientRect(), b = pill.getBoundingClientRect(), rail = nav.getBoundingClientRect();
      return Math.abs(a.left - b.left) < 1 && Math.abs(a.width - b.width) < 1 && a.left >= rail.left && a.right <= rail.right + 1;
    });
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("loom:mobile-back", { cancelable: true })));
    await page.locator(".conversation-stage, .m-stage.is-blank").waitFor({ state: "visible" });
    assert.equal(await ports.locator('[aria-expanded="true"]').count(), 0);
    const first = ports.getByRole("button", { name: "项目", exact: true });
    await first.focus(); await first.press("End");
    assert.equal(await ports.getByRole("button", { name: "交付", exact: true }).evaluate((element) => element === document.activeElement), true);
    assert.equal(await ports.locator('[aria-expanded="true"]').count(), 0, "keyboard exploration does not select until activated");
    await ports.getByRole("button", { name: "交付", exact: true }).press("Home");
    await page.getByRole("button", { name: "仅允许这次", exact: true }).click();
    await page.getByRole("button", { name: "仅允许这次", exact: true }).waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: `${out}/hub-${width}.png`, animations: "disabled" });
    await ports.getByRole("button", { name: "运行", exact: true }).click();
    await page.screenshot({ path: `${out}/budget-${width}.png`, animations: "disabled" });
    assert.deepEqual(errors, []); await page.close();
  }
  console.log("Build rail: 11 exclusive branches, scrolling without selection, capsule alignment, model settings, quota data, persistent composer, approvals, Back and narrow layouts passed");
} finally { await browser.close(); }
