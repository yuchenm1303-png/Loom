// Real-canvas geometry and selection checks. Set LOOM_PLAYWRIGHT_MODULE and LOOM_CHROMIUM_PATH if needed.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const server = await (await import("vite")).createServer({ server: { port: 0 } });
await server.listen();
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  for (const theme of ["dark", "light"]) for (const width of [1440, 1000, 700]) {
    const page = await browser.newPage({ viewport: { width, height: 850 }, reducedMotion: "reduce" });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${server.resolvedUrls.local[0]}scripts/fixtures/workspace-branch.html?theme=${theme}`);
    const trigger = page.getByRole("button", { name: "Choose a project or folder" });
    await trigger.click();
    await page.locator(".pf-stage[data-workspace-expanded]").waitFor();
    const geometry = await page.evaluate(() => {
      const host = document.querySelector(".pf-workspace-branch-host");
      const panel = host.querySelector(".pf-workspace-branch").getBoundingClientRect();
      const repo = host.parentElement.querySelector(".pf-card").getBoundingClientRect();
      const list = host.querySelector(".pf-switch-menu");
      return { right: panel.right, left: repo.left, top: panel.top, bottom: panel.bottom,
        repoTop: repo.top, repoBottom: repo.bottom, scrolls: list.scrollHeight > list.clientHeight,
        position: getComputedStyle(list).position };
    });
    assert.ok(Math.abs(geometry.left - geometry.right - 36) < 2, JSON.stringify(geometry));
    assert.ok(geometry.top < geometry.repoBottom && geometry.bottom > geometry.repoTop);
    assert.ok(geometry.scrolls);
    assert.notEqual(geometry.position, "fixed");
    if (process.env.LOOM_TEST_SCREENSHOT && theme === "dark" && width === 1440) await page.screenshot({ path: process.env.LOOM_TEST_SCREENSHOT });
    await trigger.press("Home");
    await trigger.press("ArrowDown");
    await trigger.press("Enter");
    await page.locator('output[data-selected="1"]').waitFor({ state: "attached" });
    await trigger.press("ArrowDown");
    await page.locator(".pf-stage[data-workspace-expanded]").waitFor();
    await trigger.press("End");
    await trigger.press("Enter");
    await page.locator('output[data-selected="13"]').waitFor({ state: "attached" });
    await page.locator(".pf-workspace-branch").waitFor({ state: "detached" });
    await trigger.press("ArrowDown");
    await page.locator(".pf-workspace-branch").waitFor();
    await trigger.press("Escape");
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    await trigger.click();
    await page.getByRole("button", { name: "Close project branch" }).click();
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    await trigger.click();
    await page.getByRole("button", { name: "Toggle lock" }).click();
    await page.locator(".pf-workspace-branch").waitFor({ state: "detached" });
    assert.ok(await trigger.isDisabled());
    assert.deepEqual(errors, []);
    console.log(`PASS ${theme} ${width}px: connected child branch, scroll, keyboard, close, lock`);
    await page.close();
  }
} finally {
  await browser.close();
  await server.close();
}
