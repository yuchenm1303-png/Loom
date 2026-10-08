import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const theme of ["light", "dark"]) {
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    await page.evaluate(() => window.motionFixture.presence(true));
    await page.locator('#presence[data-motion-phase="entered"]').waitFor();
    await page.evaluate(() => {
      window.originalPresence = document.querySelector('#presence');
      window.motionFixture.presence(false);
    });
    assert.equal(await page.locator('#presence').getAttribute('inert'), '', 'logical close deactivates retained DOM');
    await page.evaluate(() => window.motionFixture.presence(true));
    assert.equal(await page.locator('#presence').getAttribute('data-motion-phase'), 'entered', 'exit reverses without teleporting to entrance');
    assert.ok(await page.evaluate(() => document.querySelector('#presence') === window.originalPresence));
    await page.waitForTimeout(280);
    assert.equal(await page.locator('#presence').count(), 1, 'old timer cannot remove reopened content');
    const swappedPhase = await page.evaluate(() => {
      window.motionFixture.presence(true, 'another-panel');
      return document.querySelector('#presence').dataset.motionPhase;
    });
    assert.equal(swappedPhase, 'entering', 'a different surface gets its own entrance');
    await page.locator('#presence[data-motion-phase="entered"]').waitFor();
    await page.evaluate(() => {
      window.motionFixture.presence(false);
      document.documentElement.dataset.loomReducedMotion = 'true';
    });
    await page.waitForFunction(() => !document.querySelector('#presence'), null, { timeout: 150 });
    await page.evaluate(() => document.documentElement.dataset.loomReducedMotion = 'false');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.evaluate(() => { window.motionFixture.presence(true); window.motionFixture.presence(false); });
    assert.equal(await page.locator('#presence').count(), 0, 'system reduced motion skips exit retention');
    await page.emulateMedia({ reducedMotion: 'no-preference' });

    const text = '## 标题\n\n' + '连续生成的内容'.repeat(6);
    await page.evaluate(text => { window.motionFixture.reset(); window.motionFixture.plain(text, true); }, text);
    await page.waitForFunction(text => document.querySelector('.markdown-body')?.textContent.replace(/\s/g, '') === text.replace(/[#\s]/g, ''), text);
    await page.waitForTimeout(280);
    await page.evaluate(() => {
      window.heading = document.querySelector('h2');
      window.headingAnimation = window.heading.getAnimations()[0];
      window.tail = document.querySelector('.stream-text-tail');
    });
    await page.evaluate(text => window.motionFixture.plain(text + '新的文字', true), text);
    await page.waitForFunction(() => document.querySelector('.markdown-body')?.textContent.endsWith('新的文字'));
    assert.ok(await page.evaluate(() => window.heading === document.querySelector('h2') && window.heading.getAnimations()[0] === window.headingAnimation), 'completed Markdown block entrance does not restart on a new provider burst');
    assert.ok(await page.evaluate(() => window.tail === document.querySelector('.stream-text-tail') && Number(getComputedStyle(window.tail).opacity) > .99), 'existing text tail stays crisp during updates');
    assert.equal(await page.locator('.markdown-body').evaluate(el => getComputedStyle(el, '::after').content), 'none', 'no extra generation symbol below prose');

    await page.evaluate(() => { window.motionFixture.reset(); window.motionFixture.lightbox(); });
    await page.locator('#preview').click();
    await page.locator('.user-message-image-lightbox[data-motion-phase="entered"]').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '关闭图片预览');
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('title')), '在文件夹中查看', 'modal tab focus wraps');
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'preview', 'closing returns focus immediately');
    assert.equal(await page.locator('.user-message-image-lightbox').getAttribute('inert'), '');
    await page.locator('#preview').click();
    assert.equal(await page.locator('.user-message-image-lightbox').getAttribute('data-motion-phase'), 'entered');
    await page.waitForTimeout(250);
    assert.equal(await page.locator('.user-message-image-lightbox').count(), 1);

    // A live send retains its timeline across server confirmation and finishes
    // before a fast response. Historical remounts never replay the launch.
    const user = { id: 'user', threadId: 'thread-1', turnId: 'turn-1', type: 'user_message', text: '你好', status: 'completed' };
    await page.evaluate(user => { window.motionFixture.reset(); window.motionFixture.turn([user], true); }, user);
    assert.equal(await page.locator('.is-sending').count(), 1);
    await page.waitForTimeout(440);
    assert.equal(await page.locator('.is-sending').count(), 0, 'send lifetime ends after its animation');
    assert.equal(await page.locator('.entry-user_message').evaluate(el => el.getAnimations().length), 0, 'removing send marker does not launch legacy entrance again');
    await page.evaluate(user => { window.motionFixture.reset(); window.motionFixture.turn([user], false); }, user);
    assert.equal(await page.locator('.entry-user_message').evaluate(el => el.getAnimations().length), 0);

    await page.evaluate(() => { window.motionFixture.reset(); window.motionFixture.card(true); });
    await page.locator('.decision-custom-toggle').click();
    await page.locator('.decision-custom textarea').fill('保留补充条件');
    await page.locator('.decision-custom-toggle').click();
    assert.equal(await page.locator('.decision-custom-presence').getAttribute('inert'), '');
    await page.locator('.decision-custom-toggle').click();
    assert.equal(await page.locator('.decision-custom textarea').inputValue(), '保留补充条件');
    await page.locator('.decision-option').first().click();
    await page.locator('.decision-error').waitFor();
    assert.equal(await page.locator('.decision-inline-receipt').count(), 0, 'failed submission preserves editable card');
    await page.evaluate(() => window.motionFixture.card(false));
    const before = (await page.locator('.decision-response').boundingBox()).height;
    await page.locator('.decision-submit').click();
    await page.locator('.decision-response.is-resolved').waitFor();
    assert.equal(await page.locator('.decision-response-fold').getAttribute('inert'), '');
    const during = (await page.locator('.decision-response').boundingBox()).height;
    assert.ok(during > 50 && during <= before + 1, 'receipt appears while the card is still folding');
    await page.locator('.decision-card').waitFor({ state: 'detached' });
    assert.ok((await page.locator('.decision-response').boundingBox()).height < 50, 'card geometry collapses to receipt');
    assert.equal(await page.locator('body').getAttribute('data-decision-calls'), '2');
  }
  // Verify IME and keyboard focus in the actual application, not an input clone.
  await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=light&motion=1`);
  await page.locator('.compact-thread-main').first().click({ position: { x: 20, y: 16 } });
  const input = page.locator('.composer textarea');
  await input.fill('输入法候选');
  await input.evaluate(el => el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true })));
  assert.equal(await page.locator('body').getAttribute('data-submitted'), null);
  assert.equal(await input.inputValue(), '输入法候选');
  await page.locator('.composer-mode-crossfade > :not(.crossfade-out) .permission-chip').click();
  await page.locator('.permission-popover[data-motion-phase="entered"]').waitFor();
  await page.locator('.permission-option').first().focus();
  await page.keyboard.press('Escape');
  assert.ok(await page.locator('.composer-mode-crossfade > :not(.crossfade-out) .permission-chip').evaluate(el => el === document.activeElement));
  assert.equal(await page.locator('.permission-popover').getAttribute('inert'), '');
  await page.locator('.permission-popover').waitFor({ state: 'detached' });
  // Exercise the real fallback when View Transitions are not available.
  await page.evaluate(() => document.startViewTransition = undefined);
  await page.locator('.project-git-bar button').first().click();
  await page.locator('.project-details-panel[data-motion-phase="entered"]').waitFor();
  await page.waitForTimeout(450);
  const panel = page.locator('.project-details-panel');
  const fallback = await page.evaluate(async () => {
    document.querySelector('.project-details-close').click();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const el = document.querySelector('.project-details-panel');
    const fade = el.getAnimations().find(a => a.transitionProperty === 'opacity');
    const frames = fade?.effect.getKeyframes();
    if (fade) { fade.pause(); fade.currentTime = fade.effect.getTiming().duration; }
    return { inert: el.inert, snapshotMode: document.documentElement.dataset.loomLayoutTransition,
      endOpacity: frames?.at(-1).opacity, opacity: Number(getComputedStyle(el).opacity) };
  });
  assert.equal(fallback.inert, true);
  assert.equal(fallback.snapshotMode, undefined);
  assert.equal(Number(fallback.endOpacity), 0, 'fallback owns a real opacity exit transition');
  assert.ok(fallback.opacity < .01, 'fallback fades to invisible before unmount');
  await panel.waitFor({ state: 'detached' });
  const moving = await page.evaluate(() => {
    document.querySelector('.thread-sidebar-triangle').click();
    const count = document.getAnimations().filter(a => a.id === 'loom-layout-anchor' && a.playState === 'running').length;
    document.documentElement.dataset.loomReducedMotion = 'true';
    return count;
  });
  assert.ok(moving > 0, 'panel toggle starts live layout movement');
  await page.waitForFunction(() => !document.getAnimations().some(a => a.id === 'loom-layout-anchor' && a.playState === 'running'));
  assert.deepEqual(errors, []);
  console.log('Runtime motion passed in both themes: stable streaming, reversible presence, reduced motion, historical/send lifetimes, preview focus, IME and popover exits.');
} finally {
  await browser.close();
}
