import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { buildSync } from "esbuild";
const require = createRequire(import.meta.url);
const project = fileURLToPath(new URL("../..", import.meta.url));
test("native browser keeps its automation HUD inside the consented page and out of captures", () => {
  const directory = mkdtempSync(join(tmpdir(), "loom-browser-native-"));
  try {
    buildSync({ entryPoints: [join(project,"electron/embeddedBrowser.ts")], bundle: true, platform: "node", format: "cjs", external: ["electron"], outfile: join(directory,"native.cjs") });
    writeFileSync(join(directory,"main.cjs"), `
      const {app,BrowserWindow,nativeImage}=require('electron');
      const {readFileSync}=require('node:fs');
      const {createServer}=require('node:http');
      const assert=require('node:assert/strict');
      const {EmbeddedBrowserManager}=require('./native.cjs');
      app.setPath('userData',__dirname+'/profile');
      app.whenReady().then(async()=>{
        const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<title>Native preview</title><h1>Preview workspace</h1><p>Preview workspace</p><button style="position:absolute;left:100px;top:180px;width:100px;height:36px" onclick="window.clicked=(window.clicked||0)+1">Test click</button><input style="position:absolute;left:100px;top:240px" />');});
        await new Promise(r=>server.listen(0,'127.0.0.1',r));
        const win=new BrowserWindow({width:900,height:700,show:false});
        const manager=new EmbeddedBrowserManager(win,()=>readFileSync(${JSON.stringify(resolve(project,"../extensions/browser-current-tab/browser-hud.js"))},'utf8'));
        const active=()=>{const state=manager.snapshot();return state.tabs.find(t=>t.id===state.activeId)};
        const waitFor=async(fn)=>{for(let i=0;i<150;i++){if(await fn())return;await new Promise(r=>setTimeout(r,30));}throw Error('native state timed out '+JSON.stringify(active()))};
        try {
          await manager.command({action:'new-tab',url:'http://127.0.0.1:'+server.address().port+'/'});
          await manager.command({action:'bounds',bounds:{x:0,y:0,width:800,height:600}});
          await waitFor(()=>!active().loading&&active().title==='Native preview');
          await manager.command({action:'zoom',percent:120});assert.equal(active().zoomPercent,120);
          await assert.rejects(manager.command({action:'zoom',percent:0}),/between/);
          await new Promise(r=>setTimeout(r,350));await manager.command({action:'find',text:'Preview'});await waitFor(()=>active().findResult?.matches===2);
          const first = active().findResult.activeMatchOrdinal;await manager.command({action:'find',text:'Preview',next:true});await waitFor(()=>active().findResult?.activeMatchOrdinal !== first);
          await manager.command({action:'stop-find'});assert.equal(active().findResult,undefined);
          assert.equal(active().aiAllowed,false);
          const page=win.contentView.children.find(view=>view.webContents?.getURL().startsWith('http')).webContents;
          const hudState=()=>page.executeJavaScript("(() => {const host=document.getElementById('loom-browser-hud-root-v2');const root=host?.shadowRoot;return {exists:!!host,live:!!root?.querySelector('#hud.live'),clicking:!!root?.querySelector('.cursor.clicking'),point:root?.querySelector('#point')?.textContent,source:root?.querySelector('#source')?.textContent,pointer:host?getComputedStyle(host).pointerEvents:'',bounds:host?{width:host.getBoundingClientRect().width,height:host.getBoundingClientRect().height}:null,viewport:{width:innerWidth,height:innerHeight}}})()");
          const args={session_id:'test-owner',tab_id:active().id};
          await assert.rejects(manager.agentCommand('click',{...args,index:0}),/Enable AI/);
          assert.equal((await hudState()).exists,false);
          await manager.command({action:'allow-ai',tabId:active().id,enabled:true});
          await manager.agentCommand('state',args);
          // Hidden native windows do not receive OS input or produce compositor images.
          // Observe the native boundary while exercising the real page, HUD and authorization.
          const inputs=[];page.sendInputEvent=event=>inputs.push(event);
          await manager.agentCommand('click',{...args,index:0});
          assert.ok(inputs.some(event=>event.type==='mouseDown'));
          assert.equal(await page.executeJavaScript('document.elementFromPoint(150,198).tagName'),'BUTTON');
          const hud=await hudState();assert.equal(hud.live,true);assert.equal(hud.clicking,true);const [x,y]=hud.point.split(',').map(Number);assert.ok(Math.abs(x-150)<=1);assert.ok(Math.abs(y-198)<=1);
          assert.equal(hud.source,'内置浏览器 · DOM');assert.equal(hud.pointer,'none');assert.ok(Math.abs(hud.bounds.width-hud.viewport.width)<1);assert.ok(Math.abs(hud.bounds.height-hud.viewport.height)<1);
          assert.equal(await page.executeJavaScript('typeof globalThis.__loomBrowserHudRuntimeV2'),'undefined');
          assert.equal((await manager.agentCommand('state',args)).dom.includes('Loom 正在控制浏览器'),false);
          let captures=0;page.capturePage=async()=>{
            captures++;
            assert.equal(await page.executeJavaScript('document.getElementById("loom-browser-hud-root-v2").style.display'),'none');
            return nativeImage.createFromBitmap(Buffer.from([255,255,255,255]),{width:1,height:1});
          };
          const shot=await manager.agentCommand('screenshot',args);
          const pixels=nativeImage.createFromBuffer(Buffer.from(shot.png_base64,'base64')).toBitmap();
          assert.equal(pixels[0],255);assert.equal(pixels[1],255);assert.equal(pixels[2],255);
          await manager.command({action:'copy-screenshot'});assert.equal(captures,2);
          page.capturePage=async()=>{throw Error('capture unavailable')};
          await assert.rejects(manager.agentCommand('screenshot',args),/capture unavailable/);
          assert.equal(await page.executeJavaScript('document.getElementById("loom-browser-hud-root-v2").style.display'),'');
          await assert.rejects(manager.agentCommand('click',{...args,session_id:'another-owner',index:0}),/another task/);
          await manager.agentCommand('type_text',{...args,index:1,text:'private-value',clear:true});
          assert.equal(await page.executeJavaScript('document.querySelector("input").value'),'private-value');
          assert.equal(await page.executeJavaScript('document.getElementById("loom-browser-hud-root-v2").shadowRoot.textContent.includes("private-value")'),false);
          await manager.agentCommand('finish_turn',args);assert.equal((await hudState()).live,false);
          await manager.agentCommand('navigate',{...args,url:'http://127.0.0.1:'+server.address().port+'/next'});
          assert.equal((await hudState()).live,true);
          await manager.command({action:'allow-ai',tabId:active().id,enabled:false});assert.equal((await hudState()).live,false);
          console.log('NATIVE_BROWSER_PASS');manager.dispose();win.destroy();server.close();app.exit(0);
        }catch(error){console.error(error);manager.dispose();win.destroy();server.close();app.exit(1);}
      });
    `);
    const env={...process.env,ELECTRON_DISABLE_SECURITY_WARNINGS:"true"};delete env.ELECTRON_RUN_AS_NODE;
    const result=spawnSync(require("electron"),[resolve(directory,"main.cjs")],{env,encoding:"utf8",windowsHide:true,timeout:20000});
    assert.equal(result.status,0,`${result.error||""}\n${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout,/NATIVE_BROWSER_PASS/);
  }finally{rmSync(directory,{recursive:true,force:true});}
});



