(() => {
  'use strict';

  const HOST_ID = 'loom-browser-page-hud-root';
  const LAYER_ID = 'loom-computer-hud-layer';
  const ROOT_OBSERVER_KEY = '__loomComputerHudObserver';

  const SVG_CURSOR = `<svg viewBox="0 0 64 64" focusable="false" aria-hidden="true"><defs><path id="loom-hud-shape" d="M 7.8 7.8 C 8.91 5.71, 13.83 7.17, 20.4 10.4 C 26.97 13.63, 49.02 25.34, 51.1 29.1 C 53.18 32.86, 37.42 31.38, 34.1 35.2 C 30.78 39.02, 30.93 52.78, 29.2 54.3 C 27.47 55.82, 25.14 49.77, 22.7 45.2 C 20.26 40.63, 15.36 29.87, 13.1 24.2 C 10.84 18.53, 6.69 9.89, 7.8 7.8 Z"/><linearGradient id="loom-hud-base" x1="9" y1="9" x2="42" y2="55" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#2EF1FF"/><stop offset=".23" stop-color="#69E9FF"/><stop offset=".54" stop-color="#EDF7FF"/><stop offset=".78" stop-color="#E6D9FF"/><stop offset="1" stop-color="#FF9FCE"/></linearGradient><radialGradient id="loom-hud-white" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(29 28) rotate(32) scale(18 14)"><stop offset="0" stop-color="#fff" stop-opacity=".96"/><stop offset=".36" stop-color="#fff" stop-opacity=".80"/><stop offset="1" stop-color="#EEF7FF" stop-opacity="0"/></radialGradient><linearGradient id="loom-hud-rim" x1="9" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#CCFEFF" stop-opacity=".97"/><stop offset=".30" stop-color="#78F0FF" stop-opacity=".94"/><stop offset=".66" stop-color="#C0BEFF" stop-opacity=".86"/><stop offset="1" stop-color="#FFC5E2" stop-opacity=".95"/></linearGradient></defs><use href="#loom-hud-shape" fill="url(#loom-hud-base)"/><use href="#loom-hud-shape" fill="url(#loom-hud-white)"/><use href="#loom-hud-shape" fill="none" stroke="url(#loom-hud-rim)" stroke-width="1.02" stroke-linejoin="round"/></svg>`;

  const STYLES = `
    #${LAYER_ID}{--x:50vw;--y:46vh;--bubble-x:calc(50vw + 52px);--bubble-y:calc(46vh + 26px);--bubble-width:390px;--accent:#a994ff;position:fixed;inset:0;z-index:2147483646;pointer-events:none;overflow:hidden;color:#eef5ff;font-family:Inter,Segoe UI,system-ui,sans-serif;}
    #${LAYER_ID} .loom-cu-edge{position:absolute;inset:0;overflow:hidden;isolation:isolate;opacity:.82}
    #${LAYER_ID} .loom-cu-edge::before,#${LAYER_ID} .loom-cu-edge::after{content:"";position:absolute;inset:0;background:conic-gradient(from var(--angle,0deg) at 50% 50%,#62f3ff 0deg,#58c7ff 34deg,#7779ff 72deg,#ba62ff 112deg,#ff4ab8 156deg,#ff4979 198deg,#ff9e4d 234deg,#ffe55a 268deg,#abf15f 302deg,#48eacb 336deg,#62f3ff 360deg);animation:loomCuOrbit 7.5s linear infinite,loomCuBreath 1.55s ease-in-out infinite}
    #${LAYER_ID} .loom-cu-edge::before{filter:blur(14px) saturate(1.16);opacity:.38;-webkit-mask:linear-gradient(to bottom,#000,transparent) top/100% 26px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 26px no-repeat,linear-gradient(to right,#000,transparent) left/26px 100% no-repeat,linear-gradient(to left,#000,transparent) right/26px 100% no-repeat;mask:linear-gradient(to bottom,#000,transparent) top/100% 26px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 26px no-repeat,linear-gradient(to right,#000,transparent) left/26px 100% no-repeat,linear-gradient(to left,#000,transparent) right/26px 100% no-repeat}
    #${LAYER_ID} .loom-cu-edge::after{filter:blur(8px) saturate(1.3);opacity:.58;-webkit-mask:linear-gradient(to bottom,#000,transparent) top/100% 12px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 12px no-repeat,linear-gradient(to right,#000,transparent) left/12px 100% no-repeat,linear-gradient(to left,#000,transparent) right/12px 100% no-repeat;mask:linear-gradient(to bottom,#000,transparent) top/100% 12px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 12px no-repeat,linear-gradient(to right,#000,transparent) left/12px 100% no-repeat,linear-gradient(to left,#000,transparent) right/12px 100% no-repeat;animation-delay:-1.2s,-.45s}
    #${LAYER_ID} .loom-cu-pill{position:absolute;top:16px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:10px;max-width:min(680px,calc(100vw - 40px));padding:9px 14px;border-radius:999px;background:rgba(12,14,22,.78);border:1px solid rgba(169,148,255,.28);backdrop-filter:blur(18px) saturate(1.2);box-shadow:0 14px 34px rgba(0,0,0,.28),0 0 28px rgba(169,148,255,.12)}
    #${LAYER_ID} .loom-cu-dot{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 rgba(169,148,255,.45);animation:loomCuPulse 1.45s infinite;flex:0 0 auto}
    #${LAYER_ID} .loom-cu-pill strong{color:#f8fbff;font-size:13px;font-weight:760;white-space:nowrap}#${LAYER_ID} .loom-cu-pill span{color:#aeb8cc;font-size:12px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #${LAYER_ID} .loom-cu-cursor{position:absolute;left:0;top:0;width:82px;height:82px;transform:translate3d(var(--x),var(--y),0) translate(-10px,-10px);transition:transform 210ms cubic-bezier(.18,.78,.18,1);will-change:transform}
    #${LAYER_ID} .loom-cu-cursor.hidden{display:none}#${LAYER_ID} .loom-cu-aura{position:absolute;left:34px;top:34px;width:72px;height:72px;border-radius:50%;transform:translate(-50%,-50%);background:radial-gradient(ellipse at 34% 28%,rgba(70,238,255,.18),rgba(150,122,255,.06) 54%,transparent 78%);filter:blur(8px);opacity:.56;animation:loomCuAura 2.2s ease-in-out infinite}
    #${LAYER_ID} .loom-cu-cursor svg{position:absolute;left:0;top:0;width:36px;height:36px;overflow:visible;transform-origin:10px 10.5px;animation:loomCuFloat 2.15s ease-in-out infinite;filter:drop-shadow(0 0 5px rgba(90,236,255,.22)) drop-shadow(0 6px 10px rgba(52,35,120,.2))}
    #${LAYER_ID} .loom-cu-wave{position:absolute;left:10px;top:10.5px;width:24px;height:24px;border-radius:50%;border:1.5px solid rgba(112,235,255,.95);box-shadow:0 0 18px rgba(120,105,255,.20);transform:translate(-50%,-50%) scale(.25);opacity:0}#${LAYER_ID} .loom-cu-cursor.clicking svg{animation:loomCuPress .34s cubic-bezier(.2,.8,.2,1)}#${LAYER_ID} .loom-cu-cursor.clicking .loom-cu-wave{animation:loomCuWave .6s ease-out}
    #${LAYER_ID} .loom-cu-bubble{position:absolute;left:0;top:0;width:var(--bubble-width);min-height:108px;padding:13px 14px 12px;border-radius:17px;background:rgba(8,17,27,.86);border:1px solid rgba(169,148,255,.24);backdrop-filter:blur(18px) saturate(1.18);box-shadow:0 16px 45px rgba(0,0,0,.38),0 0 26px rgba(169,148,255,.09);transform:translate3d(var(--bubble-x),var(--bubble-y),0) scale(.80);transform-origin:left top;transition:transform 210ms cubic-bezier(.18,.78,.18,1),opacity .18s ease}
    #${LAYER_ID} .loom-cu-bubble.hidden{display:none}#${LAYER_ID} .loom-cu-bubble-title{min-height:17px;margin-bottom:7px;color:#f8fbff;font-size:13px;font-weight:780}#${LAYER_ID} .loom-cu-line{display:flex;justify-content:space-between;gap:12px;color:#9fb0c1;font-size:12px;line-height:1.7}#${LAYER_ID} .loom-cu-line b{max-width:62%;color:#e7f8ff;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:650;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#${LAYER_ID} .loom-cu-thought{margin-top:7px;color:#c6d4e6;font-size:12px;line-height:1.45;max-height:35px;overflow:hidden}
    #${LAYER_ID} .loom-cu-timeline{position:absolute;left:50%;bottom:18px;transform:translateX(-50%);display:flex;gap:7px;padding:8px;border-radius:999px;background:rgba(8,10,16,.68);border:1px solid rgba(255,255,255,.09);backdrop-filter:blur(16px);box-shadow:0 15px 34px rgba(0,0,0,.28)}#${LAYER_ID} .loom-cu-phase{display:flex;align-items:center;gap:6px;padding:6px 9px;border-radius:999px;color:#7f8ea3;font-size:11px;font-weight:650}#${LAYER_ID} .loom-cu-phase i{width:7px;height:7px;border-radius:50%;background:#344054}#${LAYER_ID} .loom-cu-phase.done{color:#a9f1d0}#${LAYER_ID} .loom-cu-phase.done i{background:#6ce6b5}#${LAYER_ID} .loom-cu-phase.active{color:#f5fbff;background:rgba(255,255,255,.08)}#${LAYER_ID} .loom-cu-phase.active i{background:var(--accent);box-shadow:0 0 12px var(--accent)}
    @keyframes loomCuOrbit{to{--angle:360deg}}@keyframes loomCuBreath{0%,100%{opacity:.42}50%{opacity:.76}}@keyframes loomCuPulse{70%{box-shadow:0 0 0 9px transparent}100%{box-shadow:0 0 0 0 transparent}}@keyframes loomCuFloat{0%,100%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}50%{transform:translate(-.5px,-1.5px) rotate(-2.5deg) scale(1.009,.958)}}@keyframes loomCuAura{0%,100%{transform:translate(-50%,-50%) scale(.97);opacity:.44}50%{transform:translate(-50%,-50%) scale(1.07);opacity:.64}}@keyframes loomCuPress{0%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}45%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(.94,.90)}100%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}}@keyframes loomCuWave{0%{opacity:1;transform:translate(-50%,-50%) scale(.35)}100%{opacity:0;transform:translate(-50%,-50%) scale(3.8)}}
    @media (prefers-reduced-motion:reduce){#${LAYER_ID},#${LAYER_ID} *{animation:none!important;transition:none!important}}
  `;

  const phaseFor = (title) => {
    const value = String(title || '').toLowerCase();
    if (/click|type|select|drag|press/.test(value)) return 3;
    if (/hover|scroll|opening|navigat|refresh|switch/.test(value)) return 2;
    if (/screenshot|captured|done|complete/.test(value)) return 4;
    if (/read|state|inspect|observe/.test(value)) return 0;
    return 1;
  };

  const normalizeText = (value, fallback = '') => String(value || fallback).replace(/\s+/g, ' ').trim().slice(0, 220);

  function findPresentation(root) {
    const target = root.querySelector('.loom-page-hud-target');
    const label = root.querySelector('.loom-page-hud-label');
    const status = root.querySelector('.loom-page-hud-pill');
    const title = normalizeText(label?.querySelector('strong')?.textContent || status?.querySelector('strong')?.textContent || status?.textContent, 'Browser Use');
    const subtitle = normalizeText(label?.querySelector('span')?.textContent || status?.textContent || 'Browser Use', 'Browser Use');
    return { target, title, subtitle };
  }

  function positionFor(target) {
    if (!target) return null;
    const rect = target.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return null;
    return {
      x: Math.max(0, Math.min(innerWidth, rect.left + rect.width / 2)),
      y: Math.max(0, Math.min(innerHeight, rect.top + rect.height / 2)),
    };
  }

  function renderComputerLayer(root) {
    if (!root || root.getElementById(LAYER_ID)) return;
    const { target, title, subtitle } = findPresentation(root);
    const point = positionFor(target);
    const phase = phaseFor(title);
    const width = Math.max(1, innerWidth);
    const height = Math.max(1, innerHeight);
    const x = point?.x ?? width * 0.5;
    const y = point?.y ?? height * 0.46;
    const bubbleWidth = Math.min(390, Math.max(310, width - 48));
    const bx = Math.max(18, Math.min(width - bubbleWidth - 18, x > width * 0.58 ? x - bubbleWidth - 52 : x + 52));
    const by = Math.max(18, Math.min(height - 155, y > height * 0.58 ? y - 168 : y + 26));
    const phases = ['观察', '分析', '移动', '点击', '完成'];
    const timeline = phases.map((label, index) => `<span class="loom-cu-phase ${index < phase ? 'done' : index === phase ? 'active' : ''}"><i></i><span>${label}</span></span>`).join('');
    const layer = document.createElement('div');
    layer.id = LAYER_ID;
    layer.style.setProperty('--x', `${x}px`);
    layer.style.setProperty('--y', `${y}px`);
    layer.style.setProperty('--bubble-x', `${bx}px`);
    layer.style.setProperty('--bubble-y', `${by}px`);
    layer.style.setProperty('--bubble-width', `${bubbleWidth}px`);
    layer.innerHTML = `<style>${STYLES}</style>
      <div class="loom-cu-edge"></div>
      <div class="loom-cu-pill"><i class="loom-cu-dot"></i><strong>Loom 正在控制浏览器</strong><span>Browser Use · 当前网页</span></div>
      <div class="loom-cu-cursor ${point ? '' : 'hidden'} ${/^click/i.test(title) ? 'clicking' : ''}"><div class="loom-cu-aura"></div>${SVG_CURSOR}<div class="loom-cu-wave"></div></div>
      <div class="loom-cu-bubble ${point ? '' : 'hidden'}"><div class="loom-cu-bubble-title"></div><div class="loom-cu-line"><span>目标置信度</span><b>DOM exact</b></div><div class="loom-cu-line"><span>网页坐标</span><b>${Math.round(x)}, ${Math.round(y)}</b></div><div class="loom-cu-line"><span>动作来源</span><b>browser + DOM</b></div><div class="loom-cu-thought"></div></div>
      <div class="loom-cu-timeline">${timeline}</div>`;
    layer.querySelector('.loom-cu-bubble-title').textContent = title;
    layer.querySelector('.loom-cu-thought').textContent = subtitle;
    root.appendChild(layer);
  }

  function attachToHost(host) {
    const root = host?.shadowRoot;
    if (!root) return;
    renderComputerLayer(root);
    if (root[ROOT_OBSERVER_KEY]) return;
    const observer = new MutationObserver(() => queueMicrotask(() => renderComputerLayer(root)));
    observer.observe(root, { childList: true, subtree: false });
    root[ROOT_OBSERVER_KEY] = observer;
  }

  function scan() {
    const host = document.getElementById(HOST_ID);
    if (host) attachToHost(host);
  }

  const documentObserver = new MutationObserver(scan);
  const begin = () => {
    scan();
    documentObserver.observe(document.documentElement, { childList: true, subtree: true });
  };
  if (document.documentElement) begin();
  else document.addEventListener('DOMContentLoaded', begin, { once: true });
})();
