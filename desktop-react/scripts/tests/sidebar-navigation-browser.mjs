import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const theme of ['light', 'dark']) {
    await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=${theme}&motion=1&navigation=1`);
    await page.locator('.markdown-body').filter({ hasText: 'Loaded visual-0' }).waitFor();
    const row = id => page.locator(`.compact-thread-row[data-thread-id="visual-${id}"]`);
    const click = async id => page.evaluate(id => {
      const start = performance.now();
      document.querySelector(`.compact-thread-row[data-thread-id="visual-${id}"] .compact-thread-main`).click();
      return new Promise(resolve => requestAnimationFrame(() => resolve({
        elapsed: performance.now() - start,
        selected: document.querySelector('.compact-thread-row.active')?.dataset.threadId,
        title: document.querySelector('.thread-header')?.textContent,
        switching: document.querySelector('.workspace').classList.contains('is-thread-switching'),
        pillAnimations: document.querySelector('.compact-thread-row.active .thread-selection-pill')?.getAnimations().length,
      })));
    }, id);
    await page.evaluate(() => { for (const id of ['visual-1', 'visual-2', 'visual-4', 'visual-5']) window.navigationFixture.hold(id); });
    const titles = await Promise.all([1, 2].map(id => row(id).locator('.compact-thread-title').textContent()));
    let selection = await click(1);
    assert.equal(selection.selected, 'visual-1');
    assert.ok(selection.elapsed < 200, `first-frame selection took ${selection.elapsed}ms`);
    assert.ok(selection.title.includes(titles[0]), 'header identifies the newly selected thread before it loads');
    assert.equal(selection.switching, true);
    assert.ok(selection.pillAnimations > 0, 'pill moves on the first frame, without waiting for thread/read');
    assert.equal(await page.locator('.thread-agent-button').isDisabled(), true, 'old transcript actions cannot target the previous thread under the new title');
    assert.equal(await page.locator('.thread-sidebar-triangle').isEnabled(), true, 'sidebar navigation remains available while loading');
    await page.waitForFunction(() => window.navigationFixture.pending.has('visual-1'));
    selection = await click(2);
    assert.equal(selection.selected, 'visual-2', 'second click wins while first request is still pending');
    assert.ok(selection.title.includes(titles[1]));
    assert.ok(selection.elapsed < 200);
    await page.waitForFunction(() => window.navigationFixture.pending.has('visual-2'));
    // Finish the second request while the first is still blocked.
    await page.evaluate(() => window.navigationFixture.release('visual-2'));
    await page.locator('.markdown-body').filter({ hasText: 'Loaded visual-2' }).waitFor();
    assert.ok(await page.evaluate(() => window.navigationFixture.pending.has('visual-1')));
    await page.evaluate(() => window.navigationFixture.release('visual-1'));
    await page.waitForTimeout(100);
    assert.equal(await row(2).getAttribute('class').then(value => value.includes('active')), true, 'late previous response cannot take selection back');
    assert.equal(await page.locator('.markdown-body').filter({ hasText: 'Loaded visual-1' }).count(), 0);
    await page.waitForTimeout(440);
    const pill = row(2).locator('.thread-selection-pill');
    const geometry = await pill.boundingBox();
    const bounds = await row(2).boundingBox();
    assert.ok(Math.abs(geometry.y - bounds.y - 1) < 1 && Math.abs(geometry.height - bounds.height + 2) < 1, 'pill lands exactly inside its row');
    assert.equal(await row(2).evaluate(el => getComputedStyle(el).transform), 'none', 'titles never travel or stretch');
    if (theme === 'light') assert.ok((await pill.evaluate(el => getComputedStyle(el).backgroundImage)).includes('linear-gradient'));
    if (process.env.LOOM_SIDEBAR_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_SIDEBAR_SCREENSHOTS}/sidebar-${theme}.png` });

    // Return to the loaded thread cancels a pending switch immediately.
    await click(4);
    await page.waitForFunction(() => window.navigationFixture.pending.has('visual-4'));
    selection = await click(2);
    assert.equal(selection.selected, 'visual-2');
    assert.equal(selection.switching, false);
    await page.evaluate(() => window.navigationFixture.fail('visual-4'));
    await page.waitForTimeout(100);
    assert.equal(await page.locator('.sidebar-notice').count(), 0, 'abandoned failure does not show an error for the current thread');
    await click(5);
    await page.waitForFunction(() => window.navigationFixture.pending.has('visual-5'));
    await page.evaluate(() => window.navigationFixture.fail('visual-5'));
    await page.getByText('Read failed: visual-5', { exact: true }).waitFor();
    assert.ok((await row(2).getAttribute('class')).includes('active'), 'current read failure restores last loaded selection');
    await click(3);
    await page.locator('.markdown-body').filter({ hasText: 'Loaded visual-3' }).waitFor();
    await page.evaluate(() => document.documentElement.dataset.loomReducedMotion = 'true');
    await click(2);
    assert.equal(await row(2).locator('.thread-selection-pill').evaluate(el => el.getAnimations().length), 0);
    // Scrolling and narrow layout keep the highlight attached to the selected row.
    await page.setViewportSize({ width: 760, height: 620 });
    await page.locator('.compact-thread-scroll').evaluate(el => el.scrollTop = 50);
    assert.ok(Math.abs((await pill.boundingBox()).y - (await row(2).boundingBox()).y - 1) < 1);
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  assert.deepEqual(errors, []);
  console.log('Sidebar selection and latest-click navigation passed in both themes: immediate movement, delayed/out-of-order reads, switch-back, failures, reduced motion and scrolling.');
} finally { await browser.close(); }
