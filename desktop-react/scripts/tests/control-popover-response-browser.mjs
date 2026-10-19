import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  for (const theme of ["light", "dark"]) for (const motion of ["1", "0"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?context=1&theme=${theme}&motion=${motion}`);
    await page.locator(".permission-chip").waitFor();
    await page.locator(".turn-final-answer").waitFor();
    await page.locator(".context-meter-chip").waitFor();
    const samples = [];
    for (const [trigger, panel, content] of [
      [".permission-chip", ".permission-popover", ".permission-popover .composer-popover-head"],
      [".model-chip", ".model-popover", ".model-popover .composer-popover-head"],
      [".context-meter-chip", ".context-meter-panel", ".context-meter-panel-head"],
    ]) {
      await page.locator(trigger).waitFor();
      // A delayed authoritative thread refresh cannot gate the model picker.
      if (trigger === ".model-chip") await page.evaluate(() => window.navigationFixture.hold("visual-0"));
      const sample = await page.evaluate(async ({ trigger, panel, content }) => {
        const started = performance.now();
        document.querySelector(trigger).click();
        await new Promise(resolve => requestAnimationFrame(resolve));
        const root = document.querySelector(panel);
        const text = document.querySelector(content);
        if (!root || !text) throw new Error(`Missing ${panel} after ${trigger}; expanded=${document.querySelector(trigger)?.getAttribute("aria-expanded")}`);
        const style = getComputedStyle(root);
        const textStyle = getComputedStyle(text);
        return { elapsed: performance.now() - started, phase: root.dataset.motionPhase,
          opacity: Number(style.opacity) * Number(textStyle.opacity), rootOpacity: style.opacity, textOpacity: textStyle.opacity,
          inert: root.inert, delay: textStyle.transitionDelay };
      }, { trigger, panel, content });
      samples.push({ trigger, ...sample });
      assert.ok(sample.opacity >= .5, `${theme}/${motion} ${trigger}: first frame content is hidden: ${JSON.stringify(sample)}`);
      assert.equal(sample.inert, false);
      assert.ok(sample.delay.split(",").every(value => parseFloat(value) === 0), "readable content must not wait for a stagger delay");
      // Reversal retains its current visual position, then the final close releases it.
      await page.locator(trigger).click();
      await page.locator(trigger).click();
      assert.equal(await page.locator(panel).getAttribute("inert"), null);
      await page.keyboard.press("Escape");
      await page.waitForFunction(selector => !document.querySelector(selector), panel);
      if (trigger === ".model-chip") await page.evaluate(() => window.navigationFixture.release("visual-0"));
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ theme, motion, samples }));
    await page.close();
  }

  // A composer card belongs above the surrounding chrome. In a narrow layout
  // it may legitimately extend into the inspector/tool-rail tracks; those
  // surfaces must neither clip it nor paint over it.
  const narrow = await browser.newPage({ viewport: { width: 820, height: 760 } });
  await narrow.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=dark&motion=1&inspector=1`);
  await narrow.locator(".model-chip").click();
  await narrow.locator('.model-popover[data-motion-phase="entered"]').waitFor();
  const overlap = await narrow.evaluate(() => {
    const workspace = document.querySelector(".workspace").getBoundingClientRect();
    const panel = document.querySelector(".model-popover").getBoundingClientRect();
    const sampleX = Math.min(panel.right - 8, Math.max(panel.left + 8, workspace.right + 8));
    const sampleY = Math.min(panel.bottom - 8, panel.top + panel.height / 2);
    const topmost = document.elementFromPoint(sampleX, sampleY);
    return {
      workspaceRight: workspace.right,
      panel: { left: panel.left, right: panel.right, top: panel.top, bottom: panel.bottom },
      sampleX,
      owned: Boolean(topmost?.closest(".model-popover")),
    };
  });
  assert.ok(overlap.panel.right > overlap.workspaceRight + 4, `fixture did not exercise cross-panel overlap: ${JSON.stringify(overlap)}`);
  assert.ok(overlap.panel.left >= 0 && overlap.panel.right <= 820, `model card escaped the viewport: ${JSON.stringify(overlap)}`);
  assert.equal(overlap.owned, true, `side chrome covered the model card: ${JSON.stringify(overlap)}`);
  await narrow.keyboard.press("Escape");
  await narrow.locator(".model-popover").waitFor({ state: "detached" });
  await narrow.locator(".permission-chip").click();
  await narrow.locator('.permission-popover[data-motion-phase="entered"]').waitFor();
  const permissionVisible = await narrow.locator(".permission-popover").evaluate(panel => {
    const rect = panel.getBoundingClientRect();
    const topmost = document.elementFromPoint(rect.right - 8, rect.top + rect.height / 2);
    return { left: rect.left, right: rect.right, owned: Boolean(topmost?.closest(".permission-popover")) };
  });
  assert.ok(permissionVisible.left >= 0 && permissionVisible.right <= 820, `permission card escaped the viewport: ${JSON.stringify(permissionVisible)}`);
  assert.equal(permissionVisible.owned, true, `side chrome covered the permission card: ${JSON.stringify(permissionVisible)}`);
  await narrow.close();
  console.log("PASS: permission, model and context controls show readable content on their first frame in both themes/motion preferences");
} finally { await browser.close(); }
