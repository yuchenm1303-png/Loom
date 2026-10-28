import assert from "node:assert/strict";
import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5186";
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 950 } });
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  await page.goto(`${origin}/scripts/fixtures/project-runs.html?empty`);
  await page.getByRole("textbox", { name: "目标与验收要求" }).fill("实现离线笔记工具并验证重启恢复");
  await page.getByRole("checkbox").check();
  await page.getByRole("combobox", { name: "并行执行上限" }).selectOption("2");
  await page.evaluate(() => { window.projectRunTest.failNext = true; });
  await page.getByRole("button", { name: "创建，暂不执行" }).click();
  await page.getByRole("button", { name: "重试上次请求" }).waitFor();
  await page.getByRole("button", { name: "重试上次请求" }).click();
  await page.getByRole("button", { name: "启动", exact: true }).waitFor();
  const creation = await page.evaluate(() => window.projectRunTest.calls.filter(item => item.method === "projectRun/create"));
  assert.equal(creation.length, 2); assert.deepEqual(creation[0], creation[1]);
  assert.equal(await page.evaluate(() => window.projectRunTest.runs.length), 1);
  assert.equal(creation[0].params.completionPolicy, "local_integrate");
  assert.equal(creation[0].params.maxWorkers, 2);
  assert.equal(await page.evaluate(() => window.projectRunTest.calls.some(item => item.method === "projectRun/start")), false);
  await page.getByRole("button", { name: "启动", exact: true }).click();
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await page.getByRole("button", { name: "继续", exact: true }).waitFor();
  await page.evaluate(() => {
    const state = window.projectRunTest, root = state.runs[0];
    Object.assign(root, { state: "running", control: "active", phase: "parallel", workflowControl: "active", teamChildren: [{ runId: "team-alpha", taskId: "task-1", state: "running" }] });
    state.runs.push({ ...root, id: "team-alpha", teamOwnerId: root.id, maxWorkers: 1, teamChildren: undefined, completionPolicy: "manual", phase: "working", plan: [] });
  });
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("button", { name: "task-1 · 执行中", exact: true }).click();
  await page.getByText(/协作任务的预算与暂停由主任务统一控制。/).waitFor();
  assert.equal(await page.getByRole("button", { name: "暂停", exact: true }).count(), 0);
  await page.getByRole("button", { name: "返回主任务", exact: true }).click();
  await page.evaluate(() => {
    const state = window.projectRunTest, root = state.runs[0];
    state.runs.splice(1); root.teamChildren = undefined;
    Object.assign(root, { workflowControl: "active", workflowStatus: "awaiting_review", state: "delivered", integrationRunId: "child" });
    state.runs.push({ ...root, id: "child", revision: 3, objective: root.objective, integrationRunId: undefined, workflowOwnerId: root.id,
      integrationTarget: { parentRunId: root.id }, completionPolicy: "manual", state: "running", phase: "verifying", control: "active",
      tokensUsed: 72500, episodesUsed: 18, plan: [{ id: "task-1", instructions: "验证离线保存及重启恢复", acceptance: ["重启后恢复已保存内容"], state: "verified" }] });
    state.runs[1].activeEpisode = { sessionId: "review-session", state: "waiting_approval" };
  });
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByText("72,500 / 100,000", { exact: true }).waitFor();
  await page.getByRole("button", { name: "打开执行会话 / 处理审批", exact: true }).click();
  assert.equal(await page.evaluate(() => window.openedProjectThread), "review-session");
  await page.getByRole("button", { name: "读取验收证据与有效性" }).click();
  await page.getByText("通过 · 完整项目", { exact: true }).waitFor();
  await page.screenshot({ path: process.env.LOOM_PROJECT_SCREENSHOT || ".cursor-build/project-runs-dark.png", fullPage: true });
  await page.evaluate(() => document.documentElement.setAttribute("data-light", ""));
  await page.setViewportSize({ width: 640, height: 900 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: ".cursor-build/project-runs-narrow.png", fullPage: true });
  await page.setViewportSize({ width: 400, height: 850 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.goto(`${origin}/scripts/fixtures/project-runs.html?lang=en`);
  await page.getByRole("heading", { name: "Persistent projects" }).waitFor();
  await page.evaluate(() => { window.projectRunTest.offline = true; });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByRole("alert").waitFor();
  assert.match(await page.getByRole("alert").innerText(), /disconnected/);
  assert.deepEqual(errors, []);
  console.log("Project task UI: lost-response retry, explicit start, pause, live child usage, evidence, narrow layout, English and offline states passed.");
} finally { await browser.close(); }
