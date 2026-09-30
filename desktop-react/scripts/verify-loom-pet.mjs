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
    import './src/components/composer.css';
    import './src/components/composer-stability.css';
    function Harness() {
      const [running,setRunning]=useState(false);
      const [approval,setApproval]=useState(false);
      const [completed,setCompleted]=useState(false);
      const [thread,setThread]=useState(1);
      window.petControls={setRunning,setApproval,setCompleted,setThread};
      return <><div className="workspace"><div className="approval-card" hidden={!approval}><button onClick={()=>window.approved=true}>Approve</button></div>
        <div className="composer-stage"><LoomPet key={thread} running={running} approval={approval} completed={completed}/><div key={running?'steering':'idle'} className={'composer-wrap '+(running?'is-working':'')}><form className="composer"><textarea aria-label="Message"/><div className="composer-toolbar">Send</div></form><div className="composer-hint">Enter to send</div></div></div></div>
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
  *{box-sizing:border-box}.composer-stage{margin-top:80px;width:500px}.composer{position:relative;width:min(var(--content-width),100%);margin:0 auto;padding:8px 9px;border:1px solid #c7bbe9;background:white;border-radius:16px}
  textarea{border:0;outline:0;background:transparent;resize:none;width:100%;height:36px}.art-review{display:flex;gap:30px;margin-top:40px}
  .composer-wrap.is-working{padding:20px 18px 34px}
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
  const reaction = async value => {
    await page.waitForFunction(value => document.querySelector('.loom-pet')?.dataset.reaction === value, value);
  };
  const floor = async () => {
    const gap = await pet.evaluate(el => {
      const border = el.closest('.composer-stage').querySelector('.composer').getBoundingClientRect();
      const box = el.getBoundingClientRect();
      return border.top - (box.top + box.height * 62 / 64);
    });
    assert.ok(gap >= -0.1 && gap < 1.5, `feet must land on the composer border: gap ${gap}`);
  };
  await page.waitForFunction(()=>document.querySelector('.loom-pet').dataset.anchored==='true');
  await state('idle');
  await floor();
  await page.getByRole('textbox').focus(); await state('listening');
  await page.evaluate(()=>petControls.setRunning(true)); await state('working');
  await floor();
  await page.evaluate(()=>petControls.setApproval(true)); await state('approval');
  assert.equal(await page.locator('.loom-pet-approval-mark').first().evaluate(e=>getComputedStyle(e).opacity), '1');
  await pet.click();
  assert.equal(await page.locator('.approval-card').evaluate(el=>el===document.activeElement),true);
  assert.equal(await page.evaluate(()=>Boolean(window.approved)),false,'locating approval must never approve it');
  await page.getByRole('textbox').focus();
  await page.evaluate(()=>{petControls.setApproval(false);petControls.setRunning(false);});
  await state('idle'); await floor();
  assert.equal(await flag('is-celebrating'), false, 'cancelled/failed work must not celebrate');
  await page.evaluate(()=>petControls.setRunning(true)); await state('working');
  await page.evaluate(()=>{petControls.setRunning(false);petControls.setCompleted(true);});
  await state('idle');
  assert.equal(await flag('is-celebrating'), true);
  await page.clock.runFor(1200);
  assert.equal(await flag('is-celebrating'), false);
  await page.getByRole('textbox').focus(); await state('listening');
  // Three distinct hit zones, typing attention, and stroking all keep text focus.
  await pet.click({position:{x:20,y:32}}); await reaction('petting');
  assert.equal(await page.getByRole('textbox').evaluate(el=>document.activeElement===el),true);
  await page.clock.runFor(180);
  assert.equal(await page.locator('.pet-heart-one').evaluate(el=>getComputedStyle(el).animationName),'loom-pet-heart');
  await page.locator('.pet-heart-one').evaluate(el=>el.getAnimations().forEach(animation=>{animation.currentTime=300;animation.pause();}));
  await page.screenshot({path:join(dir,'petting-review.png')});
  await page.clock.runFor(1500); await reaction('none');
  await pet.click({position:{x:52,y:45}}); await reaction('tickle');
  await page.clock.runFor(1300);
  await pet.click({position:{x:28,y:54}}); await reaction('hop');
  const emote = await page.locator('.loom-pet-emote').elementHandle();
  await pet.evaluate(el=>el.getAnimations().forEach(animation=>{animation.currentTime=600;}));
  await pet.evaluate(el=>el.click()); // A repeated keyboard-style tap of the same kind.
  await page.waitForFunction(el=>!el.isConnected,emote);
  assert.ok(await pet.evaluate(el=>el.getAnimations().every(animation=>animation.currentTime<200)), 'same gesture must replay');
  await page.clock.runFor(1100);
  await page.getByRole('textbox').fill('Hello Loom');
  assert.equal(await pet.getAttribute('data-typing'),'true');
  await page.clock.runFor(900);
  assert.equal(await pet.getAttribute('data-typing'),'false');
  const petBox = await pet.boundingBox();
  for (const x of [14,22,14,22,14,22]) {
    await page.mouse.move(petBox.x+x,petBox.y+30);
    await page.clock.runFor(35);
  }
  await reaction('petting');
  await page.clock.runFor(1800);
  await pet.click({position:{x:28,y:54}});
  await page.clock.runFor(200);
  await pet.click({position:{x:28,y:54}});
  await page.clock.runFor(200);
  await pet.click({position:{x:28,y:54}}); await reaction('delight');
  await page.clock.runFor(1600);
  const beforeDrag = await pet.boundingBox();
  await page.mouse.move(beforeDrag.x+28,beforeDrag.y+54);
  await page.mouse.down();
  await page.mouse.move(beforeDrag.x-120,beforeDrag.y+54,{steps:8});
  assert.equal(await pet.getAttribute('data-dragging'),'true');
  await page.mouse.up();
  assert.equal(await pet.getAttribute('data-dragging'),'false');
  assert.ok((await pet.boundingBox()).x < beforeDrag.x-90,'drag should reposition along the border');
  await reaction('hello');
  assert.equal(await page.getByRole('textbox').evaluate(el=>el===document.activeElement),true);
  await page.clock.runFor(800);
  await floor();
  await pet.focus();
  const beforeArrow = (await pet.boundingBox()).x;
  await pet.press('ArrowLeft');
  assert.equal((await pet.boundingBox()).x,beforeArrow-4);
  // Keyboard focus arms inactivity; mouse petting deliberately preserves editing.
  await pet.focus(); await state('idle');
  await page.clock.runFor(45000); await state('sleeping');
  await page.evaluate(()=>{document.documentElement.dataset.loomReducedMotion='true'});
  await page.waitForFunction(()=>document.querySelector('.loom-pet').dataset.reducedMotion==='true');
  assert.equal(await pet.evaluate(e=>getComputedStyle(e).animationName), 'none');
  assert.equal(await page.locator('.loom-pet-sleep-mark').first().evaluate(e=>getComputedStyle(e).opacity), '0.8');
  await pet.click(); await state('idle'); await reaction('wake');
  // Native button keyboard interaction and composer focus both remain usable.
  await page.clock.runFor(1200);
  await pet.press('Enter'); await reaction('hop');
  await page.getByRole('textbox').focus(); await state('listening');
  await page.clock.runFor(60000); await state('listening');
  await page.evaluate(()=>petControls.setRunning(true)); await state('working');
  await page.clock.runFor(60000); await state('working');
  await page.evaluate(()=>{petControls.setThread(2);petControls.setRunning(false)});
  await state('idle');
  assert.equal(await flag('is-celebrating'), false, 'switching threads must reset the pet');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.evaluate(()=>delete document.documentElement.dataset.loomReducedMotion);
  await page.waitForFunction(()=>document.querySelector('.loom-pet').dataset.reducedMotion==='true');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForFunction(()=>document.querySelector('.loom-pet').dataset.reducedMotion==='false');
  await page.evaluate(()=>document.documentElement.dataset.loomReducedMotion='true');
  await page.waitForFunction(()=>document.querySelector('.loom-pet').dataset.reducedMotion==='true');
  await page.clock.runFor(1600);
  // Actual wrapper padding and footer hints reproduce the previous misalignment.
  await floor();
  await page.evaluate(()=>document.querySelector('.composer-wrap').style.padding='27px 38px 34px');
  await page.clock.runFor(100); await floor();
  await page.getByRole('textbox').evaluate(el=>el.style.height='110px');
  await page.clock.runFor(100); await floor();
  // At rest, explicit tail frames stay fixed at 1:1 and fit a narrow composer.
  await page.setViewportSize({width:375, height:800});
  await page.evaluate(()=>document.querySelector('.composer-stage').style.width='275px');
  const box = await pet.boundingBox();
  assert.equal(box.width,64); assert.equal(box.height,64);
  assert.ok(box.x >= 0 && box.x + box.width <= 375);
  await floor();
  await page.evaluate(()=>document.querySelector('.workspace').style.zoom='1.25');
  await page.clock.runFor(100); await floor();
  assert.deepEqual(errors, []);
  console.log('PASS: border anchoring with real wrapper padding/height/zoom; head petting, strokes, tail tickling, body hops, click combo, typing, drag/keyboard repositioning, preserved editing focus, approval navigation without approval, sleep/wake, task states, reduced motion, narrow layout.');
} finally { await browser.close(); }
