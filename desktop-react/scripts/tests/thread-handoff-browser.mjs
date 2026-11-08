import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 850 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const theme of ["dark", "light"]) {
    await page.goto(`${origin}/scripts/fixtures/thread-handoff.html?theme=${theme}`);
    await page.locator(".thread-handoff-record").waitFor();
    await page.evaluate(() => { window.handoffFixture.emit(); window.handoffFixture.emit(); });
    await page.waitForFunction(() => window.handoffFixture.calls.filter(call => call[0] === "window").length === 1);
    assert.equal(await page.locator("header strong").textContent(), "原会话 · 账本导入", "opening a separate window preserves the source selection");
    await page.locator(".thread-handoff-record summary").click();
    assert.match(await page.locator(".thread-handoff-body").innerText(), /原会话已停止自动续跑/);
    await page.evaluate(() => window.handoffFixture.update({ state: "failed", error: "Provider unavailable" }));
    await page.getByText("Provider unavailable", { exact: true }).waitFor();
    await page.getByRole("button", { name: "在同一会话重试接续" }).click();
    await page.getByText("新会话正在接续", { exact: true }).waitFor();
    const retry = await page.evaluate(() => window.handoffFixture.calls.find(call => call[0] === "thread/handoff_retry"));
    assert.equal(retry[1].expectedTurnId, "attempt-0");
    assert.equal(await page.evaluate(() => window.handoffFixture.calls.filter(call => call[0] === "window").length), 1, "retry does not create another native window");
    await page.getByRole("button", { name: "打开接续会话", exact: true }).click();
    await page.getByText("从原会话接续", { exact: false }).waitFor();
    await page.locator(".thread-handoff-record summary").click();
    assert.match(await page.locator(".thread-handoff-body").innerText(), /验证空文件和损坏行/);
    assert.match(await page.locator(".thread-handoff-body").innerText(), /src\/parser.py/);
    const fit = await page.evaluate(() => {
      const header = document.querySelector("header").getBoundingClientRect(), panel = document.querySelector(".thread-handoff-panel").getBoundingClientRect(), stage = document.querySelector(".conversation-stage").getBoundingClientRect();
      return panel.top >= header.bottom - 1 && stage.top >= panel.bottom - 1 && stage.height > 100;
    });
    assert.equal(fit, true, "the handoff panel reserves space instead of overlapping the conversation");
    if (process.env.LOOM_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.LOOM_SCREENSHOT_DIR}/thread-handoff-${theme}.png` });
    await page.evaluate(() => { window.handoffFixture.setId("other"); window.handoffFixture.update({ id: "another", state: "running" }); });
    await page.waitForFunction(() => !document.querySelector(".thread-handoff-panel"));
    assert.equal(await page.evaluate(() => window.handoffFixture.calls.filter(call => call[0] === "window").length), 1, "events do not steal an unrelated conversation");
  }
  await page.goto(`${origin}/scripts/fixtures/thread-handoff.html?web=1`);
  await page.locator(".thread-handoff-record").waitFor();
  await page.evaluate(() => window.handoffFixture.emit());
  await page.waitForFunction(() => window.handoffFixture.calls.some(call => call[0] === "split"));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".thread-handoff-record summary").click();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(errors, []);
  console.log("Handoff UI: native navigation, duplicate events, retry receipt, provenance, dark/light layout, web fallback and narrow layout passed.");
} finally { await browser.close(); }
