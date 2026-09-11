const PROTOCOL_VERSION = 1;
const DEFAULT_BRIDGE_URL = "http://127.0.0.1:39222";
const DEFAULT_TOKEN = "loom-dev-browser-extension";
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
const CLIENT_ID_KEY = "loomBrowserBridgeClientId";

let polling = false;
let stopped = false;
let lastElementsByTab = new Map();

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `loom-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function getClientId() {
  const stored = await chrome.storage.local.get(CLIENT_ID_KEY);
  if (typeof stored[CLIENT_ID_KEY] === "string" && stored[CLIENT_ID_KEY]) {
    return stored[CLIENT_ID_KEY];
  }
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
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(`${config.bridgeUrl}${path}`, { ...options, headers });
}

async function register() {
  const clientId = await getClientId();
  await bridgeFetch("/browser-extension/v1/register", {
    method: "POST",
    body: JSON.stringify({ client_id: clientId, version: EXTENSION_VERSION, protocol_version: PROTOCOL_VERSION }),
  });
}

async function pollOnce() {
  const clientId = encodeURIComponent(await getClientId());
  const version = encodeURIComponent(EXTENSION_VERSION);
  const response = await bridgeFetch(`/browser-extension/v1/poll?client_id=${clientId}&version=${version}`);
  if (!response.ok) {
    throw new Error(`poll failed: HTTP ${response.status}`);
  }
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
  stopped = false;
  let backoff = 500;
  while (!stopped) {
    try {
      await register();
      await pollOnce();
      backoff = 500;
    } catch (cause) {
      console.warn("[loom-browser-bridge]", cause);
      await delay(backoff);
      backoff = Math.min(backoff * 1.6, 10000);
    }
  }
  polling = false;
}

function activeTabQuery() {
  return new Promise((resolve, reject) => {
    chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      if (tabs && tabs.length) {
        resolve(tabs[0]);
        return;
      }
      chrome.tabs.query({ active: true, currentWindow: true }, (fallback) => {
        const fallbackError = chrome.runtime.lastError;
        if (fallbackError) reject(new Error(fallbackError.message));
        else if (fallback && fallback.length) resolve(fallback[0]);
        else reject(new Error("No active browser tab is available"));
      });
    });
  });
}

async function getActiveTab() {
  const tab = await activeTabQuery();
  if (!tab || typeof tab.id !== "number") throw new Error("Active browser tab has no tab id");
  return tab;
}

async function allTabs() {
  const active = await activeTabQuery().catch(() => null);
  const tabs = await chrome.tabs.query({ currentWindow: true });
  return tabs.map((tab) => ({
    tab_id: String(tab.id ?? ""),
    url: tab.url || "",
    title: tab.title || "",
    active: Boolean(active && tab.id === active.id),
  }));
}

async function injectFunction(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func,
    args,
  });
  if (!results || !results.length) return {};
  return results[0].result || {};
}

function isInjectableUrl(url) {
  return /^https?:\/\//i.test(String(url || ""));
}

async function collectStateForTab(tab) {
  const base = {
    url: tab.url || "about:blank",
    title: tab.title || "",
    dom: "",
    tabs: await allTabs(),
    page_info: {
      extension_version: EXTENSION_VERSION,
      tab_id: String(tab.id),
      injectable: isInjectableUrl(tab.url || ""),
    },
    errors: [],
  };
  if (!isInjectableUrl(tab.url || "")) {
    base.errors.push("Loom can list this tab but cannot inspect chrome://, edge://, extension, file, or other privileged pages.");
    return base;
  }
  try {
    const page = await injectFunction(tab.id, collectPageState);
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
    return {
      ...base,
      errors: [cause instanceof Error ? cause.message : String(cause)],
    };
  }
}

async function state() {
  return collectStateForTab(await getActiveTab());
}

async function navigate(args) {
  const url = String(args.url || "");
  if (!url) throw new Error("url is required");
  let tab;
  if (args.new_tab) {
    tab = await chrome.tabs.create({ url, active: true });
  } else {
    const active = await getActiveTab();
    tab = await chrome.tabs.update(active.id, { url, active: true });
  }
  await waitForTabComplete(tab.id);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function waitForTabComplete(tabId) {
  const started = Date.now();
  while (Date.now() - started < 15000) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return;
    await delay(250);
  }
}

function elementRefFor(tabId, index) {
  const elements = lastElementsByTab.get(tabId) || [];
  const item = elements[Number(index)];
  if (!item || !item.loom_id) {
    throw new Error("Element index is not available. Refresh browser_state and retry.");
  }
  return item.loom_id;
}

async function click(args) {
  const tab = await getActiveTab();
  const loomId = elementRefFor(tab.id, args.index);
  await injectFunction(tab.id, clickElementByLoomId, [loomId]);
  await delay(250);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function hover(args) {
  const tab = await getActiveTab();
  const loomId = elementRefFor(tab.id, args.index);
  await injectFunction(tab.id, hoverElementByLoomId, [loomId]);
  await delay(150);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function typeText(args) {
  const tab = await getActiveTab();
  const loomId = elementRefFor(tab.id, args.index);
  await injectFunction(tab.id, typeElementByLoomId, [loomId, String(args.text || ""), args.clear !== false]);
  await delay(200);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function selectOption(args) {
  const tab = await getActiveTab();
  const loomId = elementRefFor(tab.id, args.index);
  await injectFunction(tab.id, selectElementByLoomId, [loomId, String(args.value || "")]);
  await delay(200);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function drag(args) {
  const tab = await getActiveTab();
  const source = elementRefFor(tab.id, args.source_index);
  const target = elementRefFor(tab.id, args.target_index);
  await injectFunction(tab.id, dragElementByLoomId, [source, target]);
  await delay(250);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function pressKey(args) {
  const tab = await getActiveTab();
  await injectFunction(tab.id, pressKeyInPage, [String(args.key || "")]);
  await delay(150);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function scroll(args) {
  const tab = await getActiveTab();
  await injectFunction(tab.id, scrollPage, [String(args.direction || "down"), Number(args.amount || 700)]);
  await delay(150);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function goBack() {
  const tab = await getActiveTab();
  if (isInjectableUrl(tab.url || "")) {
    await injectFunction(tab.id, () => { history.back(); return true; });
  } else {
    await chrome.tabs.goBack(tab.id);
  }
  await delay(500);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function refresh() {
  const tab = await getActiveTab();
  await chrome.tabs.reload(tab.id);
  await waitForTabComplete(tab.id);
  return collectStateForTab(await chrome.tabs.get(tab.id));
}

async function tabsState() {
  return collectStateForTab(await getActiveTab());
}

async function switchTab(args) {
  const tabId = Number.parseInt(String(args.tab_id || ""), 10);
  if (!Number.isInteger(tabId)) throw new Error("tab_id must be numeric for the extension backend");
  await chrome.tabs.update(tabId, { active: true });
  const tab = await chrome.tabs.get(tabId);
  return collectStateForTab(tab);
}

async function closeTab(args) {
  const tabId = Number.parseInt(String(args.tab_id || ""), 10);
  if (!Number.isInteger(tabId)) throw new Error("tab_id must be numeric for the extension backend");
  await chrome.tabs.remove(tabId);
  return collectStateForTab(await getActiveTab());
}

async function screenshot(args) {
  const tab = await getActiveTab();
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  const prefix = "base64,";
  const index = dataUrl.indexOf(prefix);
  if (index < 0) throw new Error("captureVisibleTab did not return base64 PNG data");
  return { png_base64: dataUrl.slice(index + prefix.length), full_page: false };
}

async function dispatchCommand(action, args) {
  switch (action) {
    case "state": return state();
    case "navigate": return navigate(args);
    case "click": return click(args);
    case "hover": return hover(args);
    case "type_text": return typeText(args);
    case "select_option": return selectOption(args);
    case "drag": return drag(args);
    case "press_key": return pressKey(args);
    case "scroll": return scroll(args);
    case "go_back": return goBack();
    case "refresh": return refresh();
    case "tabs": return tabsState();
    case "switch_tab": return switchTab(args);
    case "close_tab": return closeTab(args);
    case "screenshot": return screenshot(args);
    default: throw new Error(`Unsupported Loom browser extension action: ${action}`);
  }
}

function collectPageState() {
  const errors = [];
  const MAX_TEXT = 16000;
  const MAX_ELEMENTS = 300;

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
    if (el.isContentEditable) return true;
    if (typeof el.onclick === "function") return true;
    const tabindex = el.getAttribute("tabindex");
    return tabindex !== null && tabindex !== "-1";
  }

  function ensureId(el) {
    if (!el.dataset.loomBridgeId) {
      el.dataset.loomBridgeId = crypto.randomUUID ? crypto.randomUUID() : `loom-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }
    return el.dataset.loomBridgeId;
  }

  function clean(value, limit = 160) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function elementLine(item) {
    const attrs = [];
    if (item.text) attrs.push(`text="${item.text}"`);
    if (item.aria) attrs.push(`aria="${item.aria}"`);
    if (item.placeholder) attrs.push(`placeholder="${item.placeholder}"`);
    if (item.type) attrs.push(`type="${item.type}"`);
    if (item.value) attrs.push(`value="${item.value}"`);
    attrs.push(`rect=${item.rect.x},${item.rect.y},${item.rect.width}x${item.rect.height}`);
    return `[${item.index}] <${item.tag}> ${attrs.join(" ")}`;
  }

  let elements = [];
  try {
    const candidates = Array.from(document.querySelectorAll("a,button,input,textarea,select,summary,[role],[tabindex],[contenteditable='true']"));
    for (const el of candidates) {
      if (!(el instanceof HTMLElement) || !isCandidate(el) || !visible(el)) continue;
      const rect = el.getBoundingClientRect();
      const item = {
        index: elements.length,
        loom_id: ensureId(el),
        tag: el.tagName.toLowerCase(),
        text: clean(el.innerText || el.textContent || el.getAttribute("title") || ""),
        aria: clean(el.getAttribute("aria-label") || el.getAttribute("alt") || ""),
        placeholder: clean(el.getAttribute("placeholder") || ""),
        type: clean(el.getAttribute("type") || ""),
        value: clean(["input", "textarea", "select"].includes(el.tagName.toLowerCase()) ? el.value : ""),
        rect: {
          x: Math.round(rect.left),
          y: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
      };
      elements.push(item);
      if (elements.length >= MAX_ELEMENTS) break;
    }
  } catch (cause) {
    errors.push(cause instanceof Error ? cause.message : String(cause));
  }

  const pageText = clean(document.body?.innerText || "", MAX_TEXT);
  const lines = [
    `URL: ${location.href}`,
    `Title: ${document.title}`,
    `Viewport: ${window.innerWidth}x${window.innerHeight} scroll=${Math.round(window.scrollX)},${Math.round(window.scrollY)}`,
    "",
    "Interactive elements:",
    ...elements.map(elementLine),
    "",
    "Visible page text:",
    pageText,
  ];

  return {
    url: location.href,
    title: document.title,
    dom: lines.join("\n"),
    elements,
    page_info: {
      element_count: elements.length,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      scroll_x: Math.round(window.scrollX),
      scroll_y: Math.round(window.scrollY),
    },
    errors,
  };
}

function targetById(loomId) {
  const el = document.querySelector(`[data-loom-bridge-id="${CSS.escape(loomId)}"]`);
  if (!(el instanceof HTMLElement)) throw new Error("Element is no longer available. Refresh browser_state and retry.");
  el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
  return el;
}

function clickElementByLoomId(loomId) {
  const el = targetById(loomId);
  el.focus({ preventScroll: true });
  el.click();
  return true;
}

function hoverElementByLoomId(loomId) {
  const el = targetById(loomId);
  const rect = el.getBoundingClientRect();
  const init = { bubbles: true, cancelable: true, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
  el.dispatchEvent(new MouseEvent("mouseover", init));
  el.dispatchEvent(new MouseEvent("mouseenter", init));
  el.dispatchEvent(new MouseEvent("mousemove", init));
  return true;
}

function typeElementByLoomId(loomId, text, clear) {
  const el = targetById(loomId);
  el.focus({ preventScroll: true });
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    if (clear) el.value = "";
    el.value += text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }
  if (el.isContentEditable) {
    if (clear) el.textContent = "";
    el.textContent = `${el.textContent || ""}${text}`;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    return true;
  }
  throw new Error("Target element is not an input, textarea, or contenteditable element");
}

function selectElementByLoomId(loomId, value) {
  const el = targetById(loomId);
  if (!(el instanceof HTMLSelectElement)) throw new Error("Target element is not a select");
  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function dragElementByLoomId(sourceId, targetId) {
  const source = targetById(sourceId);
  const target = targetById(targetId);
  const dataTransfer = new DataTransfer();
  source.dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer }));
  target.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer }));
  target.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }));
  source.dispatchEvent(new DragEvent("dragend", { bubbles: true, cancelable: true, dataTransfer }));
  return true;
}

function pressKeyInPage(key) {
  const active = document.activeElement instanceof HTMLElement ? document.activeElement : document.body;
  const normalized = String(key || "");
  const parts = normalized.split("+").map((part) => part.trim()).filter(Boolean);
  const main = parts.pop() || normalized;
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

function scrollPage(direction, amount) {
  const value = Math.max(1, Math.min(Number(amount || 700), 20000));
  const dx = direction === "left" ? -value : direction === "right" ? value : 0;
  const dy = direction === "up" ? -value : direction === "down" ? value : 0;
  window.scrollBy({ left: dx, top: dy, behavior: "instant" });
  return true;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ bridgeUrl: DEFAULT_BRIDGE_URL, token: DEFAULT_TOKEN }).catch(() => {});
  startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
});

chrome.runtime.onStartup.addListener(() => {
  startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
});

chrome.action.onClicked.addListener(() => {
  startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
});

startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
