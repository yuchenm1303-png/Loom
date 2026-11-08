import assert from "node:assert/strict";
import fs from "node:fs";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const out = process.argv[2] || "../.cache/mobile-conversation-agents";
fs.mkdirSync(out, { recursive: true });
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(60000);
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  async function open(extra = "") {
    await page.goto(`${origin}/scripts/fixtures/mobile.html?goalagents=1${extra}`, { waitUntil: "domcontentloaded" });
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "建造", exact: true }).click();
    await page.getByText("Loom 账号页重做", { exact: true }).click();
    await page.getByRole("navigation", { name: "建造支路" }).getByRole("button", { name: "Agent", exact: true }).click();
  }
  await open();
  const checker = page.locator(".m-agent-card").filter({ has: page.getByRole("heading", { name: "独立检查", exact: true }) });
  await checker.getByText("运行中", { exact: true }).waitFor();
  await page.getByRole("heading", { name: "并行支路", exact: true }).waitFor();
  await checker.getByText("读取 report/report.md", { exact: true }).waitFor();
  assert.equal(await page.locator(".m-agent-card").count(), 2);
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: `${out}/agents-${width}.png`, animations: "disabled" });
  }
  await page.evaluate(() => window.__shell.agentState("checker-1", "completed", false));
  await page.getByRole("button", { name: "刷新建造状态", exact: true }).click();
  await checker.getByText("已完成 · 等待收取结果", { exact: true }).waitFor();
  await checker.getByRole("button", { name: "打开独立检查的执行对话", exact: true }).click();
  await page.getByRole("heading", { name: "独立检查执行记录", exact: true }).waitFor();
  assert.ok(await page.evaluate(() => window.__shell.calls.some(call => call.operation === "call" && call.args[0] === "thread/read" && call.args[1].threadId === "checker-1")));
  await open("&oldhost=1");
  await page.getByText(/电脑后台尚未提供对话子代理列表/).waitFor();
  await open("&emptyagents=1");
  await page.getByText(/当前没有未关闭的子代理/).waitFor();
  await open("&agenterror=1");
  await page.getByRole("alert").filter({ hasText: "子代理读取失败" }).waitFor();
  assert.deepEqual(errors, []);
  await page.close();
  console.log("PASS: independent checker without project run, completed result awaiting collection, child conversation navigation, old Host, empty/error states and 320/390px layouts");
} finally { await browser.close(); }
