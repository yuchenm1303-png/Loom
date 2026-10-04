// Exercise the real Transcript component against Vite, without a model or Host.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/streaming.html`);
  await page.waitForFunction(() => Boolean(window.renderItems));
  await page.addStyleTag({ url: "/src/theme.css?direct" });
  const item = (id, type, fields = {}) => ({ id, type, threadId: "thread-1", turnId: "turn-1", status: "completed", ...fields });
  const items = [item("user", "user_message", { text: "完成测试并交付报告" }),
    item("old-progress", "assistant_message", { text: "早期检查已经完成。", phase: "commentary" }),
    item("plan", "tool_call", { toolName: "update_plan", ok: true, result: { plan: [
      { step: "验证浏览器能力", status: "completed", evidence: "检查记录" },
      { step: "补测服务器连接", status: "blocked", blocker: "服务端暂时不可达；结果明确记为未覆盖" },
      { step: "整理报告", status: "in_progress" },
    ] }, content: "Plan updated." }),
    item("steer", "user_message", { text: "失败项也写入报告。", source: "steering" }),
    item("latest", "assistant_message", { text: "浏览器检查已完成，正在整理结果和未覆盖项。", phase: "commentary" }),
    item("command", "tool_call", { toolName: "exec", status: "running", arguments: { cmd: "build report" } })];
  await page.evaluate(items => window.renderItems(items, true), items);
  await page.getByText("1/3 已完成", { exact: true }).waitFor();
  assert.equal(await page.getByText("早期检查已经完成。", { exact: true }).count(), 0);
  await page.getByText("失败项也写入报告。", { exact: true }).waitFor();
  await page.getByText("服务端暂时不可达；结果明确记为未覆盖", { exact: true }).waitFor();
  await page.getByRole("button", { name: "展开较早过程，2 项" }).click();
  await page.getByText("早期检查已经完成。", { exact: true }).waitFor();
  await page.getByRole("button", { name: "收起较早过程，2 项" }).click();
  if (process.env.LOOM_PROGRESS_SCREENSHOT) await page.screenshot({ path: process.env.LOOM_PROGRESS_SCREENSHOT });
  await page.setViewportSize({ width: 480, height: 900 });
  await page.evaluate(() => document.documentElement.dataset.loomTheme = "light");
  const bounds = await page.locator(".task-milestones").boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 480, "plan fits a narrow viewport");
  await page.evaluate(items => window.renderItems([...items, {
    id: "answer", type: "assistant_message", threadId: "thread-1", turnId: "turn-1",
    status: "completed", phase: "final_answer", text: "报告已保存，其中连接测试仍未覆盖。",
  }], false), items);
  await page.locator(".turn-final-answer").getByText("报告已保存，其中连接测试仍未覆盖。", { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log("Real Transcript progress, expansion, narrow viewport and final-answer checks passed.");
} finally {
  await browser.close();
}
