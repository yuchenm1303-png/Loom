import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const server = await (await import("vite")).createServer({ server: { port: 18319, strictPort: false, hmr: false } });
await server.listen();
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${server.resolvedUrls.local[0]}scripts/fixtures/project-flow.html?at=0&draft=1&theme=${theme}`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.locator('[data-glide="plan-draft"]').waitFor({ timeout: 60000 });
    await page.waitForTimeout(1300);
    console.log(`Checking ${theme} card/cable synchronization`);
    const trace = async (ids, burst = false, reverse = false) => page.evaluate(async ({ ids, burst, reverse }) => {
      let worst = 0, samples = 0;
      const click = id => document.querySelector(id === "model" ? '.pf-model-chip' : `[data-branch="${id}"]`).click();
      ids.forEach(click);
      await new Promise(resolve => setTimeout(resolve, reverse ? 90 : 1300));
      if (burst) ids.forEach((id, index) => setTimeout(() => click(id), index * 70));
      else ids.forEach(click);
      for (const end = performance.now() + 1500; performance.now() < end;) {
        await new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve)));
        for (const group of document.querySelectorAll('.pf-cable:not([data-leaving])')) {
          const path = group.querySelector('.pf-cable-body');
          const slot = [...document.querySelectorAll('[data-glide]')].find(node => node.dataset.glide === group.dataset.cable);
          const card = slot?.querySelector(':scope > .pf-card');
          const shell = card?.getAnimations().find(animation => animation.id === 'pf-shell'
            && 'clipPath' in animation.effect.getKeyframes()[0]);
          if (!shell || Number(shell.currentTime) <= Number(shell.effect.getTiming().delay) + 5) continue;
          samples += 1;
          // The normalized path runs card -> hub. A negative dash offset leaves this much cable missing at the card.
          worst = Math.max(worst, Math.abs(parseFloat(getComputedStyle(path).strokeDashoffset)) * path.getTotalLength() / 100);
        }
      }
      return { worst, samples };
    }, { ids, burst, reverse });
    for (const [ids, burst, reverse] of [[["plan"], false], [["repo", "files", "knowledge"], false], [["repo", "files", "knowledge", "plan"], true], [["plan"], false, true]]) {
      const result = await trace(ids, burst, reverse);
      assert.ok(result.samples > 0, `sampled births: ${JSON.stringify(result)}`);
      assert.ok(result.worst < 1.5, `cable is attached as the card opens (${theme}, ${ids}): ${JSON.stringify(result)}`);
      console.log(`PASS ${theme} ${ids.join("+")}${reverse ? " reversal" : burst ? " staggered" : ""}`);
    }
    await page.locator('.pf-model-chip').click();
    await page.waitForTimeout(1300);
    const model = await trace(["model"]);
    assert.ok(model.samples > 0 && model.worst < 1.5, `model cable attached while neighbours resize: ${JSON.stringify(model)}`);
    // Hold the hub over the plan's plug until its scheduled cable approach would have elapsed.
    // Releasing the room must play the approach, rather than fast-forwarding straight to a connected cable.
    await page.locator('[data-branch="plan"]').click();
    await page.waitForTimeout(1300);
    const held = await page.evaluate(async () => {
      document.querySelector('[data-branch="plan"]').click();
      await new Promise(requestAnimationFrame);
      const hub = document.querySelector('[data-glide="hub"]');
      const card = document.querySelector('[data-glide="plan-draft"] > .pf-card');
      const shell = card.getAnimations().find(animation => animation.id === 'pf-shell'
        && 'clipPath' in animation.effect.getKeyframes()[0]);
      hub.style.translate = '0 300px';
      await new Promise(resolve => setTimeout(resolve, 500));
      const paused = shell.playState === 'paused';
      const path = document.querySelector('.pf-cable-body[data-of="plan-draft"]');
      const frames = [1 + parseFloat(getComputedStyle(path).strokeDashoffset) / 100];
      hub.style.translate = '';
      for (const end = performance.now() + 1000; performance.now() < end;) {
        await new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve)));
        frames.push(1 + parseFloat(getComputedStyle(path).strokeDashoffset) / 100);
      }
      return { paused, partial: frames.filter(value => value > 0.03 && value < 0.97).length,
        jump: Math.max(0, ...frames.slice(1).map((value, index) => value - frames[index])), last: frames.at(-1) };
    });
    assert.ok(held.paused, `exercise the covered-plug wait: ${JSON.stringify(held)}`);
    assert.ok(held.partial >= 3 && held.jump < 0.5 && held.last > 0.99,
      `waiting must preserve the cable's growth frames: ${JSON.stringify(held)}`);
    console.log(`PASS ${theme} covered-plug wait: ${JSON.stringify(held)}`);
    assert.deepEqual(errors, []);
    console.log(`PASS ${theme}: connected through single, simultaneous, staggered births and quick reversal`);
    await page.close();
  }
} catch (error) {
  console.error(error);
  throw error;
} finally {
  await browser.close();
  await server.close();
}
