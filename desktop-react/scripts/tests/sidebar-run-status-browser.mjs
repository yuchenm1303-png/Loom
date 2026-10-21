import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

try {
  const page = await browser.newPage({ viewport: { width: 1150, height: 800 } });
  await page.addInitScript(() => localStorage.setItem(
    "loom.sidebar.expandedProjects",
    JSON.stringify(["loom", "agent", "relay"]),
  ));
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  for (const theme of ["dark", "light"]) {
    await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=${theme}&motion=1`);
    const row = page.locator('.compact-thread-row[data-thread-id="visual-1"]');
    await row.waitFor();

    const change = (status, paused = false) =>
      page.evaluate(({ status, paused }) => {
        window.performanceFixture.setThreadStatus("visual-1", status, paused);
      }, { status, paused });

    await change("running");
    await row.getByText("运行中", { exact: true }).waitFor();
    assert.equal(await row.getAttribute("data-run-state"), "running");
    assert.equal(await row.locator(".compact-thread-status-mark").count(), 1);
    // The background transitions from transparent on the next paint frame.
    await page.waitForFunction(() => {
      const element = document.querySelector('.compact-thread-row[data-thread-id="visual-1"]');
      return element && getComputedStyle(element).backgroundColor !== "rgba(0, 0, 0, 0)";
    });
    assert.notEqual(await row.evaluate(el => getComputedStyle(el).backgroundColor), "rgba(0, 0, 0, 0)",
      "non-selected running threads receive a visible surface");
    assert.equal(await row.locator(".thread-quick-actions button").nth(1).isDisabled(), true,
      "active runs cannot be archived accidentally");

    await change("waiting_approval");
    await row.getByText("待审批", { exact: true }).waitFor();
    assert.equal(await row.getAttribute("data-run-state"), "waiting-approval");

    await change("running", true);
    await row.getByText("已暂停", { exact: true }).waitFor();
    assert.equal(await row.getAttribute("data-run-state"), "paused");

    await change("completed");
    await page.waitForFunction(() => {
      const element = document.querySelector('.compact-thread-row[data-thread-id="visual-1"]');
      return element && !element.dataset.runState && !element.querySelector(".compact-thread-status");
    });

    await change("failed");
    assert.equal(await row.locator(".compact-thread-status").count(), 0,
      "finished and failed threads do not remain highlighted as running");

    // A non-selected thread must also react to TURN_STARTED / TURN_COMPLETED,
    // including notifications carrying their thread ID only in the turn.
    await page.evaluate(() => window.performanceFixture.emit({ method: "turn/started", params: {
      turn: { id: "turn-next", threadId: "visual-1", status: "running" },
    } }));
    await row.getByText("运行中", { exact: true }).waitFor();
    await page.evaluate(() => window.performanceFixture.emit({ method: "turn/completed", params: {
      turn: { id: "turn-next", threadId: "visual-1", status: "completed" },
    } }));
    await page.waitForFunction(() =>
      !document.querySelector('.compact-thread-row[data-thread-id="visual-1"]')?.dataset.runState,
    );

    await change("running");
    await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
    await page.waitForFunction(() =>
      document.querySelector('.compact-thread-row[data-thread-id="visual-1"] .compact-thread-status-mark')
        ?.getAnimations().length === 0,
    );
  }
  assert.deepEqual(errors, []);
  console.log("Sidebar running, approval, paused and terminal states passed in light/dark themes.");
} finally {
  await browser.close();
}
