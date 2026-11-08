// The conversation's task list as a branch of the 建造 hub: its own button and card, apart from the project's plan.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/build-task-list.html?theme=${theme}`);
    const button = page.locator('.pf-branch-toggle[data-branch="tasks"]');
    const card = page.locator('[data-glide="tasks"]:not([data-leaving])');
    const plan = page.locator('[data-glide^="plan"]');
    await button.waitFor();

    // No list yet: the button stands there, disabled and saying why, and there is no empty card.
    assert.equal(await button.isDisabled(), true);
    assert.ok(await button.getAttribute("aria-description"), "an unavailable branch explains itself");
    assert.equal(await card.count(), 0);
    assert.match(await plan.innerText(), /阶段 1 计划/);

    // The list arrives: the card opens on its own, cabled to the hub, and the project's plan stays the plan.
    await page.evaluate(() => { window.tasksButton = document.querySelector('[data-branch="tasks"]'); window.taskListFixture.arrive(); });
    await card.waitFor();
    assert.equal(await button.getAttribute("aria-pressed"), "true");
    assert.equal(await page.evaluate(() => window.tasksButton === document.querySelector('[data-branch="tasks"]')), true, "the button is the same element");
    assert.match(await card.innerText(), /任务清单/);
    assert.match(await card.locator(".pf-count").innerText(), /^1\/3$/);
    assert.deepEqual(await card.locator(".pf-task").evaluateAll(rows => rows.map(row => row.dataset.state)), ["integrated", "working", "pending"]);
    assert.equal(await card.locator(".pf-strip-cell").count(), 3);
    await page.locator('.pf-cable[data-cable="tasks"]').waitFor({ state: "attached" });
    assert.match(await plan.innerText(), /阶段 1 计划/);
    assert.equal(await plan.locator(".pf-task").count(), 0, "the list is not in the plan card");
    const box = await card.boundingBox(), hub = await page.locator(".pf-composer").boundingBox();
    assert.ok(box.x >= hub.x + hub.width, "the card hangs in the right column");

    // It follows the list without leaving and coming back.
    await page.evaluate(() => { window.tasksCard = document.querySelector('[data-glide="tasks"]'); window.taskListFixture.step(); });
    await page.waitForFunction(() => document.querySelector('[data-glide="tasks"] .pf-count')?.textContent === "2/3");
    assert.equal(await page.evaluate(() => window.tasksCard === document.querySelector('[data-glide="tasks"]')), true);

    // A step closed without being done stays visibly apart and is not counted as ticked.
    await page.evaluate(() => window.taskListFixture.uncovered());
    await page.waitForFunction(() => document.querySelector('[data-glide="tasks"] .pf-count')?.textContent === "2/3");
    assert.deepEqual(await card.locator(".pf-task").evaluateAll(rows => rows.map(row => row.dataset.state)), ["integrated", "rework", "integrated"]);
    assert.equal(await card.locator(".pf-task").nth(1).getAttribute("title"), "未覆盖");
    if (await page.locator('.pf-stepper[data-editable]').count()) assert.match(await page.locator('.pf-stepper').getAttribute("title"), /普通对话不受它影响/);

    // Folded, it unplugs; unfolded, it is back. The person's choice survives the next update of the list.
    await button.click();
    await page.waitForFunction(() => !document.querySelector('[data-glide="tasks"]'));
    assert.equal(await button.getAttribute("aria-pressed"), "false");
    assert.equal(await page.locator('.pf-cable[data-cable="tasks"]').count(), 0);
    await button.click();
    await card.waitFor();
    assert.equal(await button.getAttribute("aria-pressed"), "true");
    if (process.env.LOOM_TASKS_SCREENSHOT) await page.screenshot({ path: `${process.env.LOOM_TASKS_SCREENSHOT}-${theme}.png` });
    assert.deepEqual(errors, []);
    await page.close();

    // A long goal runs with no project: one line in the hub, and the person can stop it.
    const goalPage = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await goalPage.goto(`${origin}/scripts/fixtures/build-task-list.html?theme=${theme}&goal=1`);
    const goalRow = goalPage.locator(".pf-goal");
    await goalRow.waitFor();
    assert.match(await goalRow.locator(".pf-goal-head").innerText(), /长目标进行中/);  // the spent figure moved into the meter
    assert.match(await goalRow.locator(".goal-budget-figures").innerText(), /1\.2M \/ 8M/);
    assert.match(await goalRow.locator(".goal-budget-exact").innerText(), /已用 1,200,000 · 总额 8,000,000 · 剩余 6,800,000 token/);
    await goalRow.getByRole("button", { name: "停止" }).click();  // the card also holds the budget control
    await goalPage.waitForFunction(() => window.goalCalls.length === 1);
    assert.equal(await goalPage.evaluate(() => window.goalCalls[0].action), "pause");
    if (process.env.LOOM_TASKS_SCREENSHOT) await goalPage.screenshot({ path: `${process.env.LOOM_TASKS_SCREENSHOT}-goal-${theme}.png` });
    await goalPage.close();

    // What the goal is doing beyond "running": sub-agents at work, a pass that went deeper, a provider being waited out.
    for (const [kind, expected] of [["agents", /长目标进行中，2 个子代理在工作/], ["deepen", /检查已通过，正用剩余时间加深（第 1 轮）/],
                                    ["retry", /模型服务暂时不可用，\d\d:\d\d 自动重试/]]) {
      const kindPage = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      await kindPage.goto(`${origin}/scripts/fixtures/build-task-list.html?theme=${theme}&goal=${kind}`);
      const row = kindPage.locator(".pf-goal");
      await row.waitFor();
      assert.match(await row.innerText(), expected, kind);
      if (process.env.LOOM_TASKS_SCREENSHOT) await kindPage.screenshot({ path: `${process.env.LOOM_TASKS_SCREENSHOT}-goal-${kind}-${theme}.png` });
      await kindPage.close();
    }
  }
  console.log("The task list is a branch of its own: explained while empty, opens with the list, follows it, folds, and leaves the plan card alone");
} finally { await browser.close(); }
