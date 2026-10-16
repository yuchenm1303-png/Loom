// Real desktop React surfaces: caption buttons must remain outside modal and
// settings scroll viewports, including short windows / enlarged UI layouts.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const openTool = async (page, id) => {
  await page.locator(`.loom-tool-button[data-tool-id="${id}"]`).click();
};
const checkDialog = async (page, selector) => {
  const dialog = page.locator(selector);
  await dialog.waitFor();
  const bounds = await page.evaluate(selector => {
    const box = document.querySelector(selector).getBoundingClientRect();
    const controls = document.querySelector(".loom-window-controls").getBoundingClientRect();
    return { top: box.top, bottom: box.bottom, captionBottom: controls.bottom, height: innerHeight };
  }, selector);
  assert.ok(bounds.top >= bounds.captionBottom + 8, `${selector} overlaps caption: ${JSON.stringify(bounds)}`);
  assert.ok(bounds.bottom <= bounds.height - 16, `${selector} overflows short window: ${JSON.stringify(bounds)}`);
};
try {
  for (const theme of ["dark", "light"]) {
    for (const viewport of [{ width: 1040, height: 500 }, { width: 1280, height: 820 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      const load = async () => {
        await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}`);
        await page.locator(".thread-header").waitFor();
      };
      await load();
      await openTool(page, "files");
      await checkDialog(page, ".files-workbench");
      await page.locator('.files-workbench button[title="切换最大化"]').click();
      await checkDialog(page, ".files-workbench.full-screen");

      await load();
      await openTool(page, "account");
      await checkDialog(page, ".loom-account-dialog");

      await load();
      await openTool(page, "settings");
      await page.locator(".settings-main-scroll").waitFor();
      const scrollBounds = await page.locator(".settings-main-scroll").evaluate(el => {
        el.scrollTop = el.scrollHeight;
        const r = el.getBoundingClientRect();
        const controls = document.querySelector(".loom-window-controls").getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, captionBottom: controls.bottom, height: innerHeight };
      });
      assert.ok(scrollBounds.top >= scrollBounds.captionBottom, "settings can scroll behind caption controls");
      assert.ok(scrollBounds.bottom <= scrollBounds.height, "settings scrollbar extends outside window");
      assert.deepEqual(errors, [], `${theme} safe areas caused runtime errors`);
      await page.close();
    }
  }
  console.log("PASS: desktop dialogs, fullscreen Files and scrolled settings clear window controls in both themes");
} finally { await browser.close(); }
