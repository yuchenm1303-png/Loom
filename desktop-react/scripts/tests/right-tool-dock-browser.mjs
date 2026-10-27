// Real React workspace + in-memory Host. Never opens a live browser or account.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true,
  ignoreDefaultArgs: ["--hide-scrollbars"] });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const ids = ["files","browser","terminal","background","tasks","agents","review",
  "artifacts","inspector","profile","account","settings"];
try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1420, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(order => {
      const key = "loom.ui.rightToolDock.order.v1";
      if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify(["sidebar", ...order]));
    }, ids);
    await page.goto(origin + "/scripts/fixtures/workspace-visual.html?context=1&theme=" + theme);
    const dock = page.locator(".loom-tool-dock");
    await dock.waitFor({ timeout: 20_000 });
    await page.locator(".turn-final-answer").waitFor({ timeout: 20_000 });
    const buttons = dock.locator("button[data-tool-id]");
    assert.equal(await buttons.count(), ids.length, "all shortcuts must remain visible");
    assert.deepEqual(await buttons.evaluateAll(nodes => nodes.map(n => n.dataset.toolId)), ids);
    assert.equal(await page.locator(".thread-header .thread-review-button").count(), 0,
      "workspace entries must no longer crowd the conversation header");
    assert.equal(await page.locator(".context-meter-chip").count(), 1,
      "context and run status stay in the conversation header");
    assert.equal(await dock.locator('[data-tool-id="browser"]').count(), 1,
      "browser must remain discoverable when Electron is unavailable");
    assert.equal(await dock.locator('[data-tool-id="browser"]').isDisabled(), true,
      "web cannot advertise an inert Electron browser action");

    const bounds = await page.evaluate(() => {
      const box = name => document.querySelector(name).getBoundingClientRect();
      return { rail:box(".loom-tool-rail").left, workspace:box(".workspace").right };
    });
    assert.ok(bounds.workspace <= bounds.rail + 1, "dock overlaps conversation content");

    // The sidebar's toggle stays on one spot: the same button closes the sidebar from the card's top row and opens it
    // again from the header's start (SidebarControls), so the keyboard simply stays on it.
    const toggle = page.locator('.sidebar-controls .sidebar-toggle');
    const spot = await toggle.boundingBox();
    await toggle.focus();
    await page.keyboard.press("Enter");
    await page.locator(".app-shell.sidebar-closed").waitFor({ timeout: 4000 });
    await page.waitForFunction(() => document.activeElement?.matches(".sidebar-controls .sidebar-toggle"), null, { timeout: 2000 });
    const moved = await toggle.boundingBox();
    assert.ok(Math.abs(moved.x - spot.x) <= 1 && Math.abs(moved.y - spot.y) <= 1,
      `the sidebar opens from where it closed: ${JSON.stringify({ spot, moved })}`);
    await page.keyboard.press("Enter");
    await page.locator(".app-shell.sidebar-open").waitFor({ timeout: 4000 });
    await page.waitForFunction(() => document.activeElement?.matches(".sidebar-controls .sidebar-toggle"), null, { timeout: 2000 });

    await dock.locator('[data-tool-id="agents"]').click();
    await page.locator('.sub-agent-dock[data-open="true"]').waitFor({ timeout: 5000 });
    // The existing panel intentionally translates during its ~390ms arrival.
    // The dock must remain actionable throughout, and the settled panel must
    // stop at the reserved rail column rather than covering controls.
    assert.equal(await dock.locator('[data-tool-id="agents"]').isEnabled(), true);
    await page.locator('.sub-agent-dock[data-motion-phase="entered"]').waitFor({ timeout: 5000 });
    const afterPanel = await page.evaluate(() => ({
      panel: document.querySelector('.sub-agent-dock[data-open="true"]').getBoundingClientRect().right,
      rail: document.querySelector(".loom-tool-rail").getBoundingClientRect().left,
    }));
    assert.ok(afterPanel.panel <= afterPanel.rail + 2, "agent surface obscures rail: " + JSON.stringify(afterPanel));
    await dock.locator('[data-tool-id="agents"]').click();

    await dock.locator('[data-tool-id="background"]').click();
    await page.locator(".background-task-panel").waitFor({ timeout: 5000 });
    const backgroundBounds = await page.evaluate(() => ({
      panel: document.querySelector(".background-task-panel").getBoundingClientRect().right,
      rail: document.querySelector(".loom-tool-rail").getBoundingClientRect().left,
    }));
    assert.ok(backgroundBounds.panel <= backgroundBounds.rail + 2,
      "background tasks obscure rail: " + JSON.stringify(backgroundBounds));
    await dock.locator('[data-tool-id="background"]').click();

    await dock.locator('[data-tool-id="agents"]').focus();
    await page.keyboard.press("Alt+ArrowUp");
    const movedTo = ids.indexOf("agents") - 1;
    assert.equal(await dock.locator("button").nth(movedTo).getAttribute("data-tool-id"), "agents");
    assert.equal(await page.evaluate(index => JSON.parse(localStorage.getItem("loom.ui.rightToolDock.order.v1"))[index], movedTo), "agents");
    await page.reload();
    await page.locator(".loom-tool-dock").waitFor();
    assert.equal(await page.locator(".loom-tool-dock button").nth(movedTo).getAttribute("data-tool-id"), "agents",
      "customized dock order must survive reload");

    await page.setViewportSize({ width: 825, height: 700 });
    assert.equal(await page.locator(".loom-tool-dock button[data-tool-id]").count(), ids.length,
      "narrow workspace cannot silently drop shortcuts");
    // The conversation scrolls from the window edge, outside the dock's hit area.
    await page.locator(".transcript").evaluate(el => { el.style.minHeight = "2200px"; });
    const track = page.locator(".transcript-edge-scrollbar:visible");
    await track.waitFor();
    for (const viewport of [{ width: 1420, height: 900 }, { width: 780, height: 700 }]) {
      await page.setViewportSize(viewport);
      await page.waitForFunction(() => {
        const track = document.querySelector(".transcript-edge-scrollbar:not([hidden])");
        const scroller = document.querySelector(".transcript-scroll");
        return track && Math.abs(track.getBoundingClientRect().right - innerWidth) < 1
          && track.clientHeight === scroller.clientHeight && track.scrollHeight === scroller.scrollHeight;
      });
      const lane = await track.boundingBox();
      const capsule = await dock.boundingBox();
      assert.ok(lane.x >= capsule.x + capsule.width + 2, "window-edge scroll track overlaps tools");
      await track.focus();
      await page.keyboard.press("Home");
      await page.waitForFunction(() => document.querySelector(".transcript-scroll").scrollTop < 1);
      await page.keyboard.press("End");
      await page.waitForFunction(() => {
        const scroller = document.querySelector(".transcript-scroll");
        return scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop < 2;
      });
    }
    assert.deepEqual(errors, [], theme + " quick dock caused runtime errors");
    await page.close();
  }
  console.log("PASS: right tool dock shortcuts, safe layout, panel access, keyboard order and themes");
} finally {
  await browser.close();
}
