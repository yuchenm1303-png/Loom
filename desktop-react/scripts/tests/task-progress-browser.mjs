// Exercise the real task tool against Vite: summary, durable milestones and dock state.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/task-progress.html`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => Boolean(window.renderPlan));
  await page.locator('.task-plan-panel[data-motion-phase="entered"]').waitFor();

  const panel = page.locator(".task-plan-panel");
  assert.equal(await panel.locator(".task-plan-chip").innerText(), "1/15");
  await panel.locator(".task-plan-summary-copy > strong").getByText("1/15 验收通过", { exact: true }).waitFor();
  await panel.locator(".task-plan-focus p").getByText("运行压力测试，验证长步骤在窄屏下换行并保留完整文字", { exact: true }).waitFor();
  await panel.getByText("进行中", { exact: true }).waitFor();
  await panel.getByText("当前环境没有配置 CDP；记录未覆盖", { exact: true }).waitFor();
  await panel.getByText("评审 · 3/3 次", { exact: true }).waitFor();
  await panel.getByText("依赖：确认浏览器版本", { exact: true }).waitFor();
  await panel.getByText("待返工：补充故障恢复验证", { exact: true }).waitFor();

  const evidence = panel.locator("details").filter({ hasText: "证据记录" }).first();
  await evidence.locator("summary").click();
  await panel.getByText("工具记录：call-17", { exact: true }).waitFor();
  await panel.getByText("browser_status 与 boot_token 均已核对", { exact: true }).waitFor();

  await page.evaluate(() => window.renderPlan(1280, true));
  await panel.locator(".task-plan-chip").getByText("3/15", { exact: true }).waitFor();
  await panel.locator(".task-plan-summary-copy > strong").getByText("2/15 验收通过", { exact: true }).waitFor();
  assert.equal(await panel.getByText("验收通过", { exact: true }).count(), 2, "only passed steps wear the accepted badge");
  await panel.getByText("未覆盖", { exact: true }).waitFor();

  const taskButton = page.locator('.loom-tool-dock [data-tool-id="tasks"]');
  assert.equal(await taskButton.getAttribute("aria-label"), "任务进度 · 3/15 已完成");
  assert.equal(await taskButton.getAttribute("data-ic-live"), null, "a finished plan stops the live mark");

  await page.evaluate(() => window.archiveTaskPlan());
  await page.waitForFunction(() => !document.querySelector(".task-plan-panel"));
  assert.equal(await taskButton.isDisabled(), true, "an archived plan is not an openable tool");
  assert.deepEqual(errors, []);
  console.log("Real task tool: summary, current step, outcomes, evidence, live state and archive passed.");
} finally {
  await browser.close();
}
