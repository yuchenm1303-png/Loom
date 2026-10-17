// Exercise the real Transcript component against Vite, without a model or Host.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/streaming.html`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => Boolean(window.renderItems));
  await page.addStyleTag({ url: "/src/theme.css?direct" });
  const lifecycleCss = await (await page.request.get(new URL("/src/components/task-lifecycle-motion.css?direct", page.url()).href)).text();
  await page.addStyleTag({ content: lifecycleCss.replaceAll(".app-shell", ":root") });
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
  assert.equal(await page.getByText("早期检查已经完成。", { exact: true }).count(), 1, "visible earlier records remain in place");
  assert.equal(await page.locator(".earlier-process-toggle").count(), 0);
  await page.getByText("失败项也写入报告。", { exact: true }).waitFor();
  await page.getByText("服务端暂时不可达；结果明确记为未覆盖", { exact: true }).waitFor();
  if (process.env.LOOM_PROGRESS_SCREENSHOT) await page.screenshot({ path: process.env.LOOM_PROGRESS_SCREENSHOT });
  await page.setViewportSize({ width: 480, height: 900 });
  await page.evaluate(() => document.documentElement.dataset.loomTheme = "light");
  await page.getByRole("button", { name: "查看任务进度，1/3 已完成" }).click();
  const bounds = await page.getByRole("dialog").boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 480, "plan fits a narrow viewport");
  await page.getByRole("button", { name: "关闭任务进度" }).click();
  await page.evaluate(items => window.renderItems([...items, {
    id: "answer", type: "assistant_message", threadId: "thread-1", turnId: "turn-1",
    status: "completed", phase: "final_answer", text: "报告已保存，其中连接测试仍未覆盖。",
  }], false), items);
  await page.locator(".turn-final-answer").getByText("报告已保存，其中连接测试仍未覆盖。", { exact: true }).waitFor();
  assert.equal(await page.locator(".task-progress-dock").count(), 1, "plan remains inspectable after turn completion");
  const graphSteps = [
    { id: "build", step: "实现协议", status: "completed", outcome: "passed", acceptance: ["实际调用可用"], evidence_refs: [{path:"proof.txt"}], evidence_facts: [{path:"proof.txt",sha256:"a".repeat(64)}] },
    { id: "review", step: "评审协议", type: "review", status: "blocked", outcome: "failed", deps: ["build"], cycles: 3, max_review_cycles: 3, blocker: "评审次数耗尽，需要修复后再审", feedback: {status:"open",report:"补充故障恢复验证"} },
    { id: "release", step: "交付", status: "pending", deps: ["review"] },
  ];
  const durable = [...items, item("state", "task_plan", { result: { plan: graphSteps, lifecycle: "active" } }),
    { ...item("continue", "user_message", {text:"继续"}), turnId:"turn-2" }];
  await page.evaluate(items => window.renderItems(items, false), durable);
  await page.getByRole("button", {name:"查看任务进度，1/3 已完成"}).click();
  await page.getByRole("dialog").getByText("1/3 验收通过", {exact:true}).waitFor();
  await page.getByText("评审 · 3/3 次", {exact:true}).waitFor();
  await page.getByText("待返工：补充故障恢复验证", {exact:true}).waitFor();
  await page.getByText("依赖：评审协议", {exact:true}).waitFor();
  await page.getByRole("dialog").locator("details").filter({hasText:"证据记录"}).locator("summary").click();
  await page.getByText(`文件：proof.txt · SHA-256 ${"a".repeat(64)}`, {exact:true}).waitFor();
  if (process.env.LOOM_PLAN_GRAPH_SCREENSHOT) await page.screenshot({path:process.env.LOOM_PLAN_GRAPH_SCREENSHOT});
  await page.getByRole("button", {name:"关闭任务进度"}).click();
  await page.evaluate(items => window.renderItems([...items, {
    id:"archive",type:"task_plan",threadId:"thread-1",turnId:"turn-2",status:"completed",
    result:{plan:[],lifecycle:"archived"},
  }], false), durable);
  assert.equal(await page.locator(".task-progress-dock").count(), 0, "explicit archive removes the active plan");
  assert.deepEqual(errors, []);
  console.log("Real Transcript progress, expansion, narrow viewport and final-answer checks passed.");
} finally {
  await browser.close();
}
