import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5198";
try {
  for (const scale of [1, 1.5]) {
    const context = await browser.newContext({ viewport: { width: 500, height: 160 }, deviceScaleFactor: scale });
    const page = await context.newPage();
    for (const theme of ["dark", "light"]) {
      await page.goto(`${origin}/scripts/fixtures/roll-text.html?theme=${theme}`);
      await page.waitForFunction(() => window.rollFixture);
      for (const text of ["Preparing Conversation…", "正在整理对话…", "Thinking…"]) {
        await page.evaluate(text => {
          window.rollFixture.show(text);
          for (const animation of document.getAnimations()) {
            animation.pause();
            animation.currentTime = 400;
          }
        }, text);
        assert.equal(await page.locator('.roll > .sr-only').textContent(), text);
        const before = await page.screenshot();
        // At 400ms every outgoing character is transparent (90ms exit plus
        // at most 280ms stagger), while incoming characters are still rolling.
        const outgoing = page.locator('.roll-out');
        assert.equal(await outgoing.count(), 1);
        await outgoing.evaluate(el => { el.style.display = 'none'; });
        assert.ok((await page.screenshot()).equals(before),
          `${theme} at ${scale}x: transparent retiring text must not paint ghost glyphs`);
        await outgoing.evaluate(el => { el.style.display = ''; });
      }
      await page.waitForFunction(() => !document.querySelector('.roll-out'));
      assert.equal(await page.locator('.roll-line').textContent(), 'Thinking…');
      assert.match(await page.locator('.roll-line').evaluate(el => getComputedStyle(el).backgroundImage), /linear-gradient/);
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.evaluate(() => window.rollFixture.show('正在思考…'));
      assert.equal(await page.locator('.roll-out').count(), 0);
      assert.equal(await page.locator('.roll-line').evaluate(el => el.getAnimations().length), 0);
      await page.emulateMedia({ reducedMotion: 'no-preference', forcedColors: 'active' });
      assert.equal(await page.locator('.roll-line').evaluate(el => getComputedStyle(el).backgroundImage), 'none');
      await page.emulateMedia({ forcedColors: 'none' });
    }
    await context.close();
  }
  console.log('PASS: switching labels paint no retired-text ghosts at 1x/1.5x, in both themes; reduced motion and forced colours preserved');
} finally {
  await browser.close();
}
