import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&agents=1&changes=1&lang=en`, { waitUntil: "domcontentloaded" });
  await page.locator(".loom-tool-dock").waitFor();
  await page.waitForFunction(() => document.querySelector('.loom-tool-rail')?.dataset.motionPhase === 'entered');
  const measurements = [];
  for (const id of ["review", "background", "agents", "artifacts", "inspector", "background", "review"]) {
    const result = await page.evaluate(async id => {
      const button = document.querySelector(`.loom-tool-button[data-tool-id="${id}"]`);
      const start = performance.now();
      const frames = [];
      let previous = start;
      let acknowledged = null;
      let overlap = false;
      let dockBlocked = false;
      const blockers = new Set();
      button.click();
      await new Promise(resolve => {
        const sample = () => {
          const now = performance.now();
          frames.push(now - previous);
          previous = now;
          if (acknowledged === null && button.getAttribute('aria-pressed') === 'true') acknowledged = now - start;
          overlap ||= document.querySelectorAll('.loom-tool-button[aria-pressed="true"]').length > 1;
          const rect = button.getBoundingClientRect();
          const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
          dockBlocked ||= !button.contains(hit);
          if (!button.contains(hit)) blockers.add(hit?.className || hit?.tagName || "none");
          if (now - start < 1100) requestAnimationFrame(sample);
          else resolve();
        };
        requestAnimationFrame(sample);
      });
      return { id, acknowledged, overlap, dockBlocked, blockers: [...blockers], maxFrameGap: Math.max(...frames),
        transitionActive: Boolean(document.documentElement.dataset.loomLayoutTransition),
        handoffActive: Boolean(document.documentElement.dataset.loomPanelHandoff),
        active: [...document.querySelectorAll('.loom-tool-button[aria-pressed="true"]')].map(b => b.dataset.toolId) };
    }, id);
    measurements.push(result);
    console.log(JSON.stringify(result));
    assert.deepEqual(result.active, [id]);
    assert.equal(result.overlap, false, `${id}: panels were selected together`);
    assert.equal(result.transitionActive, false, `${id}: transition did not release`);
    assert.equal(result.handoffActive, false, `${id}: handoff did not release`);
    assert.equal(result.dockBlocked, false, `${id}: snapshots blocked the next dock click: ${JSON.stringify(result)}`);
  }

  // Right tools must not invoke the screenshot API at all. Deliberately hold
  // any callback to catch an accidental regression back to snapshot waits.
  await page.evaluate(() => {
    window.heldPanelTransitions = [];
    window.originalPanelTransition = document.startViewTransition;
    document.startViewTransition = update => {
      let done;
      const finished = new Promise(resolve => { done = resolve; });
      const entry = { release: async () => { await update(); done(); } };
      window.heldPanelTransitions.push(entry);
      return { skipTransition() {}, updateCallbackDone: finished, finished, ready: finished };
    };
    document.querySelector('.loom-tool-button[data-tool-id="agents"]').click();
    document.querySelector('.loom-tool-button[data-tool-id="artifacts"]').click();
  });
  await page.evaluate(async () => {
    const held = window.heldPanelTransitions;
    for (const entry of [...held].reverse()) await entry.release();
    document.startViewTransition = window.originalPanelTransition;
  });
  assert.equal(await page.evaluate(() => window.heldPanelTransitions.length), 0, "right tool switches must not wait for a screenshot");
  await page.waitForFunction(() => !document.documentElement.dataset.loomLayoutTransition);
  assert.deepEqual(await page.locator('.loom-tool-button[aria-pressed="true"]').evaluateAll(nodes => nodes.map(n => n.dataset.toolId)), ["artifacts"], "a delayed old click must not replace the latest panel");

  // Reduced motion must commit without leaving snapshots or transforms behind.
  await page.evaluate(() => {
    document.documentElement.dataset.loomReducedMotion = 'true';
    document.querySelector('.loom-tool-button[data-tool-id="background"]').click();
  });
  await page.waitForFunction(() => document.querySelector('.loom-tool-button[data-tool-id="background"]')?.getAttribute('aria-pressed') === 'true');
  assert.equal(await page.evaluate(() => Boolean(document.documentElement.dataset.loomLayoutTransition)), false);
  await page.setViewportSize({ width: 760, height: 700 });
  const bounds = await page.locator('.background-task-panel').boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 760, 'background palette stays in the narrow viewport');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify(measurements, null, 2));
  console.log("Panel frame sampling, last-click wins, reduced motion and narrow layout passed.");
} finally {
  await browser.close();
}
