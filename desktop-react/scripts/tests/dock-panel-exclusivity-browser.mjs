import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/live-app.html?scenario=code&agents=1&changes=1`, { waitUntil: "domcontentloaded" });
  const dock = page.locator(".loom-tool-dock");
  await dock.waitFor();
  const click = async id => {
    await dock.locator(`[data-tool-id="${id}"]`).click();
    await page.waitForFunction(id => document.querySelector(`.loom-tool-button[data-tool-id="${id}"]`)?.getAttribute("aria-pressed") === "true", id);
    const active = await dock.locator('[aria-pressed="true"]').evaluateAll(nodes => nodes.map(node => node.dataset.toolId));
    assert.deepEqual(active, [id], `only ${id} may remain selected`);
  };
  for (const panel of ["review", "agents", "artifacts", "inspector"]) {
    await click(panel);
    await click("background");
    await page.locator(".background-task-panel").waitFor();
    await click(panel);
    await page.waitForFunction(() => !document.querySelector('.background-task-panel:not([inert])'));
  }
  await click("background");
  await dock.locator('[data-tool-id="background"]').click();
  await page.waitForFunction(() => document.querySelector('.loom-tool-button[data-tool-id="background"]')?.getAttribute('aria-pressed') === 'false');
  assert.deepEqual(errors, []);
  console.log("Background tasks and right panels are mutually exclusive in both directions.");
} finally {
  await browser.close();
}
