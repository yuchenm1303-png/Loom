import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const base = { threadId: 'thread-1', turnId: 'turn-1', createdAt: '2026-10-07T01:00:00Z' };
const user = { ...base, id: 'user', type: 'user_message', status: 'completed', text: '检查任务' };
const message = (id, text = `进度 ${id}。\n\n` + '这段记录会平滑收纳到较早过程。'.repeat(5), status = 'completed') =>
  ({ ...base, id, type: 'assistant_message', phase: 'commentary', status, text });
const tool = (id, status = 'completed') => ({ ...base, id, type: 'tool_call', status,
  toolName: 'exec_command', arguments: { cmd: `echo ${id}` }, result: { output: `Finished ${id}` }, ok: true });
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const render = items => page.evaluate(items => window.motionFixture.turn(items, true), items);
  const slot = id => page.locator(`.process-handoff-slot[data-process-items~="${id}"]`);
  const waitFold = id => page.waitForFunction(id => document.querySelector(
    `.process-handoff-slot[data-process-items~="${id}"]`)?.dataset.handoffPhase === 'folding', id);
  const waitGone = id => page.waitForFunction(id => !document.querySelector(
    `.process-handoff-slot[data-process-items~="${id}"]`), id);
  const toggle = () => page.locator('.earlier-process-toggle').evaluate(el => el.click());
  for (const theme of ['light', 'dark']) {
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    const first = [user, message('c1'), tool('t1')];
    await render(first);
    await page.waitForTimeout(320);
    assert.ok(await page.locator('.transcript-scroll').evaluate(el => el.clientHeight > 500), 'fixture paints the real transcript inside a sized conversation viewport');
    await page.evaluate(() => window.oldMessage = document.querySelector('[data-message-id="c1"]'));
    const height = (await slot('c1').boundingBox()).height;
    await render([...first, message('c2')]);
    assert.equal(await slot('c1').getAttribute('data-handoff-phase'), 'holding');
    assert.ok(await page.evaluate(() => window.oldMessage === document.querySelector('[data-message-id="c1"]')), 'retiring prose keeps its original DOM and paint snapshot');
    assert.equal(await page.locator('.earlier-process-count').textContent(), '2 项');
    assert.equal(await page.locator('.earlier-process-history [data-message-id="c1"]').count(), 0, 'a retiring record is not duplicated inside history');
    await waitFold('c1');
    await page.waitForTimeout(110);
    const middle = await slot('c1').boundingBox();
    assert.ok(middle.height > 0 && middle.height < height, 'old height contracts before the DOM is removed');
    assert.equal(await slot('c1').getAttribute('inert'), '');
    assert.ok(await slot('c1').locator('.process-handoff-slot-inner').evaluate(el => Number(getComputedStyle(el).opacity) < .9));
    if (process.env.LOOM_EARLIER_SCREENSHOTS) await page.screenshot({ path: `${process.env.LOOM_EARLIER_SCREENSHOTS}/earlier-handoff-${theme}.png` });
    await waitGone('c1');
    await waitGone('t1');
    await toggle();
    await page.locator('.earlier-process-history[data-motion-phase="entered"]').waitFor();
    assert.equal(await page.locator('.earlier-process-history [data-message-id="c1"]').count(), 1);
    assert.equal(await page.locator('.earlier-process-history [data-message-id="c1"]').getAttribute('data-loom-message-text'), message('c1').text.trim());
    await toggle();
    await page.waitForTimeout(300);

    // Opening mid-fold reverses in place, and its old timer must not swallow it.
    const next = [...first, message('c2'), message('c3')];
    await render(next);
    await waitFold('c2');
    await page.waitForTimeout(70);
    await page.evaluate(() => window.reversing = document.querySelector('[data-message-id="c2"]'));
    await toggle();
    assert.equal(await slot('c2').getAttribute('data-handoff-phase'), 'holding');
    assert.equal(await slot('c2').getAttribute('inert'), null);
    await page.waitForTimeout(520);
    assert.ok(await page.evaluate(() => window.reversing === document.querySelector('[data-message-id="c2"]')));
    assert.equal(await page.locator('[data-message-id="c2"]').count(), 1);
    await toggle();
    await waitGone('c2');
    assert.equal(await page.locator('.earlier-process-history [data-message-id="c2"]').count(), 1);

    // Consecutive updates preserve independent retirement lifetimes.
    await render([...next, message('c4')]);
    await page.waitForTimeout(150);
    await render([...next, message('c4'), message('c5')]);
    await waitGone('c3');
    await waitGone('c4');
    assert.equal(await page.locator('.earlier-process-count').textContent(), '5 项');
    assert.equal(await slot('c5').count(), 1);

    // A mixed tool batch remains visible until its active row has settled.
    await page.evaluate(() => window.motionFixture.reset());
    const mixed = [user, message('c1'), tool('t1'), tool('t2', 'running'), message('c2')];
    await render(mixed.slice(0, -1));
    await render(mixed);
    await page.waitForTimeout(650);
    assert.equal(await slot('t1').count(), 1);
    assert.notEqual(await slot('t1').getAttribute('data-handoff-phase'), 'folding');
    assert.equal(await slot('t2').count(), 1, 'a running tool can never be folded with completed neighbours');
    await render(mixed.map(item => item.id === 't2' ? tool('t2') : item));
    await waitFold('t2');
    assert.equal(await slot('t1').getAttribute('data-handoff-phase'), 'folding');
    await waitGone('t2');

    // Finish the old text burst before beginning its retirement animation.
    await page.evaluate(() => window.motionFixture.reset());
    const long = message('burst', '最后一批文字。'.repeat(180), 'streaming');
    await render([user, long]);
    await page.waitForTimeout(50);
    await render([user, { ...long, status: 'completed' }, message('latest')]);
    await page.waitForTimeout(200);
    assert.equal(await slot('burst').getAttribute('data-handoff-phase'), 'holding');
    await page.waitForFunction(text => document.querySelector('[data-message-id="burst"] .markdown-body')?.textContent === text, long.text);
    assert.equal(await slot('burst').getAttribute('data-handoff-phase'), 'holding', 'the final glyph gets a readable hold before folding');
    await waitGone('burst');

    // Live motion preference changes finish retirement without stale timers.
    await render([user, message('latest'), message('newest')]);
    await page.evaluate(() => document.documentElement.dataset.loomReducedMotion = 'true');
    await waitGone('latest');
    assert.equal(await slot('newest').count(), 1);
    await page.waitForTimeout(500);
    assert.equal(await slot('newest').count(), 1);
    // Completion holds the finished live layout for a beat, then folds it into
    // the summary. History is never re-expanded or duplicated on the way.
    await page.evaluate(() => document.documentElement.dataset.loomReducedMotion = 'false');
    await toggle();
    await page.locator('.earlier-process-history[data-motion-phase="entered"]').waitFor();
    await page.evaluate(items => window.motionFixture.turn(items, false),
      [user, message('latest'), message('newest'), { ...message('final', '最终答复'), phase: 'final' }]);
    // One synchronous read: the hold is shorter than a few protocol round trips.
    const held = await page.evaluate(() => ({
      hold: document.querySelectorAll('.turn-process.is-settle-hold').length,
      latest: document.querySelectorAll('[data-message-id="latest"]').length,
      header: document.querySelector('.turn-process-header-shell')?.getBoundingClientRect().height,
    }));
    assert.equal(held.hold, 1, 'the finished live layout holds');
    assert.equal(held.latest, 1);
    assert.equal(held.header, 0, 'the summary line waits for the fold instead of pushing the live layout down');
    await page.waitForFunction(() => document.querySelector('.turn-process.is-settled:not(.is-open)'));
    assert.equal(await page.locator('.earlier-process-toggle').count(), 0);
    assert.equal(await page.locator('[data-message-id="latest"]').count(), 1);
    await page.evaluate(() => window.motionFixture.reset());
  }
  assert.deepEqual(errors, []);
  console.log('Earlier-process handoff passed in both themes: retained DOM, real height exit, readable final bursts, reversible expansion, consecutive batches, live tool protection and reduced motion.');
} finally { await browser.close(); }
