import { BrowserWindow, screen } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const HIDE_AFTER_MS = 220;

let hudWindow: BrowserWindow | null = null;
let lastBounds = { x: 0, y: 0, width: 1440, height: 900 };
let pendingPayload: Record<string, unknown> | null = null;
let hideTimer: NodeJS.Timeout | null = null;
let displayListenersRegistered = false;

function virtualDesktopBounds(): Electron.Rectangle {
  const displays = screen.getAllDisplays();
  if (!displays.length) return screen.getPrimaryDisplay().bounds;
  let left = displays[0].bounds.x;
  let top = displays[0].bounds.y;
  let right = displays[0].bounds.x + displays[0].bounds.width;
  let bottom = displays[0].bounds.y + displays[0].bounds.height;
  for (const display of displays.slice(1)) {
    left = Math.min(left, display.bounds.x);
    top = Math.min(top, display.bounds.y);
    right = Math.max(right, display.bounds.x + display.bounds.width);
    bottom = Math.max(bottom, display.bounds.y + display.bounds.height);
  }
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function isVisiblePayload(payload: Record<string, unknown> | null): payload is Record<string, unknown> {
  return Boolean(payload && payload.visible !== false);
}

function terminalPayload(payload: Record<string, unknown>): boolean {
  return payload.terminal === true;
}

function clearTimer(timer: NodeJS.Timeout | null): void {
  if (timer) clearTimeout(timer);
}

function registerDisplayListeners(): void {
  if (displayListenersRegistered) return;
  displayListenersRegistered = true;
  screen.on("display-added", resizeHudWindow);
  screen.on("display-removed", resizeHudWindow);
  screen.on("display-metrics-changed", resizeHudWindow);
}

function hudDocument(): string {
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:;" />
<meta name="color-scheme" content="dark" />
<title>Loom Automation HUD</title>
<style>
html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;font-family:Inter,Segoe UI,system-ui,sans-serif;user-select:none;cursor:default;}
#hud{--x:50vw;--y:50vh;--bubble-x:calc(50vw + 54px);--bubble-y:calc(50vh + 24px);--bubble-width:420px;--accent:#66d9ff;position:fixed;inset:0;opacity:0;visibility:hidden;pointer-events:none;overflow:hidden;transition:opacity .16s ease,visibility .16s ease;}
#hud.live{opacity:1;visibility:visible;}#hud.browser{--accent:#a994ff;}#hud.capture-safe .cursor,#hud.capture-safe .bubble,#hud.capture-safe .timeline,#hud.capture-safe .pill{opacity:0!important;}
.edge{position:absolute;inset:0;overflow:hidden;isolation:isolate}.edge::before,.edge::after{content:"";position:absolute;inset:0;background:conic-gradient(from var(--angle,0deg) at 50% 50%,#62f3ff 0deg,#58c7ff 34deg,#7779ff 72deg,#ba62ff 112deg,#ff4ab8 156deg,#ff4979 198deg,#ff9e4d 234deg,#ffe55a 268deg,#abf15f 302deg,#48eacb 336deg,#62f3ff 360deg);animation:orbit 7.5s linear infinite,breath 1.55s ease-in-out infinite;}
.edge::before{filter:blur(14px) saturate(1.16);opacity:.42;-webkit-mask:linear-gradient(to bottom,#000,transparent) top/100% 34px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 34px no-repeat,linear-gradient(to right,#000,transparent) left/34px 100% no-repeat,linear-gradient(to left,#000,transparent) right/34px 100% no-repeat;mask:linear-gradient(to bottom,#000,transparent) top/100% 34px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 34px no-repeat,linear-gradient(to right,#000,transparent) left/34px 100% no-repeat,linear-gradient(to left,#000,transparent) right/34px 100% no-repeat;}
.edge::after{filter:blur(8px) saturate(1.3);opacity:.68;-webkit-mask:linear-gradient(to bottom,#000,transparent) top/100% 16px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 16px no-repeat,linear-gradient(to right,#000,transparent) left/16px 100% no-repeat,linear-gradient(to left,#000,transparent) right/16px 100% no-repeat;mask:linear-gradient(to bottom,#000,transparent) top/100% 16px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 16px no-repeat,linear-gradient(to right,#000,transparent) left/16px 100% no-repeat,linear-gradient(to left,#000,transparent) right/16px 100% no-repeat;animation-delay:-1.2s,-.45s;}
@keyframes orbit{to{--angle:360deg}}@keyframes breath{0%,100%{opacity:.42}50%{opacity:.76}}
.pill{position:absolute;top:20px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:11px;max-width:min(720px,calc(100vw - 40px));padding:10px 15px;border-radius:999px;background:rgba(12,14,22,.78);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);backdrop-filter:blur(18px) saturate(1.2);box-shadow:0 14px 34px rgba(0,0,0,.30),0 0 28px color-mix(in srgb,var(--accent) 12%,transparent);}
.dot{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 color-mix(in srgb,var(--accent) 50%,transparent);animation:pulse 1.45s infinite;flex:0 0 auto}.pill strong{color:#f8fbff;font-size:13px;font-weight:760;white-space:nowrap}.pill span:last-child{color:#aeb8cc;font-size:12px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@keyframes pulse{70%{box-shadow:0 0 0 9px transparent}100%{box-shadow:0 0 0 0 transparent}}
.cursor{position:absolute;left:0;top:0;width:82px;height:82px;transform:translate3d(var(--x),var(--y),0) translate(-10px,-10px);transition:transform 210ms cubic-bezier(.18,.78,.18,1);will-change:transform}.aura{position:absolute;left:34px;top:34px;width:72px;height:72px;border-radius:50%;transform:translate(-50%,-50%);background:radial-gradient(ellipse at 34% 28%,rgba(70,238,255,.18),rgba(150,122,255,.06) 54%,transparent 78%);filter:blur(8px);opacity:.56;animation:aura 2.2s ease-in-out infinite}.cursor svg{position:absolute;left:0;top:0;width:36px;height:36px;overflow:visible;transform-origin:10px 10.5px;animation:float 2.15s ease-in-out infinite;filter:drop-shadow(0 0 5px rgba(90,236,255,.22)) drop-shadow(0 6px 10px rgba(52,35,120,.2))}@keyframes float{0%,100%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}50%{transform:translate(-.5px,-1.5px) rotate(-2.5deg) scale(1.009,.958)}}@keyframes aura{0%,100%{transform:translate(-50%,-50%) scale(.97);opacity:.44}50%{transform:translate(-50%,-50%) scale(1.07);opacity:.64}}
.wave{position:absolute;left:10px;top:10.5px;width:24px;height:24px;border-radius:50%;border:1.5px solid rgba(112,235,255,.95);box-shadow:0 0 18px rgba(120,105,255,.20);transform:translate(-50%,-50%) scale(.25);opacity:0}.cursor.clicking svg{animation:press .34s cubic-bezier(.2,.8,.2,1)}.cursor.clicking .wave{animation:wave .6s ease-out}@keyframes press{0%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}45%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(.94,.90)}100%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}}@keyframes wave{0%{opacity:1;transform:translate(-50%,-50%) scale(.35)}100%{opacity:0;transform:translate(-50%,-50%) scale(3.8)}}
.bubble{position:absolute;left:0;top:0;width:var(--bubble-width);min-height:112px;padding:14px 15px 13px;border-radius:17px;background:rgba(8,17,27,.86);border:1px solid color-mix(in srgb,var(--accent) 24%,transparent);backdrop-filter:blur(18px) saturate(1.18);box-shadow:0 16px 45px rgba(0,0,0,.40),0 0 26px color-mix(in srgb,var(--accent) 9%,transparent);transform:translate3d(var(--bubble-x),var(--bubble-y),0) scale(.78);transform-origin:left top;transition:transform 210ms cubic-bezier(.18,.78,.18,1),opacity .18s ease}.bubble-title{min-height:17px;margin-bottom:7px;color:#f8fbff;font-size:13px;font-weight:780}.line{display:flex;justify-content:space-between;gap:12px;color:#9fb0c1;font-size:12px;line-height:1.7}.line b{max-width:58%;color:#e7f8ff;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:650;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.thought{margin-top:8px;color:#c6d4e6;font-size:12px;line-height:1.45;max-height:35px;overflow:hidden}
.timeline{position:absolute;left:50%;bottom:24px;transform:translateX(-50%);display:flex;gap:8px;padding:9px;border-radius:999px;background:rgba(8,10,16,.68);border:1px solid rgba(255,255,255,.09);backdrop-filter:blur(16px);box-shadow:0 15px 34px rgba(0,0,0,.32)}.phase{display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:999px;color:#7f8ea3;font-size:11px;font-weight:650}.phase i{width:7px;height:7px;border-radius:50%;background:#344054}.phase.done{color:#a9f1d0}.phase.done i{background:#6ce6b5}.phase.active{color:#f5fbff;background:rgba(255,255,255,.08)}.phase.active i{background:var(--accent);box-shadow:0 0 12px var(--accent)}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<div id="hud" class="computer">
  <div class="edge"></div>
  <div class="pill"><i class="dot"></i><strong id="title">Loom 正在观察</strong><span id="meta">Computer Use</span></div>
  <div id="cursor" class="cursor"><div class="aura"></div><svg viewBox="0 0 64 64" focusable="false"><defs><path id="shape" d="M 7.8 7.8 C 8.91 5.71, 13.83 7.17, 20.4 10.4 C 26.97 13.63, 49.02 25.34, 51.1 29.1 C 53.18 32.86, 37.42 31.38, 34.1 35.2 C 30.78 39.02, 30.93 52.78, 29.2 54.3 C 27.47 55.82, 25.14 49.77, 22.7 45.2 C 20.26 40.63, 15.36 29.87, 13.1 24.2 C 10.84 18.53, 6.69 9.89, 7.8 7.8 Z"/><linearGradient id="base" x1="9" y1="9" x2="42" y2="55" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#2EF1FF"/><stop offset=".23" stop-color="#69E9FF"/><stop offset=".54" stop-color="#EDF7FF"/><stop offset=".78" stop-color="#E6D9FF"/><stop offset="1" stop-color="#FF9FCE"/></linearGradient><radialGradient id="white" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(29 28) rotate(32) scale(18 14)"><stop offset="0" stop-color="#fff" stop-opacity=".96"/><stop offset=".36" stop-color="#fff" stop-opacity=".80"/><stop offset="1" stop-color="#EEF7FF" stop-opacity="0"/></radialGradient><linearGradient id="rim" x1="9" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#CCFEFF" stop-opacity=".97"/><stop offset=".30" stop-color="#78F0FF" stop-opacity=".94"/><stop offset=".66" stop-color="#C0BEFF" stop-opacity=".86"/><stop offset="1" stop-color="#FFC5E2" stop-opacity=".95"/></linearGradient><clipPath id="clip"><use href="#shape"/></clipPath></defs><use href="#shape" fill="url(#base)"/><use href="#shape" fill="url(#white)"/><use href="#shape" fill="none" stroke="url(#rim)" stroke-width="1.02" stroke-linejoin="round"/><g clip-path="url(#clip)"><path d="M12 12.8 C20.9 14.7 33.4 20.3 45.8 27.7" fill="none" stroke="rgba(255,255,255,.44)" stroke-width=".90" stroke-linecap="round"/></g></svg><div class="wave"></div></div>
  <div class="bubble"><div id="bubbleTitle" class="bubble-title">准备视觉自动化</div><div class="line"><span>目标置信度</span><b id="confidence">—</b></div><div class="line"><span>执行坐标</span><b id="point">—</b></div><div class="line"><span>动作来源</span><b id="source">runtime</b></div><div id="thought" class="thought">等待下一步浏览器或桌面动作。</div></div>
  <div class="timeline" id="timeline"></div>
</div>
<script>
(function(){
  const hud=document.getElementById('hud');
  const title=document.getElementById('title');
  const meta=document.getElementById('meta');
  const bubbleTitle=document.getElementById('bubbleTitle');
  const confidence=document.getElementById('confidence');
  const point=document.getElementById('point');
  const source=document.getElementById('source');
  const thought=document.getElementById('thought');
  const cursor=document.getElementById('cursor');
  const timeline=document.getElementById('timeline');
  const phases=['观察','分析','移动','点击','验证'];
  let lastClick='';
  let settleTimer=0;
  let lastX=null,lastY=null;
  function clamp(v,min,max){v=Number(v); if(!Number.isFinite(v)) return min; return Math.min(max,Math.max(min,v));}
  function text(v,f){v=typeof v==='string'?v.trim():''; return v||f;}
  function place(payload){
    const w=Math.max(1,innerWidth),h=Math.max(1,innerHeight);
    if(lastX===null){lastX=w*.5;lastY=h*.46;}
    let x=Number(payload.hudX),y=Number(payload.hudY);
    // Task-level events (computer_run_task) legitimately carry no coordinate;
    // only the nested actions do. Falling through to clamp() turned those into
    // 0 and parked the cursor in the top-left corner, which read as a broken
    // HUD rather than as "no new target yet".
    if(!Number.isFinite(x)){const n=Number(payload.xNorm); x=Number.isFinite(n)?clamp(n,0,1)*w:lastX;}
    if(!Number.isFinite(y)){const n=Number(payload.yNorm); y=Number.isFinite(n)?clamp(n,0,1)*h:lastY;}
    lastX=x;lastY=y;
    const bw=Math.min(420,Math.max(320,w-48));
    const bx=clamp(x>w*.58?x-bw-52:x+52,18,Math.max(18,w-bw-18));
    const by=clamp(y>h*.58?y-172:y+26,18,Math.max(18,h-160));
    hud.style.setProperty('--x',x+'px');
    hud.style.setProperty('--y',y+'px');
    hud.style.setProperty('--bubble-x',bx+'px');
    hud.style.setProperty('--bubble-y',by+'px');
    hud.style.setProperty('--bubble-width',bw+'px');
    point.textContent=Math.round(x)+', '+Math.round(y);
  }
  function renderTimeline(active){
    timeline.textContent='';
    phases.forEach(function(label,index){
      const item=document.createElement('span');
      item.className='phase '+(index<active?'done':index===active?'active':'');
      item.innerHTML='<i></i><span></span>';
      item.lastChild.textContent=label;
      timeline.appendChild(item);
    });
  }
  function render(payload){
    if(!payload || payload.visible===false){
      hud.classList.remove('live');
      return;
    }
    const phase=clamp(Math.round(Number(payload.phase)||0),0,4);
    hud.className=(payload.source==='browser'?'browser':'computer')+(payload.captureSafe===true?' capture-safe':'')+' live';
    title.textContent=text(payload.title,payload.source==='browser'?'Loom 正在控制浏览器':'Loom 正在控制桌面');
    meta.textContent=text(payload.meta,payload.source==='browser'?'Browser Use':'Computer Use');
    bubbleTitle.textContent=text(payload.bubbleTitle||payload.currentAction,'准备视觉自动化');
    confidence.textContent=text(payload.confidence,'—');
    source.textContent=text(payload.actionSource,payload.source==='browser'?'browser-use + DOM':'screenshot + UIA');
    thought.textContent=text(payload.thought||payload.result,'等待下一步浏览器或桌面动作。');
    place(payload);
    renderTimeline(phase);
    if(payload.clickRevision && payload.clickRevision!==lastClick){
      lastClick=String(payload.clickRevision);
      cursor.classList.remove('clicking');
      void cursor.offsetWidth;
      cursor.classList.add('clicking');
      clearTimeout(settleTimer);
      settleTimer=setTimeout(function(){cursor.classList.remove('clicking');},620);
    }
  }
  if(window.loomHud && window.loomHud.onUpdate) window.loomHud.onUpdate(render);
  renderTimeline(0);
})();
</script>
</body>
</html>`;
}

function translatePayload(payload: Record<string, unknown>): Record<string, unknown> {
  const next = { ...payload };
  const sx = Number(next.screenX ?? next.screen_x);
  const sy = Number(next.screenY ?? next.screen_y);
  if (Number.isFinite(sx) && Number.isFinite(sy)) {
    next.hudX = sx - lastBounds.x;
    next.hudY = sy - lastBounds.y;
  }
  return next;
}

function resizeHudWindow(): void {
  if (!hudWindow || hudWindow.isDestroyed()) return;
  lastBounds = virtualDesktopBounds();
  hudWindow.setBounds(lastBounds, false);
  if (hudWindow.isVisible()) {
    hudWindow.setAlwaysOnTop(true, "screen-saver");
    hudWindow.moveTop();
  }
}

function showHudWindow(window: BrowserWindow): void {
  clearTimer(hideTimer);
  hideTimer = null;
  resizeHudWindow();
  if (!window.isVisible()) {
    window.setAlwaysOnTop(true, "screen-saver");
    window.showInactive();
    window.moveTop();
  }
}

function hideHudWindow(delayMs = HIDE_AFTER_MS): void {
  clearTimer(hideTimer);
  if (!hudWindow || hudWindow.isDestroyed()) return;
  hideTimer = setTimeout(() => {
    if (!hudWindow || hudWindow.isDestroyed()) return;
    hudWindow.hide();
  }, delayMs);
}

export function createHudOverlayWindow(force = false): BrowserWindow | null {
  if (hudWindow && !hudWindow.isDestroyed()) return hudWindow;
  if (!force && !isVisiblePayload(pendingPayload)) return null;

  registerDisplayListeners();
  lastBounds = virtualDesktopBounds();
  hudWindow = new BrowserWindow({
    ...lastBounds,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    closable: false,
    focusable: false,
    skipTaskbar: true,
    alwaysOnTop: false,
    show: false,
    title: "Loom HUD Overlay",
    webPreferences: {
      preload: path.join(__dirname, "hudPreload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });

  // The HUD is display-only. Do not forward mouse movement through the overlay:
  // on Windows, a transparent always-on-top window with forwarded mouse events
  // can force repeated cursor hit-testing/repainting over the main Loom window.
  hudWindow.setIgnoreMouseEvents(true);
  hudWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  hudWindow.once("ready-to-show", () => {
    if (!hudWindow || hudWindow.isDestroyed() || !isVisiblePayload(pendingPayload)) return;
    sendHudUpdate(pendingPayload);
  });
  hudWindow.on("closed", () => {
    hudWindow = null;
  });
  void hudWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(hudDocument())}`);
  return hudWindow;
}

export function sendHudUpdate(payload: Record<string, unknown>): void {
  if (!isVisiblePayload(payload)) {
    pendingPayload = null;
    const existing = hudWindow && !hudWindow.isDestroyed() ? hudWindow : null;
    if (existing && !existing.webContents.isLoading()) {
      existing.webContents.send("loom:hud-update", { ...(payload as Record<string, unknown>), visible: false });
    }
    hideHudWindow();
    return;
  }

  pendingPayload = payload;
  const window = hudWindow && !hudWindow.isDestroyed() ? hudWindow : createHudOverlayWindow(true);
  if (!window) return;
  showHudWindow(window);
  if (window.webContents.isLoading()) return;

  window.webContents.send("loom:hud-update", translatePayload(payload));
  if (terminalPayload(payload)) hideHudWindow(2300);
}

export function closeHudOverlayWindow(): void {
  clearTimer(hideTimer);
  hideTimer = null;
  if (!hudWindow || hudWindow.isDestroyed()) return;
  hudWindow.destroy();
  hudWindow = null;
}
