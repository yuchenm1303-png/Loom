const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent('<button id="target">Target</button>');
    await page.evaluate(() => {
      window.testReads = 0;
      window.testOwned = true;
      window.chrome = {
        runtime: {sendMessage: async () => {if(++testReads === 1) throw Error('worker waking'); return {tab_id:7};}},
        storage: {session: {get: async key => ({[key]:key === 'loomBrowserSessionActive' ? true : (testOwned ? [7] : [])})},
          onChanged:{addListener(){}}},
      };
    });
    await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'../extensions/browser-current-tab/browser-hud.js'),'utf8')});
    const live = () => page.evaluate(() => !!document.getElementById('loom-browser-hud-root-v2')?.shadowRoot?.getElementById('hud')?.classList.contains('live'));
    assert.equal(await live(),false,'identity failure must not claim ownership');
    await page.waitForFunction(() => document.getElementById('loom-browser-hud-root-v2')?.shadowRoot?.getElementById('hud')?.classList.contains('live'));
    await page.evaluate(() => document.getElementById('loom-browser-hud-root-v2').remove());
    await page.waitForFunction(() => document.getElementById('loom-browser-hud-root-v2')?.shadowRoot?.getElementById('hud')?.classList.contains('live'));
    assert.equal(await page.$eval('#target',el=>document.elementFromPoint(el.getBoundingClientRect().left+5,el.getBoundingClientRect().top+5)===el),true,'HUD must not intercept input');
    await page.evaluate(() => {testOwned=false;});
    await page.waitForFunction(() => !document.getElementById('loom-browser-hud-root-v2')?.shadowRoot?.getElementById('hud')?.classList.contains('live'));
    await page.evaluate(() => __loomBrowserHudRuntimeV2.dispose());
    console.log('HUD recovery: worker race, SPA removal, ownership release, click-through passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
