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
test("image cards render direct PNGs and immediately load their page in the sidebar", () => {
  const directory = mkdtempSync(join(tmpdir(), "loom-browser-card-"));
  try {
    buildSync({
      stdin: { resolveDir: project, loader: "tsx", contents: `
        import React, { useState, useEffect } from 'react';
        import { createRoot } from 'react-dom/client';
        import { BrowserActivityCards } from './src/components/BrowserActivityCards';
        import { BrowserWorkspace } from './src/components/BrowserWorkspace';
        import { I18nProvider } from './src/i18n';
        import { RightToolDock, DEFAULT_TOOL_ORDER } from './src/components/RightToolDock';
        window.commands = []; window.tabs = []; window.expectedDockCount = DEFAULT_TOOL_ORDER.length;
        window.loom = {
          readLocalImage: async () => { throw new Error('local image unavailable'); },
          embeddedBrowser: async command => {
            window.commands.push(command);
            if (command.action === 'new-tab') window.tabs.push({id:'copy', url:command.url,title:'Preview'});
            return {tabs: window.tabs, activeId: window.tabs[0]?.id || null};
          }
        };
        localStorage.setItem('loom.settings.language', 'en');
        const dockProps = {hasWorkspace:true,filesAvailable:true,browserAvailable:true,browserOpen:true,backgroundOpen:false,backgroundRunningCount:2,taskOpen:false,taskCompleted:1,taskTotal:3,taskRunning:true,agentsOpen:false,agentCount:1,reviewOpen:false,reviewCount:2,artifactsOpen:false,artifactCount:1,inspectorOpen:false,accountAuthenticated:true,
          onOpenFiles:()=>{},onToggleBrowser:()=>{},onToggleBackground:()=>{window.selectedActivity='background'},onToggleTasks:()=>{},onToggleAgents:()=>{},onToggleReview:()=>{},onToggleArtifacts:()=>{},onToggleInspector:()=>{},onOpenProfile:()=>{},onOpenAccount:()=>{},onOpenSettings:()=>{}};
        function Fixture() {
          const [target, setTarget] = useState(null);
          const [broken, setBroken] = useState(false);
          window.useBrokenImage = () => setBroken(true);
          useEffect(() => {
            const open = event => setTarget(event.detail);
            window.addEventListener('loom:browser-activity-open', open);
            return () => window.removeEventListener('loom:browser-activity-open', open);
          }, []);
          const items = [{id:'open', type:'tool_call',toolName:'browser_open',status:'completed',result:{
            browser_id:'sidebar',browser_connection:'embedded',url:location.origin + (broken ? '/missing.png' : '/artifacts/check.png'), title:'Preview'
          }}, {id:'outside',type:'tool_call',toolName:'browser_open',status:'completed',result:{browser_id:'external',browser_connection:'isolated',url:'https://outside.example/',title:'External automation'}}];
          return <><RightToolDock {...dockProps}/><BrowserActivityCards items={items} active={false} workspace="fixture" />
            {target && <BrowserWorkspace target={target} onClose={() => setTarget(null)} />}</>;
        }
        createRoot(document.getElementById('root')).render(<I18nProvider><Fixture /></I18nProvider>);
      ` },
      bundle: true, platform: "browser", format: "iife", jsx: "automatic",
      loader: { ".css": "empty" }, outfile: join(directory, "bundle.js"),
      define: { "process.env.NODE_ENV": '"production"' },
    });
    writeFileSync(join(directory, "main.cjs"), `
      const {app, BrowserWindow} = require('electron');
      const {createServer} = require('node:http');
      const {readFileSync} = require('node:fs');
      app.whenReady().then(async () => {
        const server = createServer((req,res) => {
          if(req.url === '/bundle.js') {res.setHeader('Content-Type','text/javascript');res.end(readFileSync(__dirname+'/bundle.js'));}
          else if(req.url === '/artifacts/check.png') {res.setHeader('Content-Type','image/png');res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+afoQAAAAASUVORK5CYII=','base64'));}
          else if(req.url === '/') res.end('<div id="root"></div><script src="/bundle.js"></script>');
          else {res.statusCode=404;res.end();}
        });
        await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
        try {
          const win = new BrowserWindow({show:process.platform==='linux',webPreferences:{contextIsolation:true,backgroundThrottling:false}});
          await win.loadURL('http://127.0.0.1:'+server.address().port+'/');
          const outcome = await win.webContents.executeJavaScript(\`(async () => {
            const waitFor = async (check, label) => {
              for(let i=0;i<100;i++){if(check())return;await new Promise(r=>setTimeout(r,30));}
              throw new Error(label);
            };
            await waitFor(() => document.querySelector('.browser-activity-visual img')?.naturalWidth > 0, 'Direct PNG preview failed: ' + (document.querySelector('.browser-activity-visual')?.outerHTML || document.body.innerHTML).slice(0, 600));
            if(document.querySelectorAll('.browser-activity-card').length !== 1) throw new Error('External automation created a browser card');
            document.querySelector('.browser-activity-card').click();
            await waitFor(() => window.commands.some(c => c.action === 'new-tab'), 'Card did not open page');
            if(window.commands.filter(c => c.action === 'new-tab').length !== 1) throw new Error('Duplicate page created');
            await waitFor(() => document.querySelector('.browser-session-notice'), 'Session information missing');
            if(document.body.innerText.includes('Open in new tab')) throw new Error('Extra confirmation still required');
            document.querySelector('.browser-activity-card').click();
            await waitFor(() => window.commands.some(c => c.action === 'select-tab'), 'Existing copy not reused');
            if(window.commands.filter(c => c.action === 'new-tab').length !== 1) throw new Error('Repeated click created duplicate');
            if(document.querySelectorAll('[data-tool-id]').length !== window.expectedDockCount) throw new Error('Detailed dock entries missing');
            document.querySelector('[data-tool-id="background"]').click();
            await waitFor(() => window.selectedActivity === 'background', 'Activity action not connected');
            document.querySelector('[aria-label="Page tools"]').click();
            await waitFor(() => document.querySelector('.browser-tools-menu'), 'Page tools menu missing');
            const zoom = document.querySelector('[aria-label="Zoom in"]'); zoom.click();
            await waitFor(() => window.commands.some(c => c.action === 'zoom' && c.percent === 110), 'Zoom control missing');
            [...document.querySelectorAll('.browser-tools-menu > button')].find(b => b.textContent.includes('Find in page')).click();
            await waitFor(() => document.querySelector('.browser-find input'), 'Find bar missing');
            const input = document.querySelector('.browser-find input');
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; setter.call(input,'Preview'); input.dispatchEvent(new Event('input',{bubbles:true}));
            await waitFor(() => window.commands.some(c => c.action === 'find' && c.text === 'Preview'), 'Find request not sent');
            window.useBrokenImage();
            await waitFor(() => document.querySelector('.browser-activity-placeholder')?.textContent.includes('Preview unavailable'), 'Broken image did not explain failure');
            return 'BROWSER_CARD_UI_PASS';
          })()\`);
          console.log(outcome);server.close();app.exit(0);
        } catch(error) {console.error(error);server.close();app.exit(1);}
      });
    `);
    const env = { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "true" };
    delete env.ELECTRON_RUN_AS_NODE;
    const result = spawnSync(require("electron"), [resolve(directory, "main.cjs")], {
      env, encoding: "utf8", windowsHide: true, timeout: 25000,
    });
    assert.equal(result.status, 0, `${result.error || ""}\n${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, /BROWSER_CARD_UI_PASS/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
