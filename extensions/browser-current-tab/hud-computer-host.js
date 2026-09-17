(() => {
  'use strict';

  const INSTALL_KEY = '__loomBrowserHudHostV2';
  if (globalThis[INSTALL_KEY]) {
    try { globalThis[INSTALL_KEY].sync(); } catch (_) {}
    return;
  }

  const SOURCE_HOST_ID = 'loom-browser-page-hud-root';
  const SOURCE_LAYER_ID = 'loom-computer-hud-layer';
  const HOST_ID = 'loom-browser-computer-hud-root';
  // Keep the original layer id inside the separate ShadowRoot so the Computer HUD
  // CSS copied from the source layer continues to match without any selector rewrite.
  const CLONE_ID = SOURCE_LAYER_ID;

  let sourceHost = null;
  let sourceRoot = null;
  let sourceObserver = null;
  let scheduled = false;

  const PARITY_OVERRIDES = `
    #${CLONE_ID}{--bubble-width:420px!important}
    #${CLONE_ID} .loom-cu-edge::before{opacity:.42!important;-webkit-mask:linear-gradient(to bottom,#000,transparent) top/100% 34px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 34px no-repeat,linear-gradient(to right,#000,transparent) left/34px 100% no-repeat,linear-gradient(to left,#000,transparent) right/34px 100% no-repeat!important;mask:linear-gradient(to bottom,#000,transparent) top/100% 34px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 34px no-repeat,linear-gradient(to right,#000,transparent) left/34px 100% no-repeat,linear-gradient(to left,#000,transparent) right/34px 100% no-repeat!important}
    #${CLONE_ID} .loom-cu-edge::after{opacity:.68!important;-webkit-mask:linear-gradient(to bottom,#000,transparent) top/100% 16px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 16px no-repeat,linear-gradient(to right,#000,transparent) left/16px 100% no-repeat,linear-gradient(to left,#000,transparent) right/16px 100% no-repeat!important;mask:linear-gradient(to bottom,#000,transparent) top/100% 16px no-repeat,linear-gradient(to top,#000,transparent) bottom/100% 16px no-repeat,linear-gradient(to right,#000,transparent) left/16px 100% no-repeat,linear-gradient(to left,#000,transparent) right/16px 100% no-repeat!important}
    #${CLONE_ID} .loom-cu-pill{top:20px!important;gap:11px!important;max-width:min(720px,calc(100vw - 40px))!important;padding:10px 15px!important;box-shadow:0 14px 34px rgba(0,0,0,.30),0 0 28px rgba(169,148,255,.12)!important}
    #${CLONE_ID} .loom-cu-bubble{min-height:112px!important;padding:14px 15px 13px!important;box-shadow:0 16px 45px rgba(0,0,0,.40),0 0 26px rgba(169,148,255,.09)!important;transform:translate3d(var(--bubble-x),var(--bubble-y),0) scale(.78)!important}
    #${CLONE_ID} .loom-cu-timeline{bottom:24px!important;gap:8px!important;padding:9px!important;box-shadow:0 15px 34px rgba(0,0,0,.32)!important}
    #${CLONE_ID} .loom-cu-phase{padding:7px 10px!important}
  `;

  function ensureHost() {
    let host = document.getElementById(HOST_ID);
    if (!host) {
      host = document.createElement('loom-browser-computer-hud');
      host.id = HOST_ID;
      host.setAttribute('aria-hidden', 'true');
      Object.assign(host.style, {
        position: 'fixed',
        inset: '0',
        zIndex: '2147483646',
        pointerEvents: 'none',
        contain: 'layout style paint',
      });
      document.documentElement.appendChild(host);
    }
    return { host, root: host.shadowRoot || host.attachShadow({ mode: 'open' }) };
  }

  function removeHost() {
    document.getElementById(HOST_ID)?.remove();
  }

  function cloneComputerLayer(layer) {
    const { root } = ensureHost();
    const clone = layer.cloneNode(true);
    clone.id = CLONE_ID;
    clone.style.display = '';
    const viewportWidth = Math.max(1, innerWidth);
    clone.style.setProperty('--bubble-width', `${Math.min(420, Math.max(310, viewportWidth - 48))}px`);
    const parity = document.createElement('style');
    parity.textContent = PARITY_OVERRIDES;
    root.replaceChildren(clone, parity);
    // Keep the legacy in-root layer as the data/render source but never paint it.
    layer.style.display = 'none';
  }

  function render() {
    scheduled = false;
    const liveHost = document.getElementById(SOURCE_HOST_ID);
    const liveRoot = liveHost?.shadowRoot || null;
    if (!liveRoot) {
      removeHost();
      return;
    }
    const layer = liveRoot.querySelector(`#${SOURCE_LAYER_ID}`);
    if (!layer) {
      removeHost();
      return;
    }
    cloneComputerLayer(layer);
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(render);
  }

  function attach(nextHost) {
    const nextRoot = nextHost?.shadowRoot || null;
    if (sourceHost === nextHost && sourceRoot === nextRoot) {
      schedule();
      return;
    }
    sourceObserver?.disconnect();
    sourceHost = nextHost || null;
    sourceRoot = nextRoot;
    if (sourceRoot) {
      sourceObserver = new MutationObserver(schedule);
      sourceObserver.observe(sourceRoot, { childList: true, subtree: true });
    } else {
      sourceObserver = null;
    }
    schedule();
  }

  function sync() {
    attach(document.getElementById(SOURCE_HOST_ID));
  }

  const documentObserver = new MutationObserver(sync);
  function begin() {
    sync();
    documentObserver.observe(document.documentElement, { childList: true, subtree: true });
    addEventListener('resize', schedule, { passive: true });
  }

  globalThis[INSTALL_KEY] = { sync };
  if (document.documentElement) begin();
  else addEventListener('DOMContentLoaded', begin, { once: true });
})();
