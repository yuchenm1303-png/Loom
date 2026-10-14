import assert from "node:assert/strict";
import {build} from "esbuild";
const {chromium}=await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const fixture=await build({stdin:{resolveDir:process.cwd(),loader:"tsx",contents:`
import React,{useLayoutEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {useEarlierProcessHandoff} from './src/components/useEarlierProcessHandoff';
const old={id:'old',type:'assistant_message',status:'completed',text:'Earlier work'};
const latest={id:'latest',type:'assistant_message',status:'completed',text:'Current work'};
const items=[old,latest], progress={earlier:[old],current:[latest]}, pending=new Set();
const groups=items=>items.map(item=>[item.id]);
window.retirementTrace=[];
function App(){
 const handoff=useEarlierProcessHandoff(items,progress,true,false,pending,groups);
 useLayoutEffect(()=>{
  const slot=document.querySelector('[data-process-items="old"]');
  if(slot) slot.getBoundingClientRect=()=>({bottom:window.returnedToViewport?100:-1});
  window.retirementTrace.push(handoff.folding.has('old')?'folding':handoff.retained.has('old')?'holding':'retired');
 });
 return <div className="transcript-scroll" data-following="true"><section ref={handoff.rootRef}>
 {handoff.current.map(item=><div key={item.id} data-process-items={item.id} data-phase={handoff.folding.has(item.id)?'folding':'holding'} style={{height:80}}>{item.text}</div>)}
 </section></div>;
}
createRoot(document.getElementById('root')).render(<App/>);
`},bundle:true,write:false,format:"iife",platform:"browser"});
const browser=await chromium.launch({executablePath:process.env.LOOM_CHROMIUM_PATH,headless:true});
try {
 for(const manual of [false,true]) {
  const page=await browser.newPage();
  await page.setContent('<style>.transcript-scroll{height:300px;overflow:auto}</style><div id="root"></div>');
  await page.addScriptTag({content:fixture.outputFiles[0].text});
  await page.waitForFunction(()=>document.querySelector('[data-process-items="old"]')?.dataset.phase==='folding');
  await page.evaluate(manual=>{
   window.returnedToViewport=true;
   const scroller=document.querySelector('.transcript-scroll');
   if(manual) scroller.dataset.following='false';
   scroller.dispatchEvent(new Event('scroll'));
  },manual);
  await page.waitForTimeout(500);
  const trace=await page.evaluate(()=>window.retirementTrace);
  const afterStart=trace.slice(trace.indexOf('folding'));
  if(manual) {
   assert.ok(afterStart.includes('holding'),'explicit reader detachment reverses retirement');
   assert.equal(await page.locator('[data-process-items="old"]').count(),1);
  } else {
   assert.ok(!afterStart.includes('holding'),`layout contraction reversed retirement: ${trace}`);
   assert.equal(await page.locator('[data-process-items="old"]').count(),0,'automatic retirement finishes despite geometry returning to viewport');
  }
  await page.close();
 }
 console.log('PASS: fold-induced viewport changes cannot reverse retirement; explicit reader detachment still can');
} finally {await browser.close();}
