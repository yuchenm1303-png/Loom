import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/task-progress.html`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => Boolean(window.renderPlan));
  await page.evaluate(() => window.renderPlan(1200));
  await page.locator(".task-progress-dock.is-expanded").waitFor();
  async function noOverlap() {
    const text = await page.locator(".transcript-scroll").boundingBox();
    const card = await page.locator(".task-progress-dock").boundingBox();
    assert.ok(text.x + text.width <= card.x + 1);
    assert.ok(text.width >= Math.min(480, (await page.locator(".conversation-stage").boundingBox()).width - 56));
    assert.ok(card.x + card.width <= (await page.locator(".conversation-stage").boundingBox()).width + 1);
  }
  await noOverlap();
  const cardY = (await page.locator(".task-progress-dock").boundingBox()).y;
  await page.locator(".transcript-scroll").evaluate(el => el.scrollTop = 300);
  assert.equal((await page.locator(".task-progress-dock").boundingBox()).y, cardY);
  await page.getByRole("button", { name: "折叠任务进度" }).click();
  await page.locator(".task-progress-dock.is-rail").waitFor();
  await page.evaluate(() => window.renderPlan(1200, true));
  await page.getByRole("button", { name: "查看任务进度，2/15 已完成" }).click();
  await page.locator(".task-progress-dock.is-expanded").waitFor();
  // Same window; only the workspace narrows as an external inspector opens.
  await page.evaluate(() => window.renderPlan(800));
  await page.locator(".task-progress-dock.is-rail").waitFor();
  await noOverlap();
  await page.getByRole("button", { name: "查看任务进度，1/15 已完成" }).click();
  await page.getByRole("dialog").waitFor();
  await page.getByText("当前环境没有配置 CDP；记录未覆盖", { exact: true }).waitFor();
  await page.evaluate(() => window.renderPlan(800, true));
  await page.getByRole("dialog").getByText("2/15 已完成", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(await page.locator(".task-plan-trigger").evaluate(el => el === document.activeElement), true);
  for (const width of [560, 360]) {
    await page.setViewportSize({ width, height: 700 });
    await page.evaluate(width => window.renderPlan(width), width);
    await noOverlap();
    await page.locator(".task-plan-trigger").click();
    const bounds = await page.getByRole("dialog").boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
    assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 700);
    await page.getByRole("button", { name: "关闭任务进度" }).click();
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => window.renderPlan(800));
  await page.locator(".task-plan-trigger").click();
  await page.evaluate(() => window.renderPlan(1200));
  await page.locator(".task-progress-dock.is-expanded").waitFor();
  assert.equal(await page.getByRole("dialog").count(), 0);
  await page.evaluate(() => window.renderPlan(1200, false, true));
  await page.locator(".task-progress-dock.is-expanded").waitFor();
  await page.evaluate(() => document.documentElement.dataset.loomTheme = "light");
  await noOverlap();
  if (process.env.LOOM_PROGRESS_SCREENSHOT) await page.screenshot({ path: process.env.LOOM_PROGRESS_SCREENSHOT });
  assert.deepEqual(errors, []);
  console.log("Task panel: independent scroll, collapse, resize, live update, dialog focus, narrow/light layout passed.");
} finally { await browser.close(); }
