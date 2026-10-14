import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
const {chromium} = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const css = ["styles.css", "components/Inspector.css", "components/runtime-controls-polish.css", "theme.css", "typography-scale.css", "components/inspector-tabs.css", "components/workspace-panels.css", "components/window-chrome.css", "components/window-rail.css"].map(p=>readFileSync(new URL(`../../src/${p}`,import.meta.url),"utf8")).join("\n");
const browser=await chromium.launch({executablePath:process.env.LOOM_CHROMIUM_PATH,headless:true});
try {
 for(const width of [280,316,360]) for(const zh of [false,true]) {
  const page=await browser.newPage({viewport:{width:1000,height:800}});
  const labels=zh?["活动","电脑","文件","终端"]:["Activity","Computer","Changes","Shell"];
  const glyph='<svg width="14" height="14"><rect width="12" height="12"/></svg>';
  await page.setContent(`<html class="loom-desktop-chrome"><style>${css} .runtime-inspector{position:absolute;right:0;top:0;width:${width}px;height:800px;display:grid}</style><aside class="inspector runtime-inspector"><header class="runtime-header"><div class="runtime-heading"><div class="runtime-title-row"><strong>${zh?'执行详情':'Activity'}</strong></div><div class="runtime-subtitle-row"><span class="runtime-caption">${zh?'工具调用与文件变更':'Tools and workspace changes'}</span><span class="runtime-health">${zh?'含失败记录':'Past errors'}</span></div></div></header><div class="runtime-tabs">${labels.map(label=>`<button class="runtime-tab"><span class="runtime-tab-content">${glyph}<span class="runtime-tab-label">${label}</span></span><span class="runtime-tab-count">99+</span></button>`).join('')}</div><div class="runtime-body"></div></aside></html>`);
  for(const label of await page.locator('.runtime-tab-label').all()) {
   const bounds=await label.evaluate(el=>{const a=el.getBoundingClientRect(),b=el.closest('button').getBoundingClientRect();return {left:a.left,right:a.right,tabLeft:b.left,tabRight:b.right,sw:el.scrollWidth,cw:el.clientWidth};});
   assert.ok(bounds.left>=bounds.tabLeft && bounds.right<=bounds.tabRight && bounds.sw<=bounds.cw+1,JSON.stringify({width,zh,bounds}));
  }
  const header=await page.locator('.runtime-subtitle-row').evaluate(el=>{const a=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();return {left:a.left,right:a.right,parentRight:b.right,top:a.top};});
  assert.ok(header.right<=header.parentRight+1 && header.top>=60);
  assert.equal(await page.locator('.runtime-caption').isVisible(), true);
  if(!zh && width===280) await page.screenshot({path:process.env.TEMP+'/loom-inspector-fit.png'});
  await page.close();
 }
 console.log('PASS: inspector labels and subtitle/status fit at 280/316/360px in Chinese and English');
} finally {await browser.close();}
