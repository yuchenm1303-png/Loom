import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?browser=1&theme=${theme}&motion=${theme === "dark" ? "1" : "0"}`);
    await page.locator(".turn-final-answer").waitFor();
    const composer = page.locator(".composer textarea");
    await composer.fill("Keep this draft while browsing");
    const open = () => page.locator('[data-tool-id="browser"]').click();
    const dock = page.locator(".artifact-preview-dock:has(.browser-workspace)");
    await open();
    await dock.locator('.browser-viewport').waitFor();
    await page.waitForFunction(() => window.browserFixture.commands.some(c => c.action === "bounds" && c.bounds));
    assert.equal(await page.locator(".turn-final-answer").isVisible(), true);
    assert.equal(await composer.inputValue(), "Keep this draft while browsing");
    const checkBounds = async () => {
      const result = await page.evaluate(() => {
        const rect = selector => {
          const { x, y, width, height, right } = document.querySelector(selector).getBoundingClientRect();
          return { x, y, width, height, right };
        };
        return { workspace: rect(".workspace"), viewport: rect(".browser-viewport"),
          dock: rect(".artifact-preview-dock"), rail: rect(".loom-tool-rail"),
          native: window.browserFixture.commands.filter(c => c.action === "bounds").at(-1)?.bounds };
      });
      assert.ok(result.workspace.right <= result.dock.x + 1, JSON.stringify(result));
      assert.ok(result.dock.right <= result.rail.x + 1, JSON.stringify(result));
      assert.ok(result.viewport.width > 150 && result.viewport.height > 150, JSON.stringify(result));
      for (const dimension of ["x", "y", "width", "height"]) {
        assert.ok(Math.abs(result.native[dimension] - result.viewport[dimension]) < 2, JSON.stringify(result));
      }
    };
    await checkBounds();
    await page.screenshot({ path: `design/browser-sidebar-${theme}.local.png` });
    const separator = dock.locator('[role="separator"]');
    const before = Number(await separator.getAttribute("aria-valuenow"));
    await separator.focus();
    await page.keyboard.press("ArrowLeft");
    assert.ok(Number(await separator.getAttribute("aria-valuenow")) > before);
    await page.waitForTimeout(100);
    await checkBounds();
    const handle = await separator.boundingBox();
    await page.mouse.move(handle.x + handle.width / 2, handle.y + 200);
    await page.mouse.down();
    await page.mouse.move(handle.x - 45, handle.y + 200);
    await page.mouse.up();
    assert.ok(Number(await separator.getAttribute("aria-valuenow")) > before + 40);
    await page.waitForTimeout(100);
    await checkBounds();
    const address = dock.locator(".browser-address-form input");
    await address.fill("https://example.com");
    await address.press("Enter");
    await page.waitForFunction(() => window.browserFixture.commands.some(c => c.action === "navigate"));
    await dock.locator(".browser-add-tab").click();
    assert.equal(await dock.locator('[role="tab"]').count(), 2);
    await dock.locator('[role="tab"]').first().click();
    await page.waitForFunction(() => document.querySelector(".browser-address-form input").value === "https://example.com");
    assert.equal(await address.inputValue(), "https://example.com");
    await page.locator('[data-tool-id="settings"]').click();
    await page.waitForFunction(() => window.browserFixture.commands.filter(c => c.action === "bounds").at(-1)?.bounds === null);
    await page.locator(".settings-back").click();
    await page.waitForFunction(() => window.browserFixture.commands.filter(c => c.action === "bounds").at(-1)?.bounds !== null);
    await page.locator('[data-tool-id="inspector"]').click();
    await dock.waitFor({ state: "detached" });
    await open();
    await page.waitForFunction(() => window.browserFixture.commands.filter(c => c.action === "bounds").at(-1)?.bounds !== null);
    assert.equal(await page.locator(".app-shell.inspector-closed").count(), 1);
    await page.setViewportSize({ width: 980, height: 760 });
    await page.waitForTimeout(150);
    await checkBounds();
    await dock.locator(".pl-close").click();
    await dock.waitFor({ state: "detached" });
    assert.equal(await composer.inputValue(), "Keep this draft while browsing");
    assert.equal(await page.locator(".app-shell.with-artifact-preview").count(), 0);
    assert.equal(await page.evaluate(() => window.browserFixture.commands.filter(c => c.action === "bounds").at(-1).bounds), null);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("PASS: shared browser sidebar, preserved chat/draft, resize, tabs, native bounds, overlays and themes");
} finally {
  await browser.close();
}
