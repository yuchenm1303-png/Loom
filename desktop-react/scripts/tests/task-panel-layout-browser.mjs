import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/task-progress.html`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => Boolean(window.renderPlan));
  await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
  await page.locator(".task-plan-panel").waitFor();
  await page.locator('.task-plan-panel[data-motion-phase="entered"]').waitFor();

  async function assertPanelPlacement(label) {
    const bounds = await page.evaluate(() => {
      const panel = document.querySelector(".task-plan-panel").getBoundingClientRect();
      const rail = document.querySelector(".loom-tool-rail").getBoundingClientRect();
      return { panel: { left: panel.left, right: panel.right, top: panel.top, bottom: panel.bottom, width: panel.width },
        railLeft: rail.left, width: innerWidth, height: innerHeight };
    });
    assert.ok(bounds.panel.right <= bounds.railLeft + 1, `${label}: task panel covers the tool dock`);
    assert.ok(bounds.panel.left >= 0, `${label}: task panel starts outside the window`);
    assert.ok(bounds.panel.top >= 0, `${label}: task panel starts above the window`);
    assert.ok(bounds.panel.bottom <= bounds.height + 1, `${label}: task panel leaves the window at the bottom`);
  }

  await assertPanelPlacement("desktop");
  assert.equal(await page.locator(".pl-header .task-plan-chip").innerText(), "1/15");
  const headerY = (await page.locator(".task-plan-panel .pl-header").boundingBox()).y;
  const list = page.locator(".task-plan-list");
  assert.ok(await list.evaluate(el => el.scrollHeight > el.clientHeight), "long plans scroll inside the panel");
  await list.evaluate(el => el.scrollTop = el.scrollHeight);
  assert.equal((await page.locator(".task-plan-panel .pl-header").boundingBox()).y, headerY,
    "the header stays put while the step list scrolls");
  await list.evaluate(el => el.scrollTop = 0);

  await page.getByRole("button", { name: "关闭任务进度" }).click();
  await page.waitForFunction(() => !document.querySelector(".task-plan-panel"));
  const taskButton = page.locator('.loom-tool-dock [data-tool-id="tasks"]');
  assert.equal(await taskButton.getAttribute("aria-label"), "任务进度 · 1/15 已完成");
  await taskButton.click();
  await page.locator(".task-plan-panel").waitFor();
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.querySelector(".task-plan-panel"));

  await taskButton.click();
  await page.locator(".task-plan-panel").waitFor();
  await page.setViewportSize({ width: 480, height: 700 });
  await assertPanelPlacement("narrow");
  await page.evaluate(() => { document.documentElement.dataset.loomTheme = "light"; });
  await assertPanelPlacement("light");

  await page.evaluate(() => window.archiveTaskPlan());
  await page.waitForFunction(() => !document.querySelector(".task-plan-panel"));
  assert.equal(await taskButton.isDisabled(), true, "an archived plan leaves the dock tool disabled");
  assert.deepEqual(errors, []);
  console.log("Task panel: dock placement, independent scroll, close, escape, narrow layout and archive passed.");
} finally { await browser.close(); }
