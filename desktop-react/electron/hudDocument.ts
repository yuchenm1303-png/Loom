// Shared unchanged by the native desktop overlay and the home-page preview.
export function hudDocument(): string {
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
#hud.live{opacity:1;visibility:visible;}#hud.browser{--accent:#a994ff;}#hud.capture-safe .cursor,#hud.capture-safe .bubble,#hud.capture-safe .pill{opacity:0!important;}
.pill{position:absolute;top:20px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:11px;max-width:min(720px,calc(100vw - 40px));padding:10px 15px;border-radius:999px;background:rgba(12,14,22,.78);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);box-shadow:0 14px 34px rgba(0,0,0,.30),0 0 28px color-mix(in srgb,var(--accent) 12%,transparent);}
.dot{position:relative;width:8px;height:8px;border-radius:50%;background:var(--accent);flex:0 0 auto}.dot::before{content:"";position:absolute;inset:0;border-radius:50%;background:color-mix(in srgb,var(--accent) 50%,transparent);animation:ring 1.45s steps(14,jump-none) infinite}.pill strong{color:#f8fbff;font-size:13px;font-weight:760;white-space:nowrap}.pill span:last-child{color:#aeb8cc;font-size:12px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@keyframes ring{0%{transform:scale(1);opacity:1}70%{transform:scale(3.25);opacity:0}100%{transform:scale(1);opacity:0}}
.cursor{position:absolute;left:0;top:0;width:82px;height:82px;transform:translate3d(var(--x),var(--y),0) translate(-10px,-10px);transition:transform 210ms cubic-bezier(.18,.78,.18,1);will-change:transform}.aura{position:absolute;left:27px;top:27px;width:52px;height:52px;border-radius:50%;transform:translate(-50%,-50%);background:radial-gradient(circle closest-side at 50% 50%,rgba(70,238,255,.12),rgba(150,122,255,.035) 54%,transparent);opacity:.30;animation:aura 2.2s steps(14,jump-none) infinite}.cursor svg{position:absolute;left:0;top:0;width:36px;height:36px;overflow:visible;transform-origin:10px 10.5px;animation:float 2.15s steps(14,jump-none) infinite;filter:drop-shadow(0 0 3px rgba(90,236,255,.14)) drop-shadow(0 4px 7px rgba(52,35,120,.10))}@keyframes float{0%,100%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}50%{transform:translate(-.5px,-1.5px) rotate(-2.5deg) scale(1.009,.958)}}@keyframes aura{0%,100%{transform:translate(-50%,-50%) scale(.98);opacity:.24}50%{transform:translate(-50%,-50%) scale(1.05);opacity:.42}}
.wave{position:absolute;left:10px;top:10.5px;width:24px;height:24px;border-radius:50%;border:1.5px solid rgba(112,235,255,.95);box-shadow:0 0 18px rgba(120,105,255,.20);transform:translate(-50%,-50%) scale(.25);opacity:0}.cursor.clicking svg{animation:press .34s cubic-bezier(.2,.8,.2,1)}.cursor.clicking .wave{animation:wave .6s ease-out}@keyframes press{0%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}45%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(.94,.90)}100%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}}@keyframes wave{0%{opacity:1;transform:translate(-50%,-50%) scale(.35)}100%{opacity:0;transform:translate(-50%,-50%) scale(3.8)}}
.bubble{position:absolute;left:0;top:0;width:var(--bubble-width);min-height:112px;padding:14px 15px 13px;border-radius:17px;background:rgba(8,17,27,.86);border:1px solid color-mix(in srgb,var(--accent) 24%,transparent);box-shadow:0 16px 45px rgba(0,0,0,.40),0 0 26px color-mix(in srgb,var(--accent) 9%,transparent);transform:translate3d(var(--bubble-x),var(--bubble-y),0) scale(.78);transform-origin:left top;transition:transform 210ms cubic-bezier(.18,.78,.18,1),opacity .18s ease}.bubble-title{min-height:17px;margin-bottom:7px;color:#f8fbff;font-size:13px;font-weight:780}.line{display:flex;justify-content:space-between;gap:12px;color:#9fb0c1;font-size:12px;line-height:1.7}.line b{max-width:58%;color:#e7f8ff;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:650;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.thought{margin-top:8px;color:#c6d4e6;font-size:12px;line-height:1.45;max-height:35px;overflow:hidden}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
.edge{position:absolute;inset:0;overflow:hidden;pointer-events:none}
.edge i{position:absolute;display:block;opacity:var(--min);background:conic-gradient(from 0deg at var(--cx) var(--cy),var(--ring));-webkit-mask-image:linear-gradient(var(--dir),var(--fade)),linear-gradient(var(--end),var(--ends));mask-image:linear-gradient(var(--dir),var(--fade)),linear-gradient(var(--end),var(--ends));-webkit-mask-composite:source-in;mask-composite:intersect}
.edge .a{--ring:#59f5ff 0deg,#51c9ff 34deg,#7678ff 72deg,#bf60ff 112deg,#ff46bd 156deg,#ff4679 198deg,#ff9d45 234deg,#ffe54f 268deg,#a7f355 302deg,#3eedcb 336deg,#59f5ff 360deg;--fade:rgba(0,0,0,.5) 0px,rgba(0,0,0,.548) 5.667px,rgba(0,0,0,.527) 11.333px,rgba(0,0,0,.444) 17px,rgba(0,0,0,.316) 22.667px,rgba(0,0,0,.163) 28.333px,transparent 34px;--ends:rgba(0,0,0,.5) 0px,rgba(0,0,0,.691) 7px,rgba(0,0,0,.841) 14px,rgba(0,0,0,.933) 21px,rgba(0,0,0,.977) 28px,rgba(0,0,0,.994) 35px,rgba(0,0,0,.994) calc(100% - 35px),rgba(0,0,0,.977) calc(100% - 28px),rgba(0,0,0,.933) calc(100% - 21px),rgba(0,0,0,.841) calc(100% - 14px),rgba(0,0,0,.691) calc(100% - 7px),rgba(0,0,0,.5) 100%;--t:34px;--min:.22;--max:.50}
.edge .b{--ring:#4df8ff 0deg,#48cbff 34deg,#7577ff 72deg,#c45dff 112deg,#ff42c3 156deg,#ff417a 198deg,#ff9b3c 234deg,#ffe642 268deg,#a3f649 302deg,#31f1cc 336deg,#4df8ff 360deg;--fade:rgba(0,0,0,.5) 0px,rgba(0,0,0,.525) 2.667px,rgba(0,0,0,.498) 5.333px,rgba(0,0,0,.421) 8px,rgba(0,0,0,.303) 10.667px,rgba(0,0,0,.159) 13.333px,transparent 16px;--ends:rgba(0,0,0,.5) 0px,rgba(0,0,0,.691) 4px,rgba(0,0,0,.841) 8px,rgba(0,0,0,.933) 12px,rgba(0,0,0,.977) 16px,rgba(0,0,0,.994) 20px,rgba(0,0,0,.994) calc(100% - 20px),rgba(0,0,0,.977) calc(100% - 16px),rgba(0,0,0,.933) calc(100% - 12px),rgba(0,0,0,.841) calc(100% - 8px),rgba(0,0,0,.691) calc(100% - 4px),rgba(0,0,0,.5) 100%;--t:16px;--min:.32;--max:.64}
.edge .n{inset:0 0 auto 0;height:var(--t);--cx:50%;--cy:50vh;--dir:to bottom;--end:to right}
.edge .s{inset:auto 0 0 0;height:var(--t);--cx:50%;--cy:calc(var(--t) - 50vh);--dir:to top;--end:to right}
.edge .w{inset:0 auto 0 0;width:var(--t);--cx:50vw;--cy:50%;--dir:to right;--end:to bottom}
.edge .e{inset:0 0 0 auto;width:var(--t);--cx:calc(var(--t) - 50vw);--cy:50%;--dir:to left;--end:to bottom}
</style>
</head>
<body>
<div id="hud" class="computer">
  <div class="edge"><i class="a n"></i><i class="a s"></i><i class="a w"></i><i class="a e"></i><i class="b n"></i><i class="b s"></i><i class="b w"></i><i class="b e"></i></div>
  <div class="pill"><i class="dot"></i><strong id="title">Loom 正在观察</strong><span id="meta">Computer Use</span></div>
  <div id="cursor" class="cursor"><div class="aura"></div><svg viewBox="0 0 64 64" focusable="false"><defs><path id="shape" d="M 7.8 7.8 C 8.91 5.71, 13.83 7.17, 20.4 10.4 C 26.97 13.63, 49.02 25.34, 51.1 29.1 C 53.18 32.86, 37.42 31.38, 34.1 35.2 C 30.78 39.02, 30.93 52.78, 29.2 54.3 C 27.47 55.82, 25.14 49.77, 22.7 45.2 C 20.26 40.63, 15.36 29.87, 13.1 24.2 C 10.84 18.53, 6.69 9.89, 7.8 7.8 Z"/><linearGradient id="base" x1="9" y1="9" x2="42" y2="55" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#2EF1FF"/><stop offset=".23" stop-color="#69E9FF"/><stop offset=".54" stop-color="#EDF7FF"/><stop offset=".78" stop-color="#E6D9FF"/><stop offset="1" stop-color="#FF9FCE"/></linearGradient><radialGradient id="white" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(29 28) rotate(32) scale(18 14)"><stop offset="0" stop-color="#fff" stop-opacity=".96"/><stop offset=".36" stop-color="#fff" stop-opacity=".80"/><stop offset="1" stop-color="#EEF7FF" stop-opacity="0"/></radialGradient><linearGradient id="rim" x1="9" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#CCFEFF" stop-opacity=".97"/><stop offset=".30" stop-color="#78F0FF" stop-opacity=".94"/><stop offset=".66" stop-color="#C0BEFF" stop-opacity=".86"/><stop offset="1" stop-color="#FFC5E2" stop-opacity=".95"/></linearGradient><clipPath id="clip"><use href="#shape"/></clipPath></defs><use href="#shape" fill="url(#base)"/><use href="#shape" fill="url(#white)"/><use href="#shape" fill="none" stroke="url(#rim)" stroke-width="1.02" stroke-linejoin="round"/><g clip-path="url(#clip)"><path d="M12 12.8 C20.9 14.7 33.4 20.3 45.8 27.7" fill="none" stroke="rgba(255,255,255,.44)" stroke-width=".90" stroke-linecap="round"/></g></svg><div class="wave"></div></div>
  <div class="bubble"><div id="bubbleTitle" class="bubble-title">准备视觉自动化</div><div class="line"><span>目标置信度</span><b id="confidence">—</b></div><div class="line"><span>执行坐标</span><b id="point">—</b></div><div class="line"><span>动作来源</span><b id="source">runtime</b></div><div id="thought" class="thought">等待下一步浏览器或桌面动作。</div></div>
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
  let lastClick='';
  let settleTimer=0;
  let lastX=null,lastY=null;
  // The glow breathes on a timer, not on an infinite CSS animation: a compositor animation over the
  // whole screen keeps the GPU producing a frame for every refresh (the HUD used to take the whole
  // integrated GPU), while a 12 Hz timer only asks for what the eye can use.
  var glow=[].slice.call(document.querySelectorAll('.edge i')),breathTimer=0,reduce=matchMedia('(prefers-reduced-motion:reduce)');
  function breathe(){var t=performance.now()/1000;for(var i=0;i<glow.length;i++){var b=glow[i].classList.contains('b');var k=.5-.5*Math.cos(6.283185*(((t+(b?.45:0))/1.55)%1));var lo=b?.32:.22,hi=b?.64:.5;glow[i].style.opacity=(lo+(hi-lo)*k).toFixed(3);}}
  function breathing(on){on=on&&!reduce.matches&&!document.hidden;if(on&&!breathTimer){breathe();breathTimer=setInterval(breathe,83);}else if(!on&&breathTimer){clearInterval(breathTimer);breathTimer=0;}}
  document.addEventListener('visibilitychange',function(){breathing(hud.classList.contains('live'));});
  function clamp(v,min,max){v=Number(v); if(!Number.isFinite(v)) return min; return Math.min(max,Math.max(min,v));}
  function text(v,f){v=typeof v==='string'?v.trim():''; return v||f;}
  function place(payload){
    const w=Math.max(1,innerWidth),h=Math.max(1,innerHeight);
    if(lastX===null){lastX=w*.5;lastY=h*.46;}
    let x=payload.hudX==null?NaN:Number(payload.hudX),y=payload.hudY==null?NaN:Number(payload.hudY);
    // Task-level events (computer_run_task) legitimately carry no coordinate;
    // only the nested actions do. Falling through to clamp() turned those into
    // 0 and parked the cursor in the top-left corner, which read as a broken
    // HUD rather than as "no new target yet".
    if(!Number.isFinite(x)){const n=payload.xNorm==null?NaN:Number(payload.xNorm); x=Number.isFinite(n)?clamp(n,0,1)*w:lastX;}
    if(!Number.isFinite(y)){const n=payload.yNorm==null?NaN:Number(payload.yNorm); y=Number.isFinite(n)?clamp(n,0,1)*h:lastY;}
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
  function render(payload){
    if(!payload || payload.visible===false){
      hud.classList.remove('live');
      breathing(false);
      return;
    }
    hud.className=(payload.source==='browser'?'browser':'computer')+(payload.captureSafe===true?' capture-safe':'')+' live';
    breathing(true);
    title.textContent=text(payload.title,payload.source==='browser'?'Loom 正在控制浏览器':'Loom 正在控制桌面');
    meta.textContent=text(payload.meta,payload.source==='browser'?'Browser Use':'Computer Use');
    bubbleTitle.textContent=text(payload.bubbleTitle||payload.currentAction,'准备视觉自动化');
    confidence.textContent=text(payload.confidence,'—');
    source.textContent=text(payload.actionSource,payload.source==='browser'?'browser-use + DOM':'screenshot + UIA');
    thought.textContent=text(payload.thought||payload.result,'等待下一步浏览器或桌面动作。');
    place(payload);
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
})();
</script>
</body>
</html>`;
}
