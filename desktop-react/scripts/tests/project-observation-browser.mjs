import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const branch = name => page.locator(`.pf-branch-toggles [data-branch="observe-${name}"]`);
  const panel = name => page.locator(`[data-observation="${name}"]`);
  const names = ["blockers", "evidence", "reviews", "changes", "spending", "delivery"];
  const openBranch = async name => {
    if (await branch(name).getAttribute("aria-pressed") !== "true") await branch(name).click();
  };
  for (const theme of ["dark", "light"]) {
    await page.goto(`${origin}/scripts/fixtures/project-observation.html?theme=${theme}`);
    await page.locator(".pf-branch-toggles [data-branch^=\"observe-\"]").first().waitFor();
    assert.equal(await page.evaluate(() => window.observationFlags.calls.length), 0, "details load only when opened");
    await branch("blockers").focus();
    await page.keyboard.press("Enter");
    await panel("blockers").getByText("文件或资源占用", { exact: true }).waitFor();
    assert.match(await panel("blockers").innerText(), /相关任务合并或释放资源后继续/);
    assert.ok(await page.locator('[data-cable="observe-blockers"]').count(), "the observation card is a connected branch");
    assert.match(await page.locator(".pf-below").innerText(), /依赖 parser/);

    await branch("evidence").click();
    await panel("evidence").getByText("历史通过", { exact: true }).waitFor();
    await panel("evidence").locator("summary").first().click();
    assert.match(await panel("evidence").innerText(), /event-real-1/);
    assert.match(await panel("evidence").innerText(), /python -m pytest/);
    await panel("evidence").locator(".bo-log summary").click();
    assert.match(await panel("evidence").innerText(), /12 passed/);
    assert.match(await panel("evidence").innerText(), /未记录执行/);
    if (process.env.LOOM_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.LOOM_SCREENSHOT_DIR}/build-observation-${theme}.png` });

    await branch("reviews").click();
    await panel("reviews").locator("summary").first().click();
    assert.match(await panel("reviews").innerText(), /空文件输入/);
    await branch("changes").click();
    await panel("changes").waitFor();
    assert.match(await panel("changes").innerText(), /tests\/金额 校验.py/);
    await branch("spending").click();
    await panel("spending").waitFor();
    assert.match(await panel("spending").innerText(), /4,500/);
    assert.match(await panel("spending").innerText(), /5,500/);
    await branch("delivery").click();
    await panel("delivery").waitFor();
    assert.match(await panel("delivery").innerText(), /远端发布/);
    assert.match(await panel("delivery").innerText(), /未记录/);
    assert.match(await panel("delivery").innerText(), /已本地集成/);
    for (const name of names) {
      assert.equal(await branch(name).getAttribute("aria-pressed"), "true", `${name} stays independently open`);
      assert.equal(await panel(name).count(), 1);
      assert.equal(await page.locator(`[data-cable="observe-${name}"]`).count(), 1);
    }
    assert.match(await panel("evidence").innerText(), /12 passed/, "opening other branches preserves expanded evidence details");
    await branch("spending").click();
    await panel("spending").waitFor({ state: "detached" });
    for (const name of names.filter(name => name !== "spending")) assert.equal(await branch(name).getAttribute("aria-pressed"), "true");
    await openBranch("spending");
    await panel("spending").waitFor();
    await page.waitForTimeout(850);
    const cards = await page.locator('.pf-slot[data-glide^="observe-"]').evaluateAll(nodes => nodes.map(node => {
      const rect = node.getBoundingClientRect(); return { top: rect.top, bottom: rect.bottom };
    }));
    for (let index = 1; index < cards.length; index++) assert.ok(cards[index].top >= cards[index - 1].bottom - 1, "simultaneous cards do not overlap");

    await page.evaluate(() => { window.observationFlags.fail = true; });
    await panel("delivery").getByRole("button", { name: "刷新运行记录" }).click();
    await panel("delivery").getByRole("alert").waitFor();
    assert.match(await panel("delivery").innerText(), /保留上次记录/);
    assert.match(await panel("delivery").innerText(), /abc123456789deadbeef/);
    await page.evaluate(() => { window.observationFlags.fail = false; window.observationFixture.offline(true); });
    await openBranch("blockers");
    assert.match(await panel("blockers").innerText(), /连接中断/);
    assert.equal(await panel("blockers").getByRole("button", { name: "重新复审并继续" }).isDisabled(), true);
    await page.evaluate(() => { window.observationFixture.offline(false); window.observationFlags.stale = true; });
    await panel("blockers").getByRole("button", { name: "刷新运行记录" }).click();
    await panel("blockers").locator(".bo-error").waitFor({ state: "detached" });
    await panel("blockers").getByText("记录已过期", { exact: false }).waitFor();
    assert.equal(await panel("blockers").getByRole("button", { name: "重新复审并继续" }).isDisabled(), true);
    await page.evaluate(() => { window.observationFlags.stale = false; });
    await page.evaluate(() => { window.observationFlags.delayed = true; window.observationFixture.thread("build-old"); });
    await page.waitForFunction(() => window.observationFlags.releases.length === 1);
    await page.evaluate(() => { window.observationFlags.delayed = false; window.observationFixture.thread("build-new"); });
    await openBranch("evidence");
    await panel("evidence").getByText("build-new · CSV 导入保持金额精度", { exact: true }).waitFor();
    await page.evaluate(() => window.observationFlags.releases.shift()());
    assert.equal(await panel("evidence").getByText("build-old · CSV 导入保持金额精度", { exact: true }).count(), 0, "late response cannot replace another conversation");

    await page.evaluate(() => { window.observationFixture.goal(true); window.observationFixture.thread("goal"); });
    await openBranch("changes");
    await panel("changes").locator("summary").filter({ hasText: "验证导频密度" }).waitFor();
    await panel("changes").locator("summary").click();
    assert.match(await panel("changes").innerText(), /notes\/pilot.md/);
    await openBranch("evidence");
    assert.match(await panel("evidence").innerText(), /未记录执行/);
    for (const name of names) if (await branch(name).getAttribute("aria-pressed") === "true") await branch(name).click();
    await page.waitForFunction(() => !document.querySelector(".pf-observation"));
  }
  await page.setViewportSize({ width: 760, height: 700 });
  await branch("changes").click();
  await panel("changes").waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  assert.equal(overflow, false, "narrow layout stays within the viewport");
  await page.evaluate(() => window.observationFixture.unavailable(true));
  assert.equal(await branch("changes").isDisabled(), true);
  await panel("changes").getByText("还没有读取到运行记录。", { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log("Build observations: six branches, evidence, spending, delivery, failures, thread races, goal lines, keyboard and narrow layout passed.");
} finally { await browser.close(); }
