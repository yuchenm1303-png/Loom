const PROTOCOL_VERSION = 1;
const DEFAULT_BRIDGE_URL = "http://127.0.0.1:39222";
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
// How long to keep looking for a navigation an element action may have started.
const NAVIGATION_GRACE_MS = 300;
// The same, when the page itself reported that this action starts no navigation.
// 86% of element actions never navigate, and the settle sleep above has already
// given the page its chance to start one, so the full grace was pure latency on
// almost every click, keystroke and selection. Still non-zero: the page's own
// prediction can be wrong, and onUpdated needs a moment to fire when it is.
const NAVIGATION_SETTLE_MS = 60;
// Status polling granularity while a page loads. At 250ms a page that finished
// immediately still waited out the rest of the tick.
const TAB_COMPLETE_POLL_MS = 60;
// The bridge only needs the active tab for its status display, so re-registering
// before every single poll was a wasted round trip that queued behind the next
// command. Connectivity is tracked by the poll itself, not by this.
const REGISTER_INTERVAL_MS = 15000;
// How long to wait when the page said the action does start one.
const NAVIGATION_COMMIT_TIMEOUT_MS = 8000;
const CLIENT_ID_KEY = "loomBrowserBridgeClientId";
const UPDATE_TOKEN_KEY = "loomBrowserBridgeUpdateToken";
const UPDATE_ALARM_NAME = "loom-browser-bridge-update";
const LOOM_TAB_GROUP_TITLE = "Loom";
const OWNED_TAB_IDS_KEY = "loomOwnedTabIds";
const ADOPTED_TAB_IDS_KEY = "loomAdoptedTabIds";
const OWNED_GROUP_IDS_KEY = "loomOwnedGroupIds";
// Element identity has to outlive the service worker. MV3 tears the worker down
// between commands, and the plain Map this replaces came back empty while Loom's
// state_revision was still current, so every index the model held turned into
// "refresh and retry" for no reason the model could see.
const ELEMENT_IDS_KEY = "loomElementIdsByTab";
// Whether Loom currently holds a browser session. The HUD's visibility has to be
// driven by this rather than by whether an action happens to be running: a page
// load destroys everything in the page, so a HUD that only appears while acting
// vanishes on every refresh and returns on the next click.
const SESSION_ACTIVE_KEY = "loomBrowserSessionActive";

const bridgeRuntime = {
  running: false,
  phase: "stopped",
  updateRequested: true,
  registeredAt: 0,
};

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
  let bundled = {};
  try {
    const response = await fetch(chrome.runtime.getURL("bridge-config.json"), { cache: "no-store" });
    if (response.ok) bundled = await response.json();
  } catch (_) {
    // Store-distributed builds can pair through a future managed installer;
    // unpacked Loom builds receive this file from Settings > Browser.
  }
  // The desktop-written config wins so "Install or repair" also replaces a
  // stale development credential left by an older extension version.
  const token = String(bundled.token || stored.token || "").trim();
  if (!token) throw new Error("Loom bridge is not paired. Use Loom Settings > Browser > Set up extension.");
  return {
    bridgeUrl: String(bundled.bridgeUrl || stored.bridgeUrl || DEFAULT_BRIDGE_URL).replace(/\/+$/, ""),
    token,
  };
}

function browserName() {
  const brands = navigator.userAgentData?.brands || [];
  if (brands.some((item) => /Microsoft Edge/i.test(item.brand))) return "Microsoft Edge";
  if (brands.some((item) => /Google Chrome/i.test(item.brand))) return "Google Chrome";
  if (/Edg\//i.test(navigator.userAgent)) return "Microsoft Edge";
  if (/Chrome\//i.test(navigator.userAgent)) return "Google Chrome";
  return "Chromium browser";
}

async function bridgeFetch(path, options = {}) {
  const config = await getConfig();
  const headers = new Headers(options.headers || {});
  headers.set("X-Loom-Token", config.token);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return fetch(`${config.bridgeUrl}${path}`, { ...options, headers });
}

async function register() {
  let activeTab = null;
  try {
    const tab = await queryActiveTab();
    activeTab = {
      tab_id: String(tab.id ?? ""),
      window_id: String(tab.windowId ?? ""),
      title: String(tab.title || ""),
      url: String(tab.url || ""),
    };
  } catch (_) {}
  await bridgeFetch("/browser-extension/v1/register", {
    method: "POST",
    body: JSON.stringify({
      client_id: await getClientId(),
      version: EXTENSION_VERSION,
      protocol_version: PROTOCOL_VERSION,
      browser: browserName(),
      active_tab: activeTab,
    }),
  });
}

async function pollOnce() {
  const clientId = encodeURIComponent(await getClientId());
  const version = encodeURIComponent(EXTENSION_VERSION);
  const browser = encodeURIComponent(browserName());
  const response = await bridgeFetch(`/browser-extension/v1/poll?client_id=${clientId}&version=${version}&browser=${browser}`);
  if (!response.ok) throw new Error(`poll failed: HTTP ${response.status}`);
  const payload = await response.json();
  if (!payload.command) return;
  const command = payload.command;
  bridgeRuntime.phase = "executing";
  let result = null;
  let ok = false;
  let error = "";
  try {
    result = await dispatchCommand(command.action, command.args || {});
    ok = true;
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }
  bridgeRuntime.phase = "reporting";
  await bridgeFetch("/browser-extension/v1/result", {
    method: "POST",
    body: JSON.stringify({ id: command.id, ok, result, error }),
  });
}

async function startPolling() {
  if (bridgeRuntime.running) return;
  bridgeRuntime.running = true;
  let backoff = 500;
  for (;;) {
    try {
      // Updates have exactly one execution point: this boundary between fully
      // reported commands. Alarms and clicks may request a check, but neither
      // can reload the worker concurrently with a browser action.
      bridgeRuntime.phase = "idle";
      if (bridgeRuntime.updateRequested) {
        bridgeRuntime.updateRequested = false;
        await applyInstalledUpdateAtCommandBoundary();
      }
      bridgeRuntime.phase = "polling";
      // Registering before every poll put a full round trip between finishing one
      // command and being able to receive the next, and the bridge only wants the
      // active tab for its status display. The poll itself is what proves the
      // extension is alive, so this can be periodic.
      if (Date.now() - bridgeRuntime.registeredAt >= REGISTER_INTERVAL_MS) {
        await register();
        bridgeRuntime.registeredAt = Date.now();
      }
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

async function applyInstalledUpdateAtCommandBoundary() {
  try {
    const response = await fetch(chrome.runtime.getURL("extension-update.json"), { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    const token = String(payload.token || "").trim();
    if (!token) return;
    const stored = await chrome.storage.local.get(UPDATE_TOKEN_KEY);
    const previous = String(stored[UPDATE_TOKEN_KEY] || "");
    if (!previous) {
      await chrome.storage.local.set({ [UPDATE_TOKEN_KEY]: token });
      return;
    }
    if (previous !== token) {
      await chrome.storage.local.set({ [UPDATE_TOKEN_KEY]: token });
      chrome.runtime.reload();
    }
  } catch (_) {
    // This generated file exists only in Loom's stable local installation.
  }
}

function startInstalledUpdateWatcher() {
  chrome.alarms.create(UPDATE_ALARM_NAME, { delayInMinutes: 0.1, periodInMinutes: 0.5 });
}

function requestInstalledUpdateCheck() {
  bridgeRuntime.updateRequested = true;
  startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== UPDATE_ALARM_NAME) return;
  requestInstalledUpdateCheck();
});

// Two kinds of ownership, and they end differently. A tab Loom opened itself is
// Loom's until it is closed: later sessions must recognise it, or every session
// opens another one. A tab borrowed from the user is theirs, and holding it past
// the session is what lets a later task navigate away a page they went back to.
async function ownedTabIds() {
  const stored = await chrome.storage.session.get([OWNED_TAB_IDS_KEY, ADOPTED_TAB_IDS_KEY]);
  const own = Array.isArray(stored[OWNED_TAB_IDS_KEY]) ? stored[OWNED_TAB_IDS_KEY] : [];
  const adopted = Array.isArray(stored[ADOPTED_TAB_IDS_KEY]) ? stored[ADOPTED_TAB_IDS_KEY] : [];
  return { own, adopted, all: [...new Set([...own, ...adopted])] };
}

async function isLoomWorkTab(tab) {
  if (!tab || typeof tab.id !== "number") return false;
  return (await ownedTabIds()).all.includes(tab.id);
}

async function markLoomWorkTab(tab, { adopted = false } = {}) {
  if (!tab || typeof tab.id !== "number") return tab;
  const key = adopted ? ADOPTED_TAB_IDS_KEY : OWNED_TAB_IDS_KEY;
  const stored = await chrome.storage.session.get(key);
  const ids = new Set(Array.isArray(stored[key]) ? stored[key] : []);
  ids.add(tab.id);
  await chrome.storage.session.set({ [key]: [...ids] });
  return tab;
}

async function forgetLoomWorkTab(tabId) {
  const stored = await chrome.storage.session.get([OWNED_TAB_IDS_KEY, ADOPTED_TAB_IDS_KEY]);
  const drop = (key) => {
    const ids = Array.isArray(stored[key]) ? stored[key] : [];
    return { [key]: ids.filter((id) => id !== tabId) };
  };
  await chrome.storage.session.set({ ...drop(OWNED_TAB_IDS_KEY), ...drop(ADOPTED_TAB_IDS_KEY) });
  await forgetElements(tabId);
}

chrome.tabs.onRemoved.addListener((tabId) => {
  void forgetLoomWorkTab(tabId);
});

async function markSessionActive(active) {
  const next = Boolean(active);
  try {
    // Only on a real transition. storage.set does not diff, so writing the same
    // value on every command would emit a change event every time - waking the
    // HUD listener in every page for nothing, and masking whether the HUD can
    // restore itself on its own after a page load.
    const stored = await chrome.storage.session.get(SESSION_ACTIVE_KEY);
    if (Boolean(stored?.[SESSION_ACTIVE_KEY]) === next) return;
    await chrome.storage.session.set({ [SESSION_ACTIVE_KEY]: next });
  } catch (cause) {
    console.warn("[loom-browser-bridge] could not record session state", cause);
  }
}

async function releaseTabs() {
  // Only the borrowed ones. Releasing Loom's own tabs too made every new session
  // fail to recognise the tab it had just been working in, so it opened another,
  // and another - it fought the user for the foreground instead of staying put.
  // The group is left alone: the tabs stay visible and the next session reuses
  // the group rather than stacking up a second one.
  const stored = await chrome.storage.session.get(ADOPTED_TAB_IDS_KEY);
  const released = Array.isArray(stored[ADOPTED_TAB_IDS_KEY]) ? stored[ADOPTED_TAB_IDS_KEY].length : 0;
  await chrome.storage.session.remove(ADOPTED_TAB_IDS_KEY);
  await markSessionActive(false);
  return { released };
}

async function placeInLoomGroup(tab, { adopted = false } = {}) {
  if (!chrome.tabGroups || typeof tab?.id !== "number") return markLoomWorkTab(tab, { adopted });
  try {
    const stored = await chrome.storage.session.get(OWNED_GROUP_IDS_KEY);
    const groupsByWindow = { ...(stored[OWNED_GROUP_IDS_KEY] || {}) };
    let groupId = Number(groupsByWindow[String(tab.windowId)]);
    try {
      if (Number.isInteger(groupId)) await chrome.tabGroups.get(groupId);
      else groupId = NaN;
    } catch (_) {
      groupId = NaN;
    }
    groupId = Number.isInteger(groupId)
      ? await chrome.tabs.group({ groupId, tabIds: [tab.id] })
      : await chrome.tabs.group({ createProperties: { windowId: tab.windowId }, tabIds: [tab.id] });
    await chrome.tabGroups.update(groupId, { title: LOOM_TAB_GROUP_TITLE, color: "purple", collapsed: false });
    groupsByWindow[String(tab.windowId)] = groupId;
    await chrome.storage.session.set({ [OWNED_GROUP_IDS_KEY]: groupsByWindow });
    return markLoomWorkTab(await chrome.tabs.get(tab.id), { adopted });
  } catch (cause) {
    console.warn("[loom-browser-bridge] could not place work tab in group", cause);
    return markLoomWorkTab(tab, { adopted });
  }
}

const isInjectableUrl = (url) => /^https?:\/\//i.test(String(url || ""));

async function inject(tabId, func, args = [], world) {
  // Content scripts run in the ISOLATED world, which is governed by the
  // extension's own MV3 policy, and that policy forbids evaluating a string as
  // JavaScript. Anything that has to eval must run in MAIN, where the page's own
  // CSP applies instead - most pages allow it, a strict-CSP page does not.
  const target = { tabId };
  const options = { target, func, args };
  if (world) options.world = world;
  const results = await chrome.scripting.executeScript(options);
  const value = results?.[0]?.result;
  if (value && typeof value === "object" && value.__loomError) {
    throw new Error(value.__loomError);
  }
  return value || {};
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
    base.page_info.recovery = {
      action: "browser_navigate",
      automatic: true,
      creates_loom_work_tab: true,
      reason: "privileged_current_tab",
    };
    base.errors.push(
      "This is a protected browser page and cannot be inspected. Continue automatically with browser_navigate: "
      + "Loom will create its own grouped work tab without replacing this page. Do not ask the user to switch tabs.",
    );
    return base;
  }
  try {
    const page = await inject(tab.id, runPageAction, ["state", { show_hud: options.showHud !== false }]);
    await rememberElements(tab.id, page.elements);
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

async function rememberElements(tabId, elements) {
  // Only the opaque loom_id is kept: it is all elementRefFor needs, and storing
  // the serialized element would put page text into extension storage.
  const ids = (Array.isArray(elements) ? elements : []).map((item) => String(item?.loom_id || ""));
  const stored = await chrome.storage.session.get(ELEMENT_IDS_KEY);
  const byTab = { ...(stored[ELEMENT_IDS_KEY] || {}) };
  byTab[String(tabId)] = ids;
  await chrome.storage.session.set({ [ELEMENT_IDS_KEY]: byTab });
}

async function forgetElements(tabId) {
  const stored = await chrome.storage.session.get(ELEMENT_IDS_KEY);
  const byTab = { ...(stored[ELEMENT_IDS_KEY] || {}) };
  delete byTab[String(tabId)];
  await chrome.storage.session.set({ [ELEMENT_IDS_KEY]: byTab });
}

async function elementRefFor(tabId, index) {
  const stored = await chrome.storage.session.get(ELEMENT_IDS_KEY);
  const loomId = ((stored[ELEMENT_IDS_KEY] || {})[String(tabId)] || [])[Number(index)];
  if (!loomId) throw new Error("Element index is not available. Refresh browser_state and retry.");
  return loomId;
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

async function afterTabAction(tab, waitMs = 200, watcher = null, expectNavigation = false, navigationRuledOut = false) {
  await sleep(waitMs);
  let navWaited = false;
  if (watcher) {
    if (!watcher.started) {
      // A page that told us it is navigating gets the long window, because the
      // commit can take seconds on a slow origin. A page that told us it is not
      // gets only a settle tick - it has already had the sleep above. Anything
      // that reported nothing either way keeps the original grace.
      const grace = expectNavigation
        ? NAVIGATION_COMMIT_TIMEOUT_MS
        : (navigationRuledOut ? NAVIGATION_SETTLE_MS : NAVIGATION_GRACE_MS);
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

async function withNavigationWatch(tabId, run, waitMs, { canNavigate = true } = {}) {
  const watcher = watchTabNavigation(tabId);
  try {
    const outcome = await run();
    const expected = Boolean(outcome && outcome.navigation_expected);
    // Two ways to know no navigation is coming: the page said so for this element,
    // or the action cannot cause one at all. Typing text dispatches input events
    // and hovering dispatches mouse events; neither follows a link. Pressing a key,
    // selecting an option and dragging can all end in one, so they keep the grace.
    const ruledOut =
      !expected && (!canNavigate || (outcome && outcome.navigation_expected === false));
    return await afterTabAction({ id: tabId }, waitMs, watcher, expected, ruledOut);
  } finally {
    watcher.stop();
  }
}

async function navigate(args) {
  const url = String(args.url || "");
  if (!url) throw new Error("url is required");
  const destination = await resolveNavigationDestination(args, url);
  if (isInjectableUrl(destination.tab.url || "")) {
    await inject(destination.tab.id, runPageAction, ["hud_status", { title: "Opening page", subtitle: url }]).catch(() => {});
  }
  // Loom works beside the user, not in front of them. Activating the tab here
  // yanked the foreground onto Loom's page on every navigate, so someone reading
  // their own tab got pulled away at each step. Scripting, navigation and DOM
  // capture all work on a background tab; the purple group is how the work stays
  // visible, and browser_switch_tab is there when the model genuinely needs to
  // front something. Only screenshots need the tab visible, and they hand focus
  // back afterwards.
  const tab = destination.create
    ? await placeInLoomGroup(await chrome.tabs.create({ url, active: false }))
    : await chrome.tabs.update(destination.tab.id, { url });
  await waitForTabComplete(tab.id);
  return collectStateForTab(await chrome.tabs.get(tab.id), { showHud: true });
}

async function resolveNavigationDestination(args, url) {
  const current = await tabFromArgs(args);
  if (args.new_tab) return { create: true, tab: current };
  let targetUrl = "";
  try { targetUrl = new URL(url).href; } catch (_) { return { create: !(await isLoomWorkTab(current)), tab: current }; }
  const candidates = await chrome.tabs.query(
    typeof current.windowId === "number" ? { windowId: current.windowId } : { currentWindow: true },
  );
  const exact = candidates.find((candidate) => {
    if (!isInjectableUrl(candidate.url || "")) return false;
    try { return new URL(candidate.url).href === targetUrl; } catch (_) { return false; }
  });
  // Reusing a page the user already has open beats opening a duplicate, but it
  // also hands that tab to Loom: the next navigate to a different URL replaces
  // whatever is on it. Adopting it into the visible Loom group is the only thing
  // that tells the user their tab is now a work tab, so adopt and group together.
  if (exact) {
    return {
      create: false,
      tab: (await isLoomWorkTab(exact)) ? exact : await placeInLoomGroup(exact, { adopted: true }),
    };
  }
  if (await isLoomWorkTab(current)) return { create: false, tab: current };
  // A session binds to whatever the user happened to be looking at, so "current"
  // is usually their tab, not Loom's. Without this, every session concluded it had
  // no work tab and opened another one. Loom's own tab in this window is the work
  // tab regardless of where the user's attention is.
  const { own } = await ownedTabIds();
  const mine = candidates.find((candidate) => own.includes(candidate.id));
  if (mine) return { create: false, tab: mine };
  return { create: true, tab: current };
}

async function waitForTabComplete(tabId) {
  const started = Date.now();
  while (Date.now() - started < 15000) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return;
    await sleep(TAB_COMPLETE_POLL_MS);
  }
}

async function withElement(args, action, extra = {}, waitMs = 200, options = {}) {
  const tab = await tabFromArgs(args);
  const loomId = await elementRefFor(tab.id, args.index);
  return withNavigationWatch(
    tab.id,
    () => inject(tab.id, runPageAction, [action, { ...extra, loom_id: loomId, index: Number(args.index) }]),
    waitMs,
    options,
  );
}

async function click(args) {
  return withElement(args, "click", {}, 250);
}

async function hover(args) {
  return withElement(args, "hover", {}, 150, { canNavigate: false });
}

async function typeText(args) {
  return withElement(args, "type_text", { text: String(args.text || ""), clear: args.clear !== false }, 200, { canNavigate: false });
}

async function selectOption(args) {
  return withElement(args, "select_option", { value: String(args.value || "") }, 200);
}

async function drag(args) {
  const tab = await tabFromArgs(args);
  const payload = {
    source_loom_id: await elementRefFor(tab.id, args.source_index),
    target_loom_id: await elementRefFor(tab.id, args.target_index),
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

async function goForward(args) {
  const tab = await tabFromArgs(args);
  await chrome.tabs.goForward(tab.id);
  await waitForTabComplete(tab.id);
  return collectStateForTab(await chrome.tabs.get(tab.id), { showHud: true });
}

async function findText(args) {
  const tab = await requireInjectableTab(args);
  const outcome = await inject(tab.id, runPageAction, ["find_text", { text: String(args.text || "") }]);
  const state = await afterTabAction({ id: tab.id }, 150);
  state.found = Boolean(outcome && outcome.found);
  return state;
}

async function dropdownOptions(args) {
  const tab = await requireInjectableTab(args);
  const loomId = await elementRefFor(tab.id, args.index);
  const outcome = await inject(tab.id, runPageAction, [
    "dropdown_options",
    { loom_id: loomId, index: Number(args.index) },
  ]);
  return { options: (outcome && outcome.options) || [] };
}

async function evaluate(args) {
  const tab = await requireInjectableTab(args);
  return inject(
    tab.id,
    runPageAction,
    ["evaluate", { expression: String(args.expression || "") }],
    "MAIN",
  );
}

async function waitFor(args) {
  const started = Date.now();
  const timeoutMs = Math.max(500, Math.min(Number(args.timeout_seconds || 15) * 1000, 60000));
  const expression = String(args.until || "");
  if (!expression) {
    await sleep(Math.max(0, Math.min(Number(args.seconds || 0) * 1000, 60000)));
    return { satisfied: true, waited_ms: Date.now() - started };
  }
  let lastError = "";
  for (;;) {
    const tab = await requireInjectableTab(args);
    const outcome = await inject(
      tab.id,
      runPageAction,
      ["evaluate_condition", { expression }],
      "MAIN",
    );
    if (outcome && outcome.ok) {
      if (outcome.satisfied) return { satisfied: true, waited_ms: Date.now() - started };
      lastError = "";
    } else {
      lastError = String((outcome && outcome.error) || "");
    }
    if (Date.now() - started >= timeoutMs) {
      return { satisfied: false, waited_ms: Date.now() - started, error: lastError };
    }
    await sleep(250);
  }
}

async function originStorage(args) {
  const tab = await requireInjectableTab(args);
  const outcome = await inject(tab.id, runPageAction, ["origin_storage", {}]);
  return { origins: outcome && outcome.origin ? [outcome] : [] };
}

async function listCookies(args) {
  const tab = await tabFromArgs(args);
  const url = String(tab.url || "");
  if (!isInjectableUrl(url)) throw new Error("Cannot read cookies for a privileged browser page");
  const cookies = await chrome.cookies.getAll({ url });
  return { cookies: cookies.map((c) => ({ ...c, httpOnly: c.httpOnly, sameSite: c.sameSite })) };
}

async function clearCookies(args) {
  const tab = await tabFromArgs(args);
  const url = String(tab.url || "");
  if (!isInjectableUrl(url)) throw new Error("Cannot clear cookies for a privileged browser page");
  const cookies = await chrome.cookies.getAll({ url });
  for (const cookie of cookies) {
    const scheme = cookie.secure ? "https://" : "http://";
    const host = cookie.domain.startsWith(".") ? cookie.domain.slice(1) : cookie.domain;
    await chrome.cookies.remove({ url: scheme + host + cookie.path, name: cookie.name }).catch(() => {});
  }
  return { cleared: cookies.length };
}

async function listDownloads(args = {}) {
  // chrome.downloads.search sees the whole browser, so an unfiltered read hands
  // the model the user's entire recent download history. The filenames alone are
  // revealing, and browser_downloads promises only this session's files. Fail
  // closed: without a session start time there is nothing this session can claim.
  const since = Number(args.since_ms || 0);
  if (!Number.isFinite(since) || since <= 0) return { files: [] };
  const items = await chrome.downloads.search({ limit: 200, orderBy: ["-startTime"] });
  return {
    files: items
      .filter((item) => item.state === "complete" && item.filename)
      .filter((item) => Date.parse(item.startTime || "") >= since)
      .map((item) => ({ path: item.filename, bytes: item.fileSize || 0 })),
  };
}

async function requireInjectableTab(args) {
  const tab = await tabFromArgs(args);
  if (!isInjectableUrl(tab.url || "")) {
    throw new Error("Loom cannot inspect chrome://, edge://, extension, file, or other privileged pages.");
  }
  return tab;
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
  // The one action that cannot run in the background: captureVisibleTab only ever
  // returns the visible tab, so a background capture would silently hand back a
  // picture of whatever the user is reading. Front Loom's tab just long enough,
  // then put the user back where they were.
  let restore = null;
  if (!tab.active) {
    const [previous] = await chrome.tabs.query({ active: true, windowId: tab.windowId });
    restore = previous && previous.id !== tab.id ? previous.id : null;
    await chrome.tabs.update(tab.id, { active: true });
  }
  let dataUrl;
  try {
    dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  } finally {
    if (restore !== null) await chrome.tabs.update(restore, { active: true }).catch(() => {});
  }
  if (isInjectableUrl(tab.url || "")) {
    await inject(tab.id, runPageAction, ["hud_status", { title: "Screenshot captured", subtitle: "Saved into Loom workspace" }]).catch(() => {});
  }
  const marker = "base64,";
  const offset = dataUrl.indexOf(marker);
  if (offset < 0) throw new Error("captureVisibleTab did not return base64 PNG data");
  return { png_base64: dataUrl.slice(offset + marker.length), full_page: false };
}

async function dispatchCommand(action, args) {
  // Any command means Loom is driving this browser, and release_tabs is the one
  // that ends the session. Recording it here rather than at a session-start hook
  // keeps the flag true for the whole time commands are arriving, including after
  // a page load the HUD cannot otherwise know about.
  if (action !== "release_tabs") await markSessionActive(true);
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
    case "go_forward": return goForward(args);
    case "find_text": return findText(args);
    case "dropdown_options": return dropdownOptions(args);
    case "evaluate": return evaluate(args);
    case "wait_for": return waitFor(args);
    case "origin_storage": return originStorage(args);
    case "cookies": return listCookies(args);
    case "clear_cookies": return clearCookies(args);
    case "downloads": return listDownloads(args);
    case "release_tabs": return releaseTabs();
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
        const valued = ["input", "textarea", "select"].includes(tag);
        const secret = type === "password";
        const item = {
          index: elements.length,
          loom_id: ensureId(el),
          tag,
          text: clean(el.innerText || el.textContent || el.getAttribute("title") || ""),
          aria: clean(el.getAttribute("aria-label") || el.getAttribute("alt") || ""),
          placeholder: clean(el.getAttribute("placeholder") || ""),
          type,
          value: clean(valued && !secret ? el.value : ""),
          // A password value never reaches the model, but whether the field is
          // empty has to: without it a successful type looks exactly like one that
          // did nothing, so the model retries, gives up on the browser tools and
          // escalates to clicking the desktop. One bit, and the page already shows
          // it as dots - the value itself stays withheld.
          filled: secret ? Boolean(valued && el.value) : false,
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
      // Deliberately not a stand-in value: anything that looks like text invites
      // the model to type it back. This states the fact and cannot be mistaken
      // for the content.
      else if (item.filled) attrs.push('filled="true" value-withheld="password"');
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
    const wanted = String(args.value || "");
    showTargetHud(el, `Select #${Number(args.index)}`, clean(wanted, 180), "action");
    // browser_select is called with the option text that browser_dropdown_options
    // reports, or with the option's value. Assigning select.value matches only
    // the value, and assigning a string no option carries clears the selection
    // rather than failing, so an option picked by its visible label used to
    // silently deselect everything and still report success.
    let chosen = -1;
    for (let i = 0; i < el.options.length; i += 1) {
      const option = el.options[i];
      if (option.value === wanted || String(option.text).trim() === wanted.trim()) {
        chosen = i;
        break;
      }
    }
    if (chosen < 0) throw new Error(`No option matches ${JSON.stringify(wanted)} in this select`);
    el.selectedIndex = chosen;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, value: el.value };
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

  function findTextInPage() {
    const needle = String(args.text || "");
    const body = document.body;
    if (!body || !needle) return { found: false };
    // Walk text nodes rather than using window.find, which moves the selection
    // and behaves differently across engines.
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      if (String(node.nodeValue || "").indexOf(needle) !== -1) {
        const target = node.parentElement;
        if (target) {
          target.scrollIntoView({ block: "center", inline: "nearest" });
          showTargetHud(target, "Found text", clean(needle, 120), "target");
          return { found: true };
        }
      }
      node = walker.nextNode();
    }
    return { found: false };
  }

  function readDropdownOptions() {
    const el = targetById(args.loom_id);
    if (!(el instanceof HTMLSelectElement)) return { options: [] };
    showTargetHud(el, `Read options of #${Number(args.index)}`, `${el.options.length} options`, "target");
    const options = [];
    for (let i = 0; i < el.options.length && i < 300; i += 1) {
      const option = el.options[i];
      options.push({
        text: clean(option.text, 300),
        value: String(option.value || "").slice(0, 300),
        selected: Boolean(option.selected),
      });
    }
    return { options };
  }

  function evaluateInPage() {
    const source = String(args.expression || "");
    try {
      // Indirect eval runs in global scope, which is what a console expression
      // does and what the model is reaching for.
      const value = (0, eval)(source);
      return { ok: true, value: serializeValue(value), value_type: typeof value };
    } catch (cause) {
      return { ok: false, error: String(cause && cause.message ? cause.message : cause).slice(0, 2000) };
    }
  }

  function serializeValue(value) {
    if (value === undefined) return null;
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (cause) {
      return String(value).slice(0, 30000);
    }
  }

  function evaluateCondition() {
    const source = String(args.expression || "");
    try {
      return { ok: true, satisfied: Boolean((0, eval)(source)) };
    } catch (cause) {
      return { ok: false, error: String(cause && cause.message ? cause.message : cause).slice(0, 500) };
    }
  }

  function readOriginStorage() {
    const read = (store) => {
      const rows = [];
      try {
        for (let i = 0; i < store.length && i < 300; i += 1) {
          const name = store.key(i);
          rows.push({ name: String(name).slice(0, 300), value: String(store.getItem(name) || "").slice(0, 2000) });
        }
      } catch (cause) {
        return rows;
      }
      return rows;
    };
    return {
      origin: location.origin,
      localStorage: read(window.localStorage),
      sessionStorage: read(window.sessionStorage),
    };
  }

  // chrome.scripting.executeScript resolves rather than rejects when the
  // injected function throws, and the thrown error is not in the result, so an
  // action that failed in the page used to come back as an ordinary state with
  // no errors: a click on a stale index, or a select with no matching option,
  // reported success. The failure is returned as data and rethrown by inject().
  try {
    return dispatchPageAction();
  } catch (cause) {
    return {
      __loomError: String(cause && cause.message ? cause.message : cause).slice(0, 500),
    };
  }

  function dispatchPageAction() {
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
    case "find_text": return findTextInPage();
    case "dropdown_options": return readDropdownOptions();
    case "evaluate": return evaluateInPage();
    case "evaluate_condition": return evaluateCondition();
    case "origin_storage": return readOriginStorage();
    default: throw new Error(`Unsupported injected Loom page action: ${action}`);
  }
  }
}

chrome.runtime.onInstalled.addListener(() => {
  startInstalledUpdateWatcher();
  startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
});
chrome.runtime.onStartup.addListener(() => {
  startInstalledUpdateWatcher();
  startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
});
chrome.action.onClicked.addListener(() => {
  requestInstalledUpdateCheck();
});
// Content scripts are untrusted contexts, and storage.session hides from them by
// default, so the HUD could not read the session flag without this.
try {
  chrome.storage.session.setAccessLevel({ accessLevel: "TRUSTED_AND_UNTRUSTED_CONTEXTS" });
} catch (cause) {
  console.warn("[loom-browser-bridge] could not expose session state to the HUD", cause);
}
startInstalledUpdateWatcher();
startPolling().catch((cause) => console.warn("[loom-browser-bridge]", cause));
