import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { buildSync } from "esbuild";

const require = createRequire(import.meta.url);
const project = fileURLToPath(new URL("../..", import.meta.url));
test("compact composer preserves drafts and connects workspace, reasoning and preview tools", () => {
  const directory = mkdtempSync(join(tmpdir(), "loom-composer-tools-"));
  try {
    buildSync({ stdin: { resolveDir: project, loader: "tsx", contents: `
      import React, {useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {Composer} from './src/components/ComposerBase';
      import {I18nProvider} from './src/i18n';
      import './src/renderer-styles';
      localStorage.setItem('loom.settings.language','en');
      window.sent = 0; window.reasoningChanges=[]; window.openedFiles=0;
      window.loom={pickFiles:async()=>['C:/fixture/notes.txt'],call:async()=>({tools:[
        {name:'read_file',description:'Read file contents',available:true},
        {name:'write_file',description:'Write file contents',available:true},
        {name:'web_search',description:'Search the web',available:true},
        {name:'skill_load',description:'Load a skill',available:true},
        {name:'browser_open',description:'Browser disabled',available:false}],
        skills:[{name:'review',description:'Review changes'}]})};
      function Fixture(){
        const [value,setValue]=useState('Check <unsafe> text');
        const [effort,setEffort]=useState('low');
        const [thread,setThread]=useState('first'); window.switchThread=()=>setThread('second');
        const reasoning={kind:'effort',value:effort,defaultValue:'low',options:[{value:'low',label:'Low'},{value:'high',label:'High'}]};
        return <Composer threadId={thread} draftValue={value} onDraftValueChange={setValue} accountAuthenticated
          workspace="C:/fixture/project" onOpenWorkspace={()=>window.openedFiles++}
          model="deepseek-flash-long-model-name" modelSnapshot={{current:{model:'deepseek-flash-long-model-name',reasoning}}}
          permissionMode="approval" onReasoningChange={async(kind,value)=>{window.reasoningChanges.push([kind,value]);setEffort(value)}}
          onSend={(_text,_attachments,tools)=>{window.sent++;window.submittedTools=tools}} onInterrupt={()=>{}}/>;
      }
      createRoot(document.getElementById('root')).render(<I18nProvider><Fixture/></I18nProvider>);
    ` }, bundle: true, platform: "browser", format: "iife", jsx: "automatic",
      loader: { ".woff": "empty", ".woff2": "empty", ".ttf": "empty", ".png": "empty", ".svg": "empty" },
      outfile: join(directory, "bundle.js"), define: { "process.env.NODE_ENV": '"production"' } });
    writeFileSync(join(directory, "main.cjs"), `
      const {app,BrowserWindow}=require('electron');const {createServer}=require('node:http');const {readFileSync}=require('node:fs');
      app.whenReady().then(async()=>{
        const server=createServer((req,res)=>{
          if(req.url==='/bundle.js'){res.setHeader('Content-Type','text/javascript');res.end(readFileSync(__dirname+'/bundle.js'));}
          else if(req.url==='/bundle.css'){res.setHeader('Content-Type','text/css');res.end(readFileSync(__dirname+'/bundle.css'));}
          else res.end('<html data-loom-theme="dark"><link rel="stylesheet" href="/bundle.css"><style>body{margin:0}#root{width:100%}</style><div id="root"></div><script src="/bundle.js"></script></html>');
        });await new Promise(r=>server.listen(0,'127.0.0.1',r));
        try{
          const win=new BrowserWindow({show:false,width:1100,height:800,webPreferences:{contextIsolation:true}});
          await win.loadURL('http://127.0.0.1:'+server.address().port+'/');
          const result=await win.webContents.executeJavaScript(\`(async()=>{
            const waitFor=async(check,label)=>{for(let i=0;i<100;i++){if(check())return;await new Promise(r=>setTimeout(r,25));}throw Error(label)};
            const button=name=>document.querySelector('[aria-label="'+name+'"]');
            await waitFor(()=>button('Preview message'),'Composer did not mount');
            button('Open workspace files').click();if(window.openedFiles!==1)throw Error('Workspace action disconnected');
            button('Attach files').click();await waitFor(()=>document.body.textContent.includes('notes.txt'),'Attachment missing');
            button('Reasoning effort').click();await waitFor(()=>document.querySelector('[role="radio"]'),'Reasoning panel missing');
            [...document.querySelectorAll('[role="radio"]')].find(b=>b.textContent.includes('High')).click();
            await waitFor(()=>window.reasoningChanges[0]?.[1]==='high','Reasoning change not applied');
            button('Preview message').click();await waitFor(()=>document.querySelector('.composer-draft-preview'),'Preview missing');
            const preview=document.querySelector('.composer-draft-preview');
            if(!preview.textContent.includes('Check <unsafe> text')||!preview.textContent.includes('notes.txt'))throw Error('Preview lost draft context');
            if(preview.querySelector('unsafe'))throw Error('Draft interpreted as HTML');
            button('Close preview').click();button('Expand editor').click();
            await waitFor(()=>document.querySelector('textarea').getBoundingClientRect().height>200,'Editor did not expand');
            if(document.querySelector('textarea').value!=='Check <unsafe> text'||window.sent!==0)throw Error('Draft mutated or sent by tools');
            button('Collapse editor').click();await waitFor(()=>document.querySelector('textarea').getBoundingClientRect().height<200,'Editor did not collapse');
            window.checkToolbar=()=>{
              const form=document.querySelector('.composer').getBoundingClientRect();
              for(const b of document.querySelectorAll('.composer-toolbar button')){
                if(b.closest('.composer-popover'))continue;
                const box=b.getBoundingClientRect();if(box.width===0)continue;
                if(box.left<form.left-1||box.right>form.right+1||box.height>31)throw Error('Toolbar overflow or oversized button '+b.outerHTML);
                if(b.classList.contains('composer-chip')&&parseFloat(getComputedStyle(b).fontSize)>12)throw Error('Toolbar label was not reduced');
              }
            };window.checkToolbar();
            button('Expand editor').click();button('Preview message').click();window.switchThread();
            await waitFor(()=>!document.querySelector('.composer-draft-preview')&&button('Expand editor'),'Thread retained draft panels');
            button('Tools & skills').click();
            await waitFor(()=>document.querySelectorAll('.composer-tools-row').length===5,'Real tool catalog did not load');
            const byText=text=>[...document.querySelectorAll('.composer-tools-popover button')].find(b=>b.textContent===text);
            byText('Select none').click();await waitFor(()=>document.body.textContent.includes('Chat only'),'Chat-only mode missing');
            if([...document.querySelectorAll('.composer-tools-row input')].some(input=>input.checked))throw Error('Select none kept tools enabled');
            const search=document.querySelector('.composer-tools-search input');
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(search,'write_file');search.dispatchEvent(new Event('input',{bubbles:true}));
            await waitFor(()=>document.querySelectorAll('.composer-tools-row').length===1,'Tool search did not filter');
            document.querySelector('.composer-tools-row input').click();
            await waitFor(()=>document.querySelector('.composer-tools-row input').checked,'Tool selection not applied');
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(search,'');search.dispatchEvent(new Event('input',{bubbles:true}));
            await waitFor(()=>document.querySelectorAll('.composer-tools-row').length===5,'Tool search did not clear');
            if(!document.querySelector('.composer-tools-row[data-unavailable="true"] input').disabled)throw Error('Disabled capability became selectable');
            document.querySelector('[role="tab"]:last-child').click();
            await waitFor(()=>document.querySelector('.composer-tools-skill'),'Skills tab missing');
            document.querySelector('.composer-tools-skill').click();
            await waitFor(()=>document.querySelector('textarea').value.includes('$review'),'Skill reference not inserted');
            button('Tools & skills').click();await waitFor(()=>document.querySelector('.composer-tools-modes'),'Selector did not reopen');
            byText('Select all').click();await waitFor(()=>document.querySelector('.composer-tools-count')?.textContent==='4','Select all included unavailable tools');
            byText('Auto').click();await waitFor(()=>!document.querySelector('.composer-tools-count'),'Auto mode did not reset selection');
            byText('Select none').click();button('Close tool selection').click();
            return 'COMPOSER_TOOLS_PASS';
          })()\`);console.log(result);
          for(const width of [680,450,360]){
            win.setContentSize(width,800);
            await win.webContents.executeJavaScript('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
            await win.webContents.executeJavaScript(\`(async()=>{
              window.checkToolbar();document.querySelector('.reasoning-chip').click();
              await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
              const box=document.querySelector('.composer-reasoning-popover').getBoundingClientRect();
              if(box.left<0||box.right>innerWidth)throw Error('Reasoning popover outside narrow window');
              document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));
            })()\`);
          }
          await win.webContents.executeJavaScript(\`(async()=>{
            document.querySelector('[aria-label="Send message"]').click();
            await new Promise(r=>setTimeout(r,50));
            if(window.sent!==1||JSON.stringify(window.submittedTools)!=='[]')throw Error('Selected tool surface not attached to send');
          })()\`);
          server.close();app.exit(0);
        }catch(error){console.error(error);server.close();app.exit(1)}
      });
    `);
    const env = { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "true" }; delete env.ELECTRON_RUN_AS_NODE;
    const result = spawnSync(require("electron"), [join(directory, "main.cjs")], { env, encoding: "utf8", windowsHide: true, timeout: 30000 });
    assert.equal(result.status, 0, `${result.error || ""}\n${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, /COMPOSER_TOOLS_PASS/);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
