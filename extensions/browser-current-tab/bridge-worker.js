import './background.js';

const HUD_SCRIPTS = ['hud-computer-overlay.js', 'hud-computer-host.js'];
const INSTALL_KEY = '__loomBrowserHudHostV2';
const isWebUrl = (value) => /^https?:\/\//i.test(String(value || ''));

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
// the one case manifest scripts cannot repair by themselves: tabs that were
// already open when an unpacked extension was reloaded/upgraded.
void repairExistingTabs();
