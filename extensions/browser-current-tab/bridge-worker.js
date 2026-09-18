const HUD_SCRIPTS = ['browser-hud.js'];
const INSTALL_KEY = '__loomBrowserHudRuntimeV2';
const isWebUrl = (value) => /^https?:\/\//i.test(String(value || ''));

// background.js owns the bridge protocol. This tiny wrapper handles the one
// lifecycle command that should not become a Browser action: when a Loom turn
// finishes, every page-local HUD fades out without closing tabs, dropping login
// state, or destroying the browser session. The next Browser action wakes the
// persistent renderer again through the normal page HUD source.
const nativeFetch = globalThis.fetch.bind(globalThis);

async function hideBrowserHudEverywhere() {
  let hidden = 0;
  try {
    const tabs = await chrome.tabs.query({});
    await Promise.all(tabs.map(async (tab) => {
      if (!Number.isInteger(tab.id) || !isWebUrl(tab.url)) return;
      try {
        const results = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: 'ISOLATED',
          func: () => {
            try { window.clearTimeout(window.__loomBrowserPageHudTimer); } catch (_) {}
            try { document.getElementById('loom-browser-page-hud-root')?.remove(); } catch (_) {}

            let changed = false;
            for (const id of ['loom-browser-hud-root-v2', 'loom-browser-hud-root']) {
              const host = document.getElementById(id);
              const hud = host?.shadowRoot?.getElementById('hud');
              if (hud?.classList.contains('live')) {
                hud.classList.remove('live');
                changed = true;
              }
            }
            try {
              const runtime = globalThis.__loomBrowserHudRuntimeV2;
              if (runtime && typeof runtime.hide === 'function') runtime.hide();
            } catch (_) {}
            return changed;
          },
        });
        if (results?.[0]?.result === true) hidden += 1;
      } catch (_) {
        // A tab can close or become privileged while turn teardown is running.
      }
    }));
  } catch (_) {}
  return { hidden };
}

globalThis.fetch = async (input, init = {}) => {
  const response = await nativeFetch(input, init);
  const url = String(input instanceof Request ? input.url : input || '');
  if (!url.includes('/browser-extension/v1/poll')) return response;

  let payload = null;
  try { payload = await response.clone().json(); } catch (_) { return response; }
  const command = payload?.command;
  if (!command || command.action !== 'hud_end') return response;

  let result = { hidden: 0 };
  let ok = true;
  let error = '';
  try {
    result = await hideBrowserHudEverywhere();
  } catch (cause) {
    ok = false;
    error = cause instanceof Error ? cause.message : String(cause);
  }

  try {
    const headers = new Headers(init?.headers || {});
    headers.set('Content-Type', 'application/json');
    const base = url.split('/browser-extension/v1/poll', 1)[0];
    await nativeFetch(`${base}/browser-extension/v1/result`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ id: command.id, ok, result, error }),
    });
  } catch (_) {
    // If the bridge disappears during turn teardown, there is nothing left to ack.
  }

  return new Response(JSON.stringify({ ok: true, command: null }), {
    status: 200,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
};

// Semantically this is the old `import './background.js'` module load, but it is
// deliberately dynamic so the hud_end interceptor above exists before the bridge
// starts its long-poll loop.
await import('./background.js');

async function hudHostInstalled(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'ISOLATED',
      func: (key) => Boolean(globalThis[key]),
      args: [INSTALL_KEY],
    });
    return results?.[0]?.result === true;
  } catch (_) {
    return false;
  }
}

async function ensureHudScripts(tabId, url = '') {
  if (!Number.isInteger(tabId) || !isWebUrl(url)) return;
  try {
    if (await hudHostInstalled(tabId)) return;
    await chrome.scripting.executeScript({
      target: { tabId },
      files: HUD_SCRIPTS,
      world: 'ISOLATED',
    });
  } catch (_) {
    // Tabs can disappear or become privileged while an injection is in flight.
  }
}

async function repairExistingTabs() {
  try {
    const tabs = await chrome.tabs.query({});
    await Promise.all(tabs.map((tab) => ensureHudScripts(tab.id, tab.url)));
  } catch (_) {}
}

chrome.tabs.onActivated.addListener(({ tabId }) => {
  void chrome.tabs.get(tabId).then((tab) => ensureHudScripts(tabId, tab.url)).catch(() => {});
});

// Manifest content scripts own normal navigations. This startup pass exists for
// tabs that were already open when an unpacked extension was reloaded/upgraded.
// The marker is generation-specific so an extension upgrade can actually replace
// an older HUD runtime already living in an open tab.
void repairExistingTabs();
