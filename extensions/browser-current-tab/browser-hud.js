(() => {
  'use strict';

  const INSTALL_KEY = '__loomBrowserHudRuntimeV2';
  const GENERATION = '0.1.27';
  const SOURCE_HOST_ID = 'loom-browser-page-hud-root';
  const HOST_ID = 'loom-browser-hud-root-v2';
  const LEGACY_HOST_IDS = ['loom-browser-hud-root', 'loom-browser-computer-hud-root'];
  const LEGACY_LAYER_ID = 'loom-computer-hud-layer';
  const LEGACY_SUPPRESSOR_ID = 'loom-browser-hud-v2-suppress-legacy';
  const SESSION_ACTIVE_KEY = 'loomBrowserSessionActive';
  // This script is a content script: it runs in every web page, including the
  // tabs the user opens for themselves. A session flag alone cannot tell those
  // apart from the tab Loom is driving, so the HUD showed up everywhere. The
  // worker publishes the tabs it is actually driving, and each page shows the
  // HUD only when the tab it lives in is one of them.
  const HUD_TAB_IDS_KEY = 'loomHudTabIds';
  const HUD_TAB_QUERY = 'loom-hud-tab-id';

  const existing = globalThis[INSTALL_KEY];
  if (existing?.generation === GENERATION) {
    try { existing.sync(); } catch (_) {}
    return;
  }
  try { existing?.dispose?.(); } catch (_) {}
  try { globalThis.__loomBrowserHudStandaloneV1?.hide?.(); } catch (_) {}

  const SVG_CURSOR = `<svg viewBox="0 0 64 64" focusable="false" aria-hidden="true"><defs><path id="loom-browser-hud-shape" d="M 7.8 7.8 C 8.91 5.71, 13.83 7.17, 20.4 10.4 C 26.97 13.63, 49.02 25.34, 51.1 29.1 C 53.18 32.86, 37.42 31.38, 34.1 35.2 C 30.78 39.02, 30.93 52.78, 29.2 54.3 C 27.47 55.82, 25.14 49.77, 22.7 45.2 C 20.26 40.63, 15.36 29.87, 13.1 24.2 C 10.84 18.53, 6.69 9.89, 7.8 7.8 Z"/><linearGradient id="loom-browser-hud-base" x1="9" y1="9" x2="42" y2="55" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#2EF1FF"/><stop offset=".23" stop-color="#69E9FF"/><stop offset=".54" stop-color="#EDF7FF"/><stop offset=".78" stop-color="#E6D9FF"/><stop offset="1" stop-color="#FF9FCE"/></linearGradient><radialGradient id="loom-browser-hud-white" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(29 28) rotate(32) scale(18 14)"><stop offset="0" stop-color="#fff" stop-opacity=".96"/><stop offset=".36" stop-color="#fff" stop-opacity=".80"/><stop offset="1" stop-color="#EEF7FF" stop-opacity="0"/></radialGradient><linearGradient id="loom-browser-hud-rim" x1="9" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#CCFEFF" stop-opacity=".97"/><stop offset=".30" stop-color="#78F0FF" stop-opacity=".94"/><stop offset=".66" stop-color="#C0BEFF" stop-opacity=".86"/><stop offset="1" stop-color="#FFC5E2" stop-opacity=".95"/></linearGradient></defs><use href="#loom-browser-hud-shape" fill="url(#loom-browser-hud-base)"/><use href="#loom-browser-hud-shape" fill="url(#loom-browser-hud-white)"/><use href="#loom-browser-hud-shape" fill="none" stroke="url(#loom-browser-hud-rim)" stroke-width="1.02" stroke-linejoin="round"/></svg>`;

  const STYLES = `
    :host{all:initial}
    #hud{--x:50vw;--y:46vh;--bubble-x:calc(50vw + 54px);--bubble-y:calc(46vh + 24px);--bubble-width:420px;--accent:#a994ff;position:fixed;inset:0;opacity:0;visibility:hidden;pointer-events:none;overflow:hidden;color:#eef5ff;font-family:Inter,Segoe UI,system-ui,sans-serif;transition:opacity .16s ease,visibility .16s ease}
    #hud.live{opacity:1;visibility:visible}
    .pill{position:absolute;top:20px;left:50%;z-index:60;transform:translateX(-50%);display:flex;align-items:center;gap:11px;max-width:min(720px,calc(100vw - 40px));padding:10px 15px;border-radius:999px;background:rgba(12,14,22,.84);border:1px solid rgba(169,148,255,.30);box-shadow:0 14px 34px rgba(0,0,0,.30),0 0 28px rgba(169,148,255,.12)}
    .dot{position:relative;width:8px;height:8px;border-radius:50%;background:var(--accent);flex:0 0 auto}.dot::before{content:"";position:absolute;inset:0;border-radius:50%;background:rgba(169,148,255,.45);animation:loomBrowserRing 1.45s steps(14,jump-none) infinite}.pill strong{color:#f8fbff;font-size:13px;font-weight:760;white-space:nowrap}.pill span:last-child{color:#aeb8cc;font-size:12px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .cursor{position:absolute;left:0;top:0;z-index:80;width:82px;height:82px;opacity:1!important;visibility:visible!important;transform:translate3d(var(--x),var(--y),0) translate(-10px,-10px);transition:transform 210ms cubic-bezier(.18,.78,.18,1);will-change:transform}.aura{position:absolute;left:34px;top:34px;width:72px;height:72px;border-radius:50%;transform:translate(-50%,-50%);background:radial-gradient(circle closest-side at 50% 50%,rgba(70,238,255,.18),rgba(150,122,255,.06) 54%,transparent);opacity:.66;animation:loomBrowserAura 2.2s steps(14,jump-none) infinite}.cursor svg{position:absolute;left:0;top:0;width:40px;height:40px;overflow:visible;transform-origin:10px 10.5px;animation:loomBrowserFloat 2.15s steps(14,jump-none) infinite;filter:drop-shadow(0 0 7px rgba(90,236,255,.38)) drop-shadow(0 6px 12px rgba(52,35,120,.26))}
    .wave{position:absolute;left:10px;top:10.5px;width:24px;height:24px;border-radius:50%;border:1.5px solid rgba(112,235,255,.95);box-shadow:0 0 18px rgba(120,105,255,.20);transform:translate(-50%,-50%) scale(.25);opacity:0}.cursor.clicking svg{animation:loomBrowserPress .34s cubic-bezier(.2,.8,.2,1)}.cursor.clicking .wave{animation:loomBrowserWave .6s ease-out}
    .bubble{position:absolute;left:0;top:0;z-index:40;width:var(--bubble-width);min-height:112px;padding:14px 15px 13px;border-radius:17px;background:rgba(8,17,27,.88);border:1px solid rgba(169,148,255,.24);box-shadow:0 16px 45px rgba(0,0,0,.40),0 0 26px rgba(169,148,255,.09);transform:translate3d(var(--bubble-x),var(--bubble-y),0) scale(.78);transform-origin:left top;transition:transform 210ms cubic-bezier(.18,.78,.18,1)}.bubble-title{min-height:17px;margin-bottom:7px;color:#f8fbff;font-size:13px;font-weight:780}.line{display:flex;justify-content:space-between;gap:12px;color:#9fb0c1;font-size:12px;line-height:1.7}.line b{max-width:58%;color:#e7f8ff;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:650;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.thought{margin-top:8px;color:#c6d4e6;font-size:12px;line-height:1.45;max-height:35px;overflow:hidden}
    @keyframes loomBrowserRing{0%{transform:scale(1);opacity:1}70%{transform:scale(3.25);opacity:0}100%{transform:scale(1);opacity:0}}@keyframes loomBrowserFloat{0%,100%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}50%{transform:translate(-.5px,-1.5px) rotate(-2.5deg) scale(1.009,.958)}}@keyframes loomBrowserAura{0%,100%{transform:translate(-50%,-50%) scale(.97);opacity:.52}50%{transform:translate(-50%,-50%) scale(1.07);opacity:.78}}@keyframes loomBrowserPress{0%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}45%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(.94,.90)}100%{transform:translate(-.5px,-.5px) rotate(-2.5deg) scale(1,.95)}}@keyframes loomBrowserWave{0%{opacity:1;transform:translate(-50%,-50%) scale(.35)}100%{opacity:0;transform:translate(-50%,-50%) scale(3.8)}}
    .edge{position:absolute;inset:0;overflow:hidden;pointer-events:none}
    .edge i{position:absolute;display:block;opacity:var(--min);background:conic-gradient(from 0deg at var(--cx) var(--cy),var(--ring));-webkit-mask-image:linear-gradient(var(--dir),var(--fade)),linear-gradient(var(--end),var(--ends));mask-image:linear-gradient(var(--dir),var(--fade)),linear-gradient(var(--end),var(--ends));-webkit-mask-composite:source-in;mask-composite:intersect}
    .edge .a{--ring:#59f5ff 0deg,#51c9ff 34deg,#7678ff 72deg,#bf60ff 112deg,#ff46bd 156deg,#ff4679 198deg,#ff9d45 234deg,#ffe54f 268deg,#a7f355 302deg,#3eedcb 336deg,#59f5ff 360deg;--fade:rgba(0,0,0,.5) 0px,rgba(0,0,0,.548) 5.667px,rgba(0,0,0,.527) 11.333px,rgba(0,0,0,.444) 17px,rgba(0,0,0,.316) 22.667px,rgba(0,0,0,.163) 28.333px,transparent 34px;--ends:rgba(0,0,0,.5) 0px,rgba(0,0,0,.691) 7px,rgba(0,0,0,.841) 14px,rgba(0,0,0,.933) 21px,rgba(0,0,0,.977) 28px,rgba(0,0,0,.994) 35px,rgba(0,0,0,.994) calc(100% - 35px),rgba(0,0,0,.977) calc(100% - 28px),rgba(0,0,0,.933) calc(100% - 21px),rgba(0,0,0,.841) calc(100% - 14px),rgba(0,0,0,.691) calc(100% - 7px),rgba(0,0,0,.5) 100%;--t:34px;--min:.22;--max:.50}
    .edge .b{--ring:#4df8ff 0deg,#48cbff 34deg,#7577ff 72deg,#c45dff 112deg,#ff42c3 156deg,#ff417a 198deg,#ff9b3c 234deg,#ffe642 268deg,#a3f649 302deg,#31f1cc 336deg,#4df8ff 360deg;--fade:rgba(0,0,0,.5) 0px,rgba(0,0,0,.525) 2.667px,rgba(0,0,0,.498) 5.333px,rgba(0,0,0,.421) 8px,rgba(0,0,0,.303) 10.667px,rgba(0,0,0,.159) 13.333px,transparent 16px;--ends:rgba(0,0,0,.5) 0px,rgba(0,0,0,.691) 4px,rgba(0,0,0,.841) 8px,rgba(0,0,0,.933) 12px,rgba(0,0,0,.977) 16px,rgba(0,0,0,.994) 20px,rgba(0,0,0,.994) calc(100% - 20px),rgba(0,0,0,.977) calc(100% - 16px),rgba(0,0,0,.933) calc(100% - 12px),rgba(0,0,0,.841) calc(100% - 8px),rgba(0,0,0,.691) calc(100% - 4px),rgba(0,0,0,.5) 100%;--t:16px;--min:.32;--max:.64}
    .edge .n{inset:0 0 auto 0;height:var(--t);--cx:50%;--cy:50vh;--dir:to bottom;--end:to right}
    .edge .s{inset:auto 0 0 0;height:var(--t);--cx:50%;--cy:calc(var(--t) - 50vh);--dir:to top;--end:to right}
    .edge .w{inset:0 auto 0 0;width:var(--t);--cx:50vw;--cy:50%;--dir:to right;--end:to bottom}
    .edge .e{inset:0 0 0 auto;width:var(--t);--cx:calc(var(--t) - 50vw);--cy:50%;--dir:to left;--end:to bottom}
    @media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
  `;

  let renderer = null;
  let sourceHost = null;
  let sourceRoot = null;
  let sourceObserver = null;
  let documentObserver = null;
  let lastX = null;
  let lastY = null;
  let lastActionNode = null;
  let tabId = null;
  let tabIdRequest = null;
  let sessionActive = false;
  let drivenTabs = [];
  let standalone = false;
  let refreshInFlight = null;
  let recoveryTimer = null;
  let storageListener = null;
  let breathTimer = 0;
  const motionQuery = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;

  const clean = (value, fallback = '') => String(value || fallback).replace(/\s+/g, ' ').trim().slice(0, 220);
  const clamp = (value, min, max) => Math.min(max, Math.max(min, Number.isFinite(Number(value)) ? Number(value) : min));

  function ensureLegacySuppression() {
    let style = document.getElementById(LEGACY_SUPPRESSOR_ID);
    if (!style) {
      style = document.createElement('style');
      style.id = LEGACY_SUPPRESSOR_ID;
      style.textContent = LEGACY_HOST_IDS.map((id) => `#${id}`).join(',') + '{display:none!important;visibility:hidden!important;opacity:0!important}';
      document.documentElement.appendChild(style);
    }
  }

  function ensureRenderer() {
    ensureLegacySuppression();
    if (renderer?.host?.isConnected) return renderer;
    let host = document.getElementById(HOST_ID);
    if (!host) {
      host = document.createElement('loom-browser-hud-v2');
      host.id = HOST_ID;
      host.setAttribute('aria-hidden', 'true');
      Object.assign(host.style, { position: 'fixed', inset: '0', zIndex: '2147483646', pointerEvents: 'none', contain: 'layout style paint' });
      document.documentElement.appendChild(host);
    }
    const root = host.shadowRoot || host.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>${STYLES}</style><div id="hud"><div class="edge"><i class="a n"></i><i class="a s"></i><i class="a w"></i><i class="a e"></i><i class="b n"></i><i class="b s"></i><i class="b w"></i><i class="b e"></i></div><div class="pill"><i class="dot"></i><strong id="title">Loom 正在控制浏览器</strong><span id="meta">Browser Use</span></div><div id="cursor" class="cursor"><div class="aura"></div>${SVG_CURSOR}<div class="wave"></div></div><div class="bubble"><div id="bubbleTitle" class="bubble-title">准备浏览器自动化</div><div class="line"><span>目标置信度</span><b id="confidence">browser</b></div><div class="line"><span>执行坐标</span><b id="point">—</b></div><div class="line"><span>动作来源</span><b id="source">browser runtime</b></div><div id="thought" class="thought">等待下一步浏览器动作。</div></div></div>`;
    renderer = {
      host, root,
      hud: root.getElementById('hud'), glow: [...root.querySelectorAll('.edge i')], title: root.getElementById('title'), meta: root.getElementById('meta'), cursor: root.getElementById('cursor'),
      bubbleTitle: root.getElementById('bubbleTitle'), confidence: root.getElementById('confidence'), point: root.getElementById('point'), source: root.getElementById('source'),
      thought: root.getElementById('thought'),
    };
    place(null);
    return renderer;
  }

  // The glow breathes on a timer, not on an infinite CSS animation: a compositor animation over the
  // whole viewport keeps the GPU producing a frame for every refresh (this overlay used to take the
  // whole integrated GPU of the machine), while a 12 Hz timer only asks for what the eye can use.
  function breathe() {
    const nodes = renderer?.glow;
    if (!nodes) return;
    const t = performance.now() / 1000;
    for (const node of nodes) {
      const inner = node.classList.contains('b');
      const k = 0.5 - 0.5 * Math.cos(2 * Math.PI * (((t + (inner ? 0.45 : 0)) / 1.55) % 1));
      const lo = inner ? 0.32 : 0.22;
      const hi = inner ? 0.64 : 0.5;
      node.style.opacity = (lo + (hi - lo) * k).toFixed(3);
    }
  }

  function breathing(on) {
    const run = on && !motionQuery?.matches && !document.hidden;
    if (run && !breathTimer) { breathe(); breathTimer = setInterval(breathe, 83); }
    else if (!run && breathTimer) { clearInterval(breathTimer); breathTimer = 0; }
  }

  // The glow runs exactly while the HUD is live (and its page is visible); the live class stays
  // where it has always been decided.
  function syncBreath() {
    breathing(Boolean(renderer?.hud.classList.contains('live')));
  }

  function hide() {
    if (renderer) renderer.hud.classList.remove('live');
    syncBreath();
  }

  function extractPresentation() {
    if (!sourceRoot) return null;
    const legacyLayer = sourceRoot.getElementById?.(LEGACY_LAYER_ID);
    if (legacyLayer) legacyLayer.style.display = 'none';
    const target = sourceRoot.querySelector('.loom-page-hud-target');
    const label = sourceRoot.querySelector('.loom-page-hud-label');
    const status = sourceRoot.querySelector('.loom-page-hud-pill');
    const actionNode = label || status || target;
    if (!actionNode) return null;
    const title = clean(label?.querySelector('strong')?.textContent || status?.querySelector('strong')?.textContent || status?.textContent, 'Browser Use');
    const subtitle = clean(label?.querySelector('span')?.textContent || status?.textContent || 'Browser Use', 'Browser Use');
    let point = null;
    if (target) {
      const rect = target.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        point = { x: clamp(rect.left + rect.width / 2, 0, Math.max(1, innerWidth)), y: clamp(rect.top + rect.height / 2, 0, Math.max(1, innerHeight)) };
      }
    }
    return { actionNode, title, subtitle, point };
  }

  function place(point) {
    const view = renderer || ensureRenderer();
    const width = Math.max(1, innerWidth);
    const height = Math.max(1, innerHeight);
    if (point) { lastX = point.x; lastY = point.y; }
    if (lastX === null || lastY === null) { lastX = width * 0.5; lastY = height * 0.46; }
    lastX = clamp(lastX, 12, Math.max(12, width - 12));
    lastY = clamp(lastY, 12, Math.max(12, height - 12));
    const bubbleWidth = Math.min(420, Math.max(320, width - 48));
    const bx = clamp(lastX > width * 0.58 ? lastX - bubbleWidth - 52 : lastX + 52, 18, Math.max(18, width - bubbleWidth - 18));
    const by = clamp(lastY > height * 0.58 ? lastY - 172 : lastY + 26, 18, Math.max(18, height - 160));
    view.hud.style.setProperty('--x', `${lastX}px`);
    view.hud.style.setProperty('--y', `${lastY}px`);
    view.hud.style.setProperty('--bubble-x', `${bx}px`);
    view.hud.style.setProperty('--bubble-y', `${by}px`);
    view.hud.style.setProperty('--bubble-width', `${bubbleWidth}px`);
    view.point.textContent = `${Math.round(lastX)}, ${Math.round(lastY)}`;
  }

  function pulseClick() {
    const view = ensureRenderer();
    view.cursor.classList.remove('clicking');
    void view.cursor.offsetWidth;
    view.cursor.classList.add('clicking');
    setTimeout(() => view.cursor.classList.remove('clicking'), 620);
  }

  function renderFromSource() {
    const presentation = extractPresentation();
    if (!presentation) return;
    const view = ensureRenderer();
    const isNewAction = presentation.actionNode !== lastActionNode;
    lastActionNode = presentation.actionNode;
    place(presentation.point);
    // Content is always kept current; whether it is on screen stays the decision
    // of the session gate, so an action's markup can never reveal the HUD in a
    // tab Loom is not driving.
    if (driven()) view.hud.classList.add('live');
    syncBreath();
    view.title.textContent = 'Loom 正在控制浏览器';
    view.meta.textContent = presentation.title || 'Browser Use';
    view.bubbleTitle.textContent = presentation.title || 'Browser action';
    view.confidence.textContent = presentation.point ? 'DOM exact' : 'browser';
    view.source.textContent = presentation.point ? 'browser + DOM' : 'browser runtime';
    view.thought.textContent = presentation.subtitle || 'Browser Use';
    if (isNewAction && /^click\b/i.test(presentation.title)) pulseClick();
  }

  function attachSource(nextHost) {
    const nextRoot = nextHost?.shadowRoot || null;
    if (sourceHost === nextHost && sourceRoot === nextRoot) { renderFromSource(); return; }
    sourceObserver?.disconnect();
    sourceHost = nextHost || null;
    sourceRoot = nextRoot;
    if (sourceRoot) {
      sourceObserver = new MutationObserver(renderFromSource);
      sourceObserver.observe(sourceRoot, { childList: true, subtree: true, characterData: true, attributes: true });
      renderFromSource();
    } else {
      sourceObserver = null;
    }
  }

  function sync() {
    ensureLegacySuppression();
    // SPA frameworks can replace the document's children without a navigation.
    // Recreate the renderer from ownership, not from an expiring action marker.
    if (driven() && !renderer?.host?.isConnected) applySession(true);
    attachSource(document.getElementById(SOURCE_HOST_ID));
  }

  function applySession(active) {
    // The whole point of the session flag: a page load wipes the HUD out of the
    // document, and the per-action source host only exists while an action runs,
    // so visibility driven by that host meant the HUD vanished on every refresh
    // and came back on the next click. While Loom drives this tab, it shows.
    if (!active) {
      renderer?.hud.classList.remove('live');
      syncBreath();
      return;
    }
    const view = ensureRenderer();
    view.hud.classList.add('live');
    syncBreath();
    place(lastX === null ? null : { x: lastX, y: lastY });
  }

  function driven() {
    return standalone || (sessionActive && tabId !== null && drivenTabs.includes(tabId));
  }

  // Loom also drives browsers that contain no extension at all: one it launched
  // itself, or one it attached to over CDP. There this file is injected through
  // the protocol rather than loaded as a content script, so there is no worker
  // to ask for a tab id and no session storage to read, and the session gate can
  // only ever answer "not a work tab". The injector is by construction the thing
  // driving the page, so it says so by calling present() and the gate steps
  // aside. Content-script pages never reach this and keep deciding as before.
  function present(payload) {
    const data = payload && typeof payload === 'object' ? payload : {};
    standalone = true;
    const view = ensureRenderer();
    const title = clean(data.title, 'Browser Use');
    const subtitle = clean(data.subtitle, 'Browser Use');
    const x = Number(data.x);
    const y = Number(data.y);
    const point = Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    place(point);
    view.hud.classList.add('live');
    syncBreath();
    view.title.textContent = 'Loom 正在控制浏览器';
    view.meta.textContent = title;
    view.bubbleTitle.textContent = title;
    view.confidence.textContent = point ? 'DOM exact' : 'browser';
    view.source.textContent = point ? 'browser + CDP' : 'browser runtime';
    view.thought.textContent = subtitle;
    if (data.click) pulseClick();
    return true;
  }

  function syncVisibility() {
    applySession(driven());
  }

  const normalizeIds = (value) => (Array.isArray(value) ? value.filter((id) => Number.isInteger(id)) : []);

  // Asked once per page. A content script has no API for its own tab id, and the
  // answer cannot change under it, so the pending request is shared rather than
  // repeated for every storage change that arrives before it lands.
  function ensureTabId() {
    if (tabId !== null) return Promise.resolve();
    if (tabIdRequest) return tabIdRequest;
    tabIdRequest = (async () => {
      try {
        const reply = await chrome.runtime.sendMessage({ type: HUD_TAB_QUERY });
        tabId = Number.isInteger(reply?.tab_id) ? reply.tab_id : null;
      } catch (_) {
        // No worker, or an older one that does not answer. Without an identity
        // this page cannot claim to be a work tab, and staying hidden is right.
        tabId = null;
      }
    })();
    tabIdRequest = tabIdRequest.finally(() => { tabIdRequest = null; });
    return tabIdRequest;
  }

  async function refreshSession() {
    if (standalone) return;
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = (async () => {
    try {
      await ensureTabId();
      const stored = await chrome.storage.session.get(SESSION_ACTIVE_KEY);
      sessionActive = Boolean(stored?.[SESSION_ACTIVE_KEY]);
      const tabs = await chrome.storage.session.get(HUD_TAB_IDS_KEY);
      drivenTabs = normalizeIds(tabs?.[HUD_TAB_IDS_KEY]);
      syncVisibility();
    } catch (_) {
      // An older worker has not opened session storage to content scripts yet.
      // Staying hidden is right: nothing here proves a session is running.
    }
    })();
    try { await refreshInFlight; } finally { refreshInFlight = null; }
  }

  function watchSession() {
    try {
      storageListener = (changes, area) => {
        if (area !== 'session') return;
        if (!(SESSION_ACTIVE_KEY in changes) && !(HUD_TAB_IDS_KEY in changes)) return;
        if (SESSION_ACTIVE_KEY in changes) sessionActive = Boolean(changes[SESSION_ACTIVE_KEY].newValue);
        if (HUD_TAB_IDS_KEY in changes) drivenTabs = normalizeIds(changes[HUD_TAB_IDS_KEY].newValue);
        void ensureTabId().then(syncVisibility);
      };
      chrome.storage.onChanged.addListener(storageListener);
    } catch (_) {}
  }

  function onResize() {
    if (renderer?.hud.classList.contains('live')) place(null);
  }

  function dispose() {
    clearInterval(recoveryTimer);
    clearInterval(breathTimer);
    breathTimer = 0;
    document.removeEventListener('visibilitychange', syncBreath);
    try { if (storageListener) chrome.storage.onChanged.removeListener(storageListener); } catch (_) {}
    sourceObserver?.disconnect();
    documentObserver?.disconnect();
    removeEventListener('resize', onResize);
    renderer?.host?.remove();
    renderer = null;
    sourceHost = null;
    sourceRoot = null;
    if (globalThis[INSTALL_KEY]?.generation === GENERATION) delete globalThis[INSTALL_KEY];
  }

  function begin() {
    // No renderer until this tab turns out to be one Loom drives. This script
    // loads in every web page, and building the shadow host eagerly put a fixed
    // full-viewport overlay and two infinite conic-gradient animations into
    // every tab the user had open to render nothing. Everything that needs the
    // renderer calls ensureRenderer() itself.
    sync();
    void refreshSession();
    // document_start can race worker startup/session-storage availability.
    // Failed identity reads are retried, never interpreted as ownership.
    recoveryTimer = setInterval(() => { void refreshSession(); sync(); }, 2000);
    watchSession();
    documentObserver = new MutationObserver(sync);
    documentObserver.observe(document.documentElement, { childList: true, subtree: true });
    addEventListener('resize', onResize, { passive: true });
    document.addEventListener('visibilitychange', syncBreath);
  }

  globalThis[INSTALL_KEY] = { generation: GENERATION, sync, hide, dispose, present };
  if (document.documentElement) begin();
  else addEventListener('DOMContentLoaded', begin, { once: true });
})();
