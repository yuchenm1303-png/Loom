// Browser regression checks + visual contact sheet, independent of the backend.
// Install Playwright locally or set LOOM_PLAYWRIGHT_MODULE to its module path.
// LOOM_PET_BROWSER optionally selects an installed browser (e.g. msedge).
import { build } from 'esbuild';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
import { tmpdir } from 'node:os';
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.LOOM_PLAYWRIGHT_MODULE).href : 'playwright');
const project = dirname(dirname(fileURLToPath(import.meta.url)));
const dir = mkdtempSync(join(tmpdir(), 'loom-pet-review-'));
const result = await build({
  stdin: { contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {LoomPet} from './src/loomPetRuntime';
    import {LoomPetArt} from './src/LoomPetArt';
    function Harness() {
      const [running,setRunning]=useState(false);
      const [approval,setApproval]=useState(false);
      const [completed,setCompleted]=useState(false);
      const [thread,setThread]=useState(1);
      window.petControls={setRunning,setApproval,setCompleted,setThread};
      return <><div className="composer-stage"><LoomPet key={thread} running={running} approval={approval} completed={completed}/><div className="composer"><input aria-label="Message"/></div></div>
        <div className="art-review">{[0,1,2].map(pose=><div key={pose}><LoomPetArt pose={pose}/><small>Tail pose {pose}</small></div>)}</div>
        <div className="dark-review"><LoomPetArt/></div></>;
    }
    createRoot(document.getElementById('root')).render(<Harness/>);
  `, resolveDir: project, loader: 'tsx' },
  bundle: true, write: false, outfile: join(dir, 'review.js'), jsx: 'automatic',
});
const js = result.outputFiles.find(f => f.path.endsWith('.js')).text;
const css = result.outputFiles.find(f => f.path.endsWith('.css')).text;
writeFileSync(join(dir,'review.html'), `<!doctype html><html><head><meta charset="UTF-8"><style>${css}
  body{margin:0;background:#f7f7fa;font-family:Arial;color:#38314d;padding:50px;--content-width:500px}
  .composer-stage{margin-top:80px;width:500px}.composer{background:white;border:1px solid #c7bbe9;border-radius:18px;padding:20px;height:70px}
  input{border:0;outline:0;background:transparent}.art-review{display:flex;gap:30px;margin-top:40px}
  .art-review .loom-pet-svg,.dark-review .loom-pet-svg{width:256px;height:256px}
  small{display:block;text-align:center;padding-top:8px}.dark-review{background:#181720;margin-top:25px;width:256px;padding:20px;border-radius:15px}
  .sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
  </style></head><body><div id="root"></div><script>${js}</script></body></html>`);
console.log(join(dir,'review.html'));
const browser = await chromium.launch({ headless: true,
  ...(process.env.LOOM_PET_BROWSER ? {channel: process.env.LOOM_PET_BROWSER} : {}) });
try {
  const page = await browser.newPage({ viewport: {width:1000, height:840} });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install();
  await page.goto(pathToFileURL(join(dir, 'review.html')).href);
  const pet = page.locator('button.loom-pet');
  await pet.waitFor();
  await page.screenshot({path:join(dir, 'art-review.png')});
  await page.locator('.art-review .loom-pet-svg').first().screenshot({path:join(dir, 'pet-preview.png')});
  const state = async value => {
    await page.waitForFunction(value => document.querySelector('.loom-pet')?.dataset.state === value, value);
    assert.equal(await pet.getAttribute('data-state'), value);
  };
  const flag = async name => pet.evaluate((el,name)=>el.classList.contains(name),name);
  await state('idle');
  await page.getByRole('textbox').focus(); await state('listening');
  await page.evaluate(()=>petControls.setRunning(true)); await state('working');
  await page.evaluate(()=>petControls.setApproval(true)); await state('approval');
  assert.equal(await page.locator('.loom-pet-approval-mark').first().evaluate(e=>getComputedStyle(e).opacity), '1');
  await page.evaluate(()=>{petControls.setApproval(false);petControls.setRunning(false);});
  await state('listening');
  assert.equal(await flag('is-celebrating'), false, 'cancelled/failed work must not celebrate');
  await page.evaluate(()=>petControls.setRunning(true)); await state('working');
  await page.evaluate(()=>{petControls.setRunning(false);petControls.setCompleted(true);});
  await state('listening');
  assert.equal(await flag('is-celebrating'), true);
  await page.clock.runFor(1200);
  assert.equal(await flag('is-celebrating'), false);
  await pet.click(); assert.equal(await flag('is-playful'), true);
  await page.clock.runFor(900); assert.equal(await flag('is-playful'), false);
  await page.clock.runFor(45000); await state('sleeping');
  await page.evaluate(()=>{document.documentElement.dataset.loomReducedMotion='true'});
  await page.waitForFunction(()=>document.querySelector('.loom-pet').dataset.reducedMotion==='true');
  assert.equal(await pet.evaluate(e=>getComputedStyle(e).animationName), 'none');
  assert.equal(await page.locator('.loom-pet-sleep-mark').first().evaluate(e=>getComputedStyle(e).opacity), '0.8');
  await pet.click(); await state('idle');
  // Native button keyboard interaction and composer focus both remain usable.
  await pet.press('Enter'); assert.equal(await flag('is-playful'), true);
  await page.getByRole('textbox').focus(); await state('listening');
  await page.clock.runFor(60000); await state('listening');
  await page.evaluate(()=>petControls.setRunning(true)); await state('working');
  await page.clock.runFor(60000); await state('working');
  await page.evaluate(()=>{petControls.setThread(2);petControls.setRunning(false)});
  await state('listening');
  assert.equal(await flag('is-celebrating'), false, 'switching threads must reset the pet');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.evaluate(()=>delete document.documentElement.dataset.loomReducedMotion);
  await page.waitForFunction(()=>document.querySelector('.loom-pet').dataset.reducedMotion==='true');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForFunction(()=>document.querySelector('.loom-pet').dataset.reducedMotion==='false');
  // At rest, explicit tail frames stay fixed at 1:1 and fit a narrow composer.
  await page.setViewportSize({width:375, height:800});
  await page.evaluate(()=>document.querySelector('.composer-stage').style.width='275px');
  const box = await pet.boundingBox();
  assert.equal(box.width,64); assert.equal(box.height,64);
  assert.ok(box.x >= 0 && box.x + box.width <= 375);
  assert.deepEqual(errors, []);
  console.log('PASS: state priority, completion/cancellation, mouse/keyboard interaction, sleep/wake, focus/busy sleep prevention, thread reset, reduced motion, narrow layout.');
} finally { await browser.close(); }
