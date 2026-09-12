const PROTOCOL_VERSION = 1;
const DEFAULT_BRIDGE_URL = "http://127.0.0.1:39222";
const DEFAULT_TOKEN = "loom-dev-browser-extension";
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
// How long to keep looking for a navigation an element action may have started.
const NAVIGATION_GRACE_MS = 300;
// How long to wait when the page said the action does start one.
const NAVIGATION_COMMIT_TIMEOUT_MS = 8000;
const CLIENT_ID_KEY = "loomBrowserBridgeClientId";

let polling = false;
const lastElementsByTab = new Map();

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const randomId = () => globalThis.crypto?.randomUUID?.() || `loom-${Date.now()}-${Math.random().toString(16).slice(2)}`;

async function getClientId() {
  const stored = await chrome.storage.local.get(CLIENT_ID_KEY);
  if (typeof stored[CLIENT_ID_KEY] === "string" && stored[CLIENT_ID_KEY]) return stored[CLIENT_ID_KEY];
  const next = randomId();
  await chrome.storage.local.set({ [CLIENT_ID_KEY]: next });
  return next;
}

async function getConfig() {
  const stored = await chrome.storage.local.get(["bridgeUrl", "token"]);
  return {
    bridgeUrl: String(stored.bridgeUrl || DEFAULT_BRIDGE_URL).replace(/\/+$/, ""),
    token: String(stored.token || DEFAULT_TOKEN),
  };
}

async function bridgeFetch(path, options = {}) {
  const config = await getConfig();
  const headers = new Headers(options.headers || {});
  headers.set("X-Loom-Token", config.token);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return fetch(`${config.bridgeUrl}${path}`, { ...options, headers });
}

async function register() {
  await bridgeFetch("/browser-extension/v1/register", {
    method: "POST",
    body: JSON.stringify({
      client_id: await getClientId(),
      version: EXTENSION_VERSION,
      protocol_version: PROTOCOL_VERSION,
    }),
  });
}

async function pollOnce() {
  const clientId = encodeURIComponent(await getClientId());
  const version = encodeURIComponent(EXTENSION_VERSION);
  const response = await bridgeFetch(`/browser-extension/v1/poll?client_id=${clientId}&version=${version}`);
  if (!response.ok) throw new Error(`poll failed: HTTP ${response.status}`);
  const payload = await response.json();
  if (!payload.command) return;
  const command = payload.command;
  let result = null;
  let ok = false;
  let error = "";
  try {
    result = await dispatchCommand(command.action, command.args || {});
    ok = true;
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }
  await bridgeFetch("/browser-extension/v1/result", {
    method: "POST",
    body: JSON.stringify({ id: command.id, ok, result, error }),
  });
}

async function startPolling() {
  if (polling) return;
  polling = true;
  let backoff = 500;
  for (;;) {
    try {
      await register();
      await pollOnce();
      backoff = 500;
    } catch (cause) {
      console.warn("[loom-browser-bridge]", cause);
      await sleep(backoff);
      backoff = Math.min(Math.round(backoff * 1.6), 10000);
    }
  }
}

function queryActiveTab() {
  return new Promise((resolve, reject) => {
    chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
      const error = chrome.runtime.lastError;
      if (error) return reject(new Error(error.message));
      if (tabs?.length) return resolve(tabs[0]);
      chrome.tabs.query({ active: true, currentWindow: true }, (fallback) => {
        const fallbackError = chrome.runtime.lastError;
        if (fallbackError) reject(new Error(fallbackError.message));
        else if (fallback?.length) resolve(fallback[0]);
        else reject(new Error("No active browser tab is available"));
      });
    });
  });
}

async function tabFromArgs(args = {}) {
  const raw = String(args.tab_id || "").trim();
  if (raw) {
    const tabId = Number.parseInt(raw, 10);
    if (!Number.isInteger(tabId)) throw new Error("tab_id must be numeric for the extension backend");
    return chrome.tabs.get(tabId);
  }
  const tab = await queryActiveTab();
  if (!tab || typeof tab.id !== "number") throw new Error("Active browser tab has no tab id");
  return tab;
}

async function tabsForWindow(tab) {
  const tabs = await chrome.tabs.query(typeof tab.windowId === "number" ? { windowId: tab.windowId } : { currentWindow: true });
  return tabs.map((item) => ({
    tab_id: String(item.id ?? ""),
    url: item.url || "",
    title: item.title || "",
    active: item.id === tab.id,
  }));
}

const isInjectableUrl = (url) => /^https?:\/\//i.test(String(url || ""));

async function inject(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return results?.[0]?.result || {};
}

async function collectStateForTab(tab, options = {}) {
  const base = {
    url: tab.url || "about:blank",
    title: tab.title || "",
    dom: "",
    tabs: await tabsForWindow(tab),
    page_info: {
      extension_version: EXTENSION_VERSION,
      tab_id: String(tab.id),
      injectable: isInjectableUrl(tab.url || ""),
      browser_hud: "page-local",
    },
    errors: [],
  };
  if (!isInjectableUrl(tab.url || "")) {
    base.errors.push("Loom can list this tab but cannot inspect chrome://, edge://, extension, file, or other privileged pages.");
    return base;
  }
  try {
    const page = await inject(tab.id, runPageAction, ["state", { show_hud: options.showHud !== false }]);
    lastElementsByTab.set(tab.id, Array.isArray(page.elements) ? page.elements : []);
    return {
      ...base,
      url: page.url || base.url,
      title: page.title || base.title,
      dom: page.dom || "",
      page_info: { ...base.page_info, ...(page.page_info || {}) },
      errors: Array.isArray(page.errors) ? page.errors : [],
    };
  } catch (cause) {
    return { ...base, errors: [cause instanceof Error ? cause.message : String(cause)] };
  }
}

function elementRefFor(tabId, index) {
  const item = (lastElementsByTab.get(tabId) || [])[Number(index)];
  if (!item?.loom_id) throw new Error("Element index is not available. Refresh browser_state and retry.");
  return item.loom_id;
}

// Element actions can start a navigation. navigate() and refresh() already wait
// for the tab to finish loading, but click/type/select/drag/press/go_back used to
// return after a fixed sleep, so a click on a link reported the page it had just
// left. The model reads that as "my click did nothing" and clicks again.
function watchTabNavigation(tabId) {
  let started = false;
  let announce = () => {};
  const startedPromise = new Promise((resolve) => { announce = resolve; });
  const listener = (id, info) => {
    if (id !== tabId) return;
    if (info.status === "loading" || typeof info.url === "string") {
      started = true;
      announce();
    }
  };
  chrome.tabs.onUpdated.addListener(listener);
  return {
    get started() { return started; },
    startedPromise,
    stop() { chrome.tabs.onUpdated.removeListener(listener); },
  };
}

async function afterTabAction(tab, waitMs = 200, watcher = null, expectNavigation = false) {
  await sleep(waitMs);
  let navWaited = false;
  if (watcher) {
    if (!watcher.started) {
      // A page that told us it is navigating gets the long window, because the
      // commit can take seconds on a slow origin. Everything else pays only the
      // short one, so an action that navigates nothing stays fast.
      const grace = expectNavigation ? NAVIGATION_COMMIT_TIMEOUT_MS : NAVIGATION_GRACE_MS;
      await Promise.race([watcher.startedPromise, sleep(grace)]);
    }
    if (watcher.started) {
      await waitForTabComplete(tab.id);
      navWaited = true;
    }
  }
  const state = await collectStateForTab(await chrome.tabs.get(tab.id), { showHud: false });
  state.page_info = { ...(state.page_info || {}), nav_waited: navWaited };
  return state;
}

async function withNavigationWatch(tabId, run, waitMs) {
  const watcher = watchTabNavigation(tabId);
  try {
    const outcome = await run();
    const expected = Boolean(outcome && outcome.navigation_expected);
    return await afterTabAction({ id: tabId }, waitMs, watcher, expected);
  } finally {
    watcher.stop();
  }
}

async function navigate(args) {
  const url = String(args.url || "");
  if (!url) throw new Error("url is required");
  const existing = await tabFromArgs(args);
  if (isInjectableUrl(existing.url || "")) {
    await inject(existing.id, runPageAction, ["hud_status", { title: "Opening page", subtitle: url }]).catch(() => {});
  }
  const tab = args.new_tab
    ? await chrome.tabs.create({ url, active: true })
    : await chrome.tabs.update(existing.id, { url, active: true });
  await waitForTabComplete(tab.id);
  return collectStateForTab(await chrome.tabs.get(tab.id), { showHud: true });
}

async function waitForTabComplete(tabId) {
  const started = Date.now();
  while (Date.now() - started < 15000) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return;
    await sleep(250);
  }
}

async function withElement(args, action, extra = {}, waitMs = 200) {
  const tab = await tabFromArgs(args);
  const loomId = elementRefFor(tab.id, args.index);
  return withNavigationWatch(
    tab.id,
    () => inject(tab.id, runPageAction, [action, { ...extra, loom_id: loomId, index: Number(args.index) }]),
    waitMs,
  );
}

async function click(args) {
  return withElement(args, "click", {}, 250);
}

async function hover(args) {
  return withElement(args, "hover", {}, 150);
}

async function typeText(args) {
  return withElement(args, "type_text", { text: String(args.text || ""), clear: args.clear !== false }, 200);
}

async function selectOption(args) {
  return withElement(args, "select_option", { value: String(args.value || "") }, 200);
}

async function drag(args) {
  const tab = await tabFromArgs(args);
  const payload = {
    source_loom_id: elementRefFor(tab.id, args.source_index),
    target_loom_id: elementRefFor(tab.id, args.target_index),
    source_index: Number(args.source_index),
    target_index: Number(args.target_index),
  };
  return withNavigationWatch(tab.id, () => inject(tab.id, runPageAction, ["drag", payload]), 250);
}

async function pressKey(args) {
  const tab = await tabFromArgs(args);
  // Enter in a form field is the usual way a key press turns into a navigation.
  return withNavigationWatch(
    tab.id,
    () => inject(tab.id, runPageAction, ["press_key", { key: String(args.key || "") }]),
    150,
  );
}

async function scroll(args) {
  const tab = await tabFromArgs(args);
  await inject(tab.id, runPageAction, ["scroll", { direction: String(args.direction || "down"), amount: Number(args.amount || 700) }]);
  return afterTabAction(tab, 150);
}

async function goBack(args) {
  const tab = await tabFromArgs(args);
  if (!isInjectableUrl(tab.url || "")) throw new Error("Cannot go back from a privileged browser page");
  return withNavigationWatch(tab.id, () => inject(tab.id, runPageAction, ["go_back", {}]), 500);
}

async function refresh(args) {
  const tab = await tabFromArgs(args);
  if (isInjectableUrl(tab.url || "")) {
    await inject(tab.id, runPageAction, ["hud_status", { title: "Refreshing tab", subtitle: "Browser Use" }]).catch(() => {});
  }
  await chrome.tabs.reload(tab.id);
  await waitForTabComplete(tab.id);
  return collectStateForTab(await chrome.tabs.get(tab.id), { showHud: true });
}

async function switchTab(args) {
  const tabId = Number.parseInt(String(args.tab_id || ""), 10);
  if (!Number.isInteger(tabId)) throw new Error("tab_id must be numeric for the extension backend");
  await chrome.tabs.update(tabId, { active: true });
  return collectStateForTab(await chrome.tabs.get(tabId), { showHud: true });
}

async function closeTab(args) {
  const tabId = Number.parseInt(String(args.tab_id || ""), 10);
  if (!Number.isInteger(tabId)) throw new Error("tab_id must be numeric for the extension backend");
  await chrome.tabs.remove(tabId);
  return collectStateForTab(await tabFromArgs({}), { showHud: true });
}

async function screenshot(args) {
  const tab = await tabFromArgs(args);
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  if (isInjectableUrl(tab.url || "")) {
    await inject(tab.id, runPageAction, ["hud_status", { title: "Screenshot captured", subtitle: "Saved into Loom workspace" }]).catch(() => {});
  }
  const marker = "base64,";
  const offset = dataUrl.indexOf(marker);
  if (offset < 0) throw new Error("captureVisibleTab did not return base64 PNG data");
  return { png_base64: dataUrl.slice(offset + marker.length), full_page: false };
}

async function dispatchCommand(action, args) {
  switch (action) {
    case "state": return collectStateForTab(await tabFromArgs(args), { showHud: true });
    case "navigate": return navigate(args);
    case "click": return click(args);
    case "hover": return hover(args);
    case "type_text": return typeText(args);
    case "select_option": return selectOption(args);
    case "drag": return drag(args);
    case "press_key": return pressKey(args);
    case "scroll": return scroll(args);
    case "go_back": return goBack(args);
    case "refresh": return refresh(args);
    case "tabs": return collectStateForTab(await tabFromArgs(args), { showHud: true });
    case "switch_tab": return switchTab(args);
    case "close_tab": return closeTab(args);
    case "screenshot": return screenshot(args);
    default: throw new Error(`Unsupported Loom browser extension action: ${action}`);
  }
}

function runPageAction(action, args = {}) {
  const HUD_HOST_ID = "loom-browser-page-hud-root";
  const MAX_TEXT = 16000;
  const MAX_ELEMENTS = 300;

  function clean(value, limit = 160) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function visible(el) {
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom >= 0 && rect.right >= 0 &&
      rect.top <= window.innerHeight && rect.left <= window.innerWidth;
  }

  function isCandidate(el) {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (["a", "button", "input", "textarea", "select", "summary"].includes(tag)) return true;
    if (["button", "link", "menuitem", "option", "tab", "checkbox", "radio", "switch", "textbox"].includes(role)) return true;
    if (el.isContentEditable || typeof el.onclick === "function") return true;
    const tabindex = el.getAttribute("tabindex");
    return tabindex !== null && tabindex !== "-1";
  }

  function ensureId(el) {
    if (!el.dataset.loomBridgeId) {
      el.dataset.loomBridgeId = crypto.randomUUID ? crypto.randomUUID() : `loom-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }
    return el.dataset.loomBridgeId;
  }

  function ensureHud() {
    let host = document.getElementById(HUD_HOST_ID);
    if (!host) {
      host = document.createElement("loom-browser-page-hud");
      host.id = HUD_HOST_ID;
      host.setAttribute("aria-hidden", "true");
      Object.assign(host.style, {
        position: "fixed",
        inset: "0",
        zIndex: "2147483647",
        pointerEvents: "none",
        contain: "layout style paint",
      });
      document.documentElement.appendChild(host);
    }
    const root = host.shadowRoot || host.attachShadow({ mode: "open" });
    return { host, root };
  }

  function hudStyles() {
    return `
      <style>
        :host { all: initial; }
        .loom-page-hud-target {
          position: fixed;
          left: var(--loom-hud-x);
          top: var(--loom-hud-y);
          width: var(--loom-hud-w);
          height: var(--loom-hud-h);
          box-sizing: border-box;
          border: 2px solid rgba(56, 189, 248, .96);
          border-radius: 12px;
          background: rgba(14, 165, 233, .055);
          box-shadow: 0 0 0 1px rgba(255,255,255,.72), 0 0 28px rgba(56,189,248,.36), inset 0 0 18px rgba(56,189,248,.12);
          transform: translateZ(0);
          animation: loomHudSettle .18s cubic-bezier(.2,.9,.2,1), loomHudBreathe 1.6s ease-in-out infinite;
        }
        .loom-page-hud-target.action {
          border-color: rgba(168, 85, 247, .98);
          background: rgba(168, 85, 247, .07);
          box-shadow: 0 0 0 1px rgba(255,255,255,.72), 0 0 34px rgba(168,85,247,.38), inset 0 0 20px rgba(168,85,247,.16);
        }
        .loom-page-hud-target.secondary {
          border-style: dashed;
          border-color: rgba(34, 197, 94, .94);
          background: rgba(34,197,94,.05);
        }
        .loom-page-hud-target::after {
          content: "";
          position: absolute;
          inset: -8px;
          border: 1px solid rgba(56,189,248,.22);
          border-radius: 16px;
          opacity: .85;
          animation: loomHudPulse .72s ease-out 1;
        }
        .loom-page-hud-label {
          position: fixed;
          left: var(--loom-label-x);
          top: var(--loom-label-y);
          min-width: 174px;
          max-width: 340px;
          padding: 9px 11px 10px;
          border: 1px solid rgba(148, 163, 184, .28);
          border-radius: 14px;
          color: rgba(248,250,252,.98);
          background: linear-gradient(135deg, rgba(15,23,42,.94), rgba(30,41,59,.90));
          box-shadow: 0 18px 45px rgba(2,6,23,.30), 0 0 24px rgba(56,189,248,.14);
          font: 12px/1.35 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          letter-spacing: normal;
          text-align: left;
        }
        .loom-page-hud-label strong {
          display: block;
          margin-bottom: 3px;
          color: white;
          font-size: 12.5px;
          font-weight: 720;
        }
        .loom-page-hud-label span {
          display: block;
          overflow: hidden;
          color: rgba(203,213,225,.90);
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .loom-page-hud-pill {
          position: fixed;
          right: 18px;
          top: 18px;
          display: flex;
          align-items: center;
          gap: 8px;
          max-width: min(360px, calc(100vw - 36px));
          padding: 10px 12px;
          border: 1px solid rgba(56,189,248,.24);
          border-radius: 999px;
          color: rgba(248,250,252,.98);
          background: linear-gradient(135deg, rgba(15,23,42,.92), rgba(30,41,59,.86));
          box-shadow: 0 14px 40px rgba(2,6,23,.24), 0 0 26px rgba(56,189,248,.12);
          font: 12px/1.25 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }
        .loom-page-hud-dot {
          flex: 0 0 auto;
          width: 8px;
          height: 8px;
          border-radius: 999px;
          background: rgb(45,212,191);
          box-shadow: 0 0 0 4px rgba(45,212,191,.16), 0 0 18px rgba(45,212,191,.55);
        }
        .loom-page-hud-pill-text {
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        @keyframes loomHudSettle { from { opacity: 0; transform: scale(.985); } to { opacity: 1; transform: scale(1); } }
        @keyframes loomHudBreathe { 0%, 100% { filter: saturate(1); } 50% { filter: saturate(1.25); } }
        @keyframes loomHudPulse { from { opacity: .75; transform: scale(.98); } to { opacity: 0; transform: scale(1.08); } }
      </style>`;
  }

  function removeHudAfter(host, delayMs = 2200) {
    window.clearTimeout(window.__loomBrowserPageHudTimer);
    window.__loomBrowserPageHudTimer = window.setTimeout(() => {
      try { host.remove(); } catch {}
    }, delayMs);
  }

  function showStatusHud(title, subtitle = "Browser Use") {
    const { host, root } = ensureHud();
    root.innerHTML = `${hudStyles()}<div class="loom-page-hud-pill"><div class="loom-page-hud-dot"></div><div class="loom-page-hud-pill-text"><strong>${escapeHtml(title)}</strong>${subtitle ? ` · ${escapeHtml(clean(subtitle, 100))}` : ""}</div></div>`;
    removeHudAfter(host, 1700);
  }

  function labelPosition(rect) {
    const labelWidth = Math.min(340, Math.max(174, Math.round(rect.width + 72)));
    const x = Math.max(12, Math.min(window.innerWidth - labelWidth - 12, rect.left));
    const preferBelow = rect.top < 84;
    const y = preferBelow
      ? Math.min(window.innerHeight - 56, rect.bottom + 10)
      : Math.max(12, rect.top - 54);
    return { x: Math.round(x), y: Math.round(y), width: labelWidth };
  }

  function showTargetHud(el, title, subtitle = "Browser Use", variant = "target") {
    const { host, root } = ensureHud();
    const rect = el.getBoundingClientRect();
    const pad = 4;
    const x = Math.max(2, Math.round(rect.left - pad));
    const y = Math.max(2, Math.round(rect.top - pad));
    const w = Math.max(18, Math.min(window.innerWidth - x - 2, Math.round(rect.width + pad * 2)));
    const h = Math.max(18, Math.min(window.innerHeight - y - 2, Math.round(rect.height + pad * 2)));
    const label = labelPosition({ left: x, top: y, right: x + w, bottom: y + h, width: w, height: h });
    root.innerHTML = `${hudStyles()}
      <div class="loom-page-hud-target ${escapeHtml(variant)}" style="--loom-hud-x:${x}px;--loom-hud-y:${y}px;--loom-hud-w:${w}px;--loom-hud-h:${h}px"></div>
      <div class="loom-page-hud-label" style="--loom-label-x:${label.x}px;--loom-label-y:${label.y}px;width:${label.width}px">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(clean(subtitle, 180))}</span>
      </div>`;
    removeHudAfter(host, 2300);
  }

  function targetById(loomId) {
    const el = document.querySelector(`[data-loom-bridge-id="${CSS.escape(String(loomId || ""))}"]`);
    if (!(el instanceof HTMLElement)) throw new Error("Element is no longer available. Refresh browser_state and retry.");
    el.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
    return el;
  }

  function collectPageState(showHud = true) {
    const errors = [];
    const elements = [];
    try {
      for (const el of Array.from(document.querySelectorAll("a,button,input,textarea,select,summary,[role],[tabindex],[contenteditable='true']"))) {
        if (!(el instanceof HTMLElement) || !isCandidate(el) || !visible(el)) continue;
        const rect = el.getBoundingClientRect();
        const tag = el.tagName.toLowerCase();
        const type = clean(el.getAttribute("type") || "");
        const item = {
          index: elements.length,
          loom_id: ensureId(el),
          tag,
          text: clean(el.innerText || el.textContent || el.getAttribute("title") || ""),
          aria: clean(el.getAttribute("aria-label") || el.getAttribute("alt") || ""),
          placeholder: clean(el.getAttribute("placeholder") || ""),
          type,
          value: clean(["input", "textarea", "select"].includes(tag) && type !== "password" ? el.value : ""),
          rect: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
        };
        elements.push(item);
        if (elements.length >= MAX_ELEMENTS) break;
      }
    } catch (cause) {
      errors.push(cause instanceof Error ? cause.message : String(cause));
    }

    if (showHud) {
      showStatusHud("Reading current tab", `${elements.length} interactive elements found`);
    }

    const elementLines = elements.map((item) => {
      const attrs = [];
      if (item.text) attrs.push(`text="${item.text}"`);
      if (item.aria) attrs.push(`aria="${item.aria}"`);
      if (item.placeholder) attrs.push(`placeholder="${item.placeholder}"`);
      if (item.type) attrs.push(`type="${item.type}"`);
      if (item.value) attrs.push(`value="${item.value}"`);
      attrs.push(`rect=${item.rect.x},${item.rect.y},${item.rect.width}x${item.rect.height}`);
      return `[${item.index}] <${item.tag}> ${attrs.join(" ")}`;
    });

    return {
      url: location.href,
      title: document.title,
      dom: [
        `URL: ${location.href}`,
        `Title: ${document.title}`,
        `Viewport: ${window.innerWidth}x${window.innerHeight} scroll=${Math.round(window.scrollX)},${Math.round(window.scrollY)}`,
        "",
        "Interactive elements:",
        ...elementLines,
        "",
        "Visible page text:",
        clean(document.body?.innerText || "", MAX_TEXT),
      ].join("\n"),
      elements,
      page_info: {
        element_count: elements.length,
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        scroll_x: Math.round(window.scrollX),
        scroll_y: Math.round(window.scrollY),
        browser_hud: "page-local",
      },
      errors,
    };
  }

  // chrome.tabs.onUpdated only reports "loading" once a navigation commits, which
  // on a slow origin is well over a second after the click. The background script
  // cannot tell that apart from a click that navigates nothing, so the page says
  // up front whether a navigation is on the way.
  function navigationExpectedFor(el) {
    const anchor = typeof el.closest === "function" ? el.closest("a[href]") : null;
    if (anchor) {
      const href = String(anchor.getAttribute("href") || "").trim();
      const target = String(anchor.getAttribute("target") || "").trim().toLowerCase();
      const inPage = !href || href.startsWith("#") || /^javascript:/i.test(href);
      // _blank lands in a new tab, so the tab being watched never navigates.
      if (!inPage && target !== "_blank") return true;
    }
    const type = String(el.getAttribute("type") || "").toLowerCase();
    const isSubmit =
      (el.tagName === "BUTTON" && type !== "button" && type !== "reset") ||
      (el.tagName === "INPUT" && (type === "submit" || type === "image"));
    if (isSubmit && typeof el.closest === "function" && el.closest("form")) return true;
    return false;
  }

  function clickElement() {
    const el = targetById(args.loom_id);
    showTargetHud(el, `Click #${Number(args.index)}`, clean(el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.tagName, 180), "action");
    el.focus({ preventScroll: true });
    const expected = navigationExpectedFor(el);
    el.click();
    return { ok: true, navigation_expected: expected };
  }

  function hoverElement() {
    const el = targetById(args.loom_id);
    showTargetHud(el, `Hover #${Number(args.index)}`, clean(el.innerText || el.getAttribute("aria-label") || el.tagName, 180), "target");
    const rect = el.getBoundingClientRect();
    const init = { bubbles: true, cancelable: true, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
    el.dispatchEvent(new MouseEvent("mouseover", init));
    el.dispatchEvent(new MouseEvent("mouseenter", init));
    el.dispatchEvent(new MouseEvent("mousemove", init));
    return true;
  }

  function typeElement() {
    const el = targetById(args.loom_id);
    const text = String(args.text || "");
    showTargetHud(el, `Type into #${Number(args.index)}`, text ? `${text.length} characters` : "Empty text", "action");
    el.focus({ preventScroll: true });
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      if (args.clear !== false) el.value = "";
      el.value += text;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    if (el.isContentEditable) {
      if (args.clear !== false) el.textContent = "";
      el.textContent = `${el.textContent || ""}${text}`;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
      return true;
    }
    throw new Error("Target element is not an input, textarea, or contenteditable element");
  }

  function selectElement() {
    const el = targetById(args.loom_id);
    if (!(el instanceof HTMLSelectElement)) throw new Error("Target element is not a select");
    showTargetHud(el, `Select #${Number(args.index)}`, clean(String(args.value || ""), 180), "action");
    el.value = String(args.value || "");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function dragElement() {
    const source = targetById(args.source_loom_id);
    const target = targetById(args.target_loom_id);
    showTargetHud(source, `Drag #${Number(args.source_index)} → #${Number(args.target_index)}`, clean(target.innerText || target.getAttribute("aria-label") || target.tagName, 180), "action");
    const dataTransfer = new DataTransfer();
    source.dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer }));
    target.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer }));
    target.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }));
    source.dispatchEvent(new DragEvent("dragend", { bubbles: true, cancelable: true, dataTransfer }));
    return true;
  }

  function pressKeyInPage() {
    const key = String(args.key || "");
    showStatusHud(`Press ${key || "key"}`, "Browser keyboard action");
    const active = document.activeElement instanceof HTMLElement ? document.activeElement : document.body;
    const parts = key.split("+").map((part) => part.trim()).filter(Boolean);
    const main = parts.pop() || key;
    const init = {
      key: main,
      code: main.length === 1 ? `Key${main.toUpperCase()}` : main,
      bubbles: true,
      cancelable: true,
      ctrlKey: parts.some((part) => /^ctrl|control$/i.test(part)),
      shiftKey: parts.some((part) => /^shift$/i.test(part)),
      altKey: parts.some((part) => /^alt$/i.test(part)),
      metaKey: parts.some((part) => /^meta|cmd|command$/i.test(part)),
    };
    active.dispatchEvent(new KeyboardEvent("keydown", init));
    active.dispatchEvent(new KeyboardEvent("keyup", init));
    return true;
  }

  function scrollPage() {
    const direction = String(args.direction || "down");
    const value = Math.max(1, Math.min(Number(args.amount || 700), 20000));
    showStatusHud(`Scroll ${direction}`, `${value}px`);
    const dx = direction === "left" ? -value : direction === "right" ? value : 0;
    const dy = direction === "up" ? -value : direction === "down" ? value : 0;
    window.scrollBy({ left: dx, top: dy, behavior: "auto" });
    return true;
  }

  function goBack() {
    showStatusHud("Going back", "Browser navigation");
    history.back();
    return true;
  }

  switch (action) {
    case "state": return collectPageState(args.show_hud !== false);
    case "hud_status":
      showStatusHud(clean(args.title || "Browser action", 80), clean(args.subtitle || "Browser Use", 140));
      return true;
    case "click": return clickElement();
    case "hover": return hoverElement();
    case "type_text": return typeElement();
    case "select_option": return selectElement();
    case "drag": return dragElement();
    case "press_key": return pressKeyInPage();
    case "scroll": return scrollPage();
    case "go_back": return goBack();
    default: throw new Error(`Unsupported injected Loom page action: ${action}`);
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ bridgeUrl: DEFAULT_BRIDGE_URL, token: DEFAULT_TOKEN }).catch(() => {});
  startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
});
chrome.runtime.onStartup.addListener(() => startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause)));
chrome.action.onClicked.addListener(() => startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause)));
startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
