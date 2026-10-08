import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const directory = process.env.LOOM_SURFACE_SCREENSHOTS;
if (directory) await mkdir(directory, { recursive: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const capture = async name => {
    await page.waitForTimeout(320);
    assert.equal(await page.locator(".boot-error-card").count(), 0);
    if (directory) await page.screenshot({ path: `${directory}/${name}.png` });
  };
  for (const theme of ["dark", "light"]) {
    await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=${theme}`);
    await page.locator(".composer").waitFor();
    for (const control of ["permission", "model", "sticker"]) {
      await page.locator(`.${control}-chip`).click();
      await page.locator('.composer-popover[data-motion-phase="entered"]').waitFor();
      await capture(`${control}-${theme}`);
      await page.keyboard.press("Escape");
      await page.locator(".composer-popover").waitFor({ state: "detached" });
    }
    await page.getByRole("button", { name: /Loom 账号/ }).click();
    await page.locator('.loom-account-backdrop[data-motion-phase="entered"]').waitFor();
    assert.equal(await page.locator(".loom-account-dialog").evaluate(el => getComputedStyle(el).transform), "none");
    await capture(`account-${theme}`);
    await page.keyboard.press("Escape");
    await page.locator(".loom-account-backdrop").waitFor({ state: "detached" });
    await page.locator(".compact-thread-main").first().click({ button: "right", position: { x: 20, y: 16 } });
    await page.locator(".thread-context-menu").waitFor();
    await capture(`thread-menu-${theme}`);
    await page.keyboard.press("Escape");
    await page.locator(".thread-settings-button").click();
    await page.locator('.settings-host[data-motion-phase="entered"]').waitFor();
    await capture(`settings-general-${theme}`);
    for (const name of ["外观", "模型", "权限"]) {
      await page.locator(".settings-nav").getByRole("button", { name, exact: true }).click();
      await capture(`settings-${name}-${theme}`);
      const transforms = await page.locator(".settings-page-surface [data-flow-block]").evaluateAll(nodes => nodes.map(node => getComputedStyle(node).transform));
      assert.ok(transforms.every(value => value === "none"), "settings text blocks stay stationary");
    }
    await page.evaluate(() => document.documentElement.dataset.loomReducedMotion = "true");
    await page.locator(".settings-nav").getByRole("button", { name: "常规", exact: true }).click();
    assert.equal(await page.locator(".settings-main").evaluate(el => getComputedStyle(el).transform), "none");
    await page.getByRole("button", { name: "返回应用", exact: true }).click();
    await page.locator(".settings-host").waitFor({ state: "detached" });
  }
  assert.deepEqual(errors, []);
  console.log("Composer popovers, account dialog, sidebar menu and settings surfaces passed in both themes.");
} finally { await browser.close(); }
