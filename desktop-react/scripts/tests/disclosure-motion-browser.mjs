import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || 'playwright-core');
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH });
const origin = process.env.LOOM_TEST_ORIGIN || 'http://127.0.0.1:5199';
const directory = process.env.LOOM_DISCLOSURE_RECORDINGS;
if (directory) await mkdir(directory, { recursive: true });
const base = { threadId:'thread-1', turnId:'turn-1', status:'completed' };
const items = [
  {...base,id:'user',type:'user_message',text:'检查工作区'},
  {...base,id:'tool',type:'tool_call',toolName:'search_workspace_text',arguments:{query:'login'},result:'Condition met after 5004ms.\nFound login handler.'},
];
try {
  for (const theme of ['light','dark']) {
    const page = await browser.newPage({viewport:{width:1200,height:900}, ...(directory ? {recordVideo:{dir:directory}} : {})});
    const errors=[]; page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => window.motionFixture);
    await page.evaluate(items => window.motionFixture.turn(items,false), items);
    // Start timing after the cold fixture has painted. Otherwise its first
    // style/font/layout pass can consume the entire animation sample window.
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
    });
    const check = async (trigger, selector) => {
      const frames = await page.evaluate(async ({trigger,selector}) => {
        document.querySelector(trigger).click();
        const frames=[]; const start=performance.now();
        while(performance.now()-start<460) {
          await new Promise(requestAnimationFrame);
          const el=document.querySelector(selector);
          frames.push({time:performance.now()-start,height:el?.getBoundingClientRect().height||0,opacity:el ? Number(getComputedStyle(el).opacity):0});
        }
        return frames;
      },{trigger,selector});
      const full=frames.at(-1).height;
      assert.ok(full>20, `${selector}: expanded content exists`);
      assert.ok(frames[0].height<full*.35,`${selector}: opening starts collapsed`);
      assert.ok(frames.some(f=>f.height>full*.1&&f.height<full*.9),`${selector}: opening has intermediate frames`);
      await page.locator(trigger).click(); await page.waitForTimeout(440);
      await page.locator(trigger).click(); await page.waitForTimeout(85);
      const before=await page.locator(selector).evaluate(el=>el.getBoundingClientRect().height);
      await page.locator(trigger).click(); await page.waitForTimeout(45);
      await page.locator(trigger).click(); await page.waitForTimeout(460);
      assert.ok(await page.locator(selector).evaluate(el=>el.getBoundingClientRect().height)>=before,`${selector}: interrupted exit reopens and survives stale timers`);
      await page.locator(trigger).click(); await page.waitForTimeout(440);
      await page.evaluate(()=>document.documentElement.dataset.loomReducedMotion='true');
      await page.locator(trigger).click();
      assert.ok(await page.locator(selector).evaluate(el=>el.getBoundingClientRect().height)>20,`${selector}: reduced motion opens immediately`);
      await page.evaluate(()=>document.documentElement.dataset.loomReducedMotion='false');
      if(directory) await writeFile(`${directory}/${theme}-${selector.slice(1)}.json`,JSON.stringify(frames,null,2));
    };
    await check('.turn-process-header','.turn-process-grid');
    await check('.task-flow-row','.task-flow-inline-detail-grid');
    // Groups start expanded; first collapse then exercise their first user expansion.
    await page.locator('.task-flow-group-header').click(); await page.waitForTimeout(350);
    await check('.task-flow-group-header','.task-flow-group-grid');
    await page.evaluate(items => {
      window.motionFixture.reset();
      window.motionFixture.turn(items, false);
    }, [...items, {...base,id:'answer',type:'assistant_message',text:'<think>检查页面状态与登录处理。\n\n确认输入约束。</think>检查完成。'}]);
    await check('.live-reasoning-trigger','.live-reasoning-grid');
    await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=${theme}&motion=1`);
    await page.locator('.composer').waitFor();
    for(const control of ['permission','model','sticker']) {
      const samples=await page.evaluate(async control=> {
        document.querySelector(`.${control}-chip`).click();
        const samples=[]; const start=performance.now();
        while(performance.now()-start<300) {
          await new Promise(requestAnimationFrame);
          const el=document.querySelector('.composer-popover');
          if(el) samples.push({opacity:Number(getComputedStyle(el).opacity),transform:getComputedStyle(el).transform});
        }
        return samples;
      },control);
      if(directory) await writeFile(`${directory}/${theme}-${control}.json`,JSON.stringify(samples,null,2));
      assert.ok(samples.some(s=>s.opacity>0&&s.opacity<.99),`${control}: first opening fades in`);
      assert.ok(samples.every(s=>s.transform==='none'),`${control}: text remains stationary`);
      await page.evaluate(() => {
        window.popoverExit = null;
        const observer = new MutationObserver(() => {
          const el = document.querySelector('.composer-popover[data-motion-phase="exiting"]');
          if (!el) return;
          window.popoverExit = { phase:el.dataset.motionPhase, inert:el.inert, transform:getComputedStyle(el).transform };
          observer.disconnect();
        });
        observer.observe(document.body,{attributes:true,subtree:true});
      });
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => window.popoverExit);
      const exiting = await page.evaluate(() => window.popoverExit);
      assert.equal(exiting.phase,'exiting',`${control}: closing retains content for fade`);
      assert.equal(exiting.inert,true,`${control}: closing content stops receiving input`);
      assert.equal(exiting.transform,'none',`${control}: closing text remains stationary`);
      await page.locator('.composer-popover').waitFor({state:'detached'});
    }
    assert.deepEqual(errors,[]);
    await page.close();
  }
  console.log('Disclosure first/repeated/reversed/reduced openings and composer entrances passed in both themes.');
} finally { await browser.close(); }

