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
test("native embedded pages support zoom and find without granting AI permission", () => {
  const directory = mkdtempSync(join(tmpdir(), "loom-browser-native-"));
  try {
    buildSync({ entryPoints: [join(project,"electron/embeddedBrowser.ts")], bundle: true, platform: "node", format: "cjs", external: ["electron"], outfile: join(directory,"native.cjs") });
    writeFileSync(join(directory,"main.cjs"), `
      const {app,BrowserWindow}=require('electron');
      const {createServer}=require('node:http');
      const assert=require('node:assert/strict');
      const {EmbeddedBrowserManager}=require('./native.cjs');
      app.setPath('userData',__dirname+'/profile');
      app.whenReady().then(async()=>{
        const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<title>Native preview</title><h1>Preview workspace</h1><p>Preview workspace</p>');});
        await new Promise(r=>server.listen(0,'127.0.0.1',r));
        const win=new BrowserWindow({width:900,height:700,show:false});
        const manager=new EmbeddedBrowserManager(win);
        const active=()=>{const state=manager.snapshot();return state.tabs.find(t=>t.id===state.activeId)};
        const waitFor=async(fn)=>{for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,30));}throw Error('native state timed out '+JSON.stringify(active()))};
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



