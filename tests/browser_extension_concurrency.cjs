const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const storage = {};
  const tabs = new Map([[1, { id: 1, windowId: 10, active: true, url: 'https://user.example/', status: 'complete' }]]);
  const detached = [];
  let nextId = 2;
  const event = () => ({ addListener() {}, removeListener() {} });
  const store = {
    async get(keys) {
      keys = typeof keys === 'string' ? [keys] : keys;
      return structuredClone(Object.fromEntries(keys.map((key) => [key, storage[key]])));
    },
    async set(value) { Object.assign(storage, structuredClone(value)); },
    async remove(keys) { for (const key of [].concat(keys)) delete storage[key]; },
    setAccessLevel() {},
  };
  const chrome = {
    runtime: { getManifest: () => ({ version: '0.1.18' }), onMessage: event(), onInstalled: event(), onStartup: event() },
    storage: { session: store, local: store },
    debugger: { onDetach: event(), async detach({ tabId }) { detached.push(tabId); } },
    tabs: {
      onRemoved: event(), onUpdated: event(),
      query(filter, callback) {
        const rows = [...tabs.values()].filter((tab) => !filter.active || tab.active);
        if (callback) callback(structuredClone(rows));
        else return Promise.resolve(structuredClone(rows));
      },
      async get(id) { if (!tabs.has(id)) throw new Error('Tab not found'); return { ...tabs.get(id) }; },
      async create(options) {
        const tab = { id: nextId++, windowId: 10, status: 'complete', ...options };
        tabs.set(tab.id, tab); return { ...tab };
      },
      async update(id, options) {
        if (options.active) {
          for (const tab of tabs.values()) if (tab.windowId === tabs.get(id).windowId) tab.active = false;
        }
        Object.assign(tabs.get(id), options); return { ...tabs.get(id) };
      },
      async captureVisibleTab(windowId) {
        await new Promise((resolve) => setImmediate(resolve));
        const active = [...tabs.values()].find((tab) => tab.windowId === windowId && tab.active);
        return 'data:image/png;base64,' + Buffer.from(String(active.id)).toString('base64');
      },
      async remove(id) { tabs.delete(id); },
    },
    scripting: { async executeScript() { return [{ result: { dom: '[0] button', elements: [] } }]; } },
    windows: { async update() {} },
    alarms: { onAlarm: event(), create() {} }, action: { onClicked: event() },
  };
  const context = vm.createContext({ chrome, console, setTimeout, clearTimeout, URL, Headers, navigator: {} });
  const source = fs.readFileSync(path.join(__dirname, '../extensions/browser-current-tab/background.js'), 'utf8');
  // Declare the real runtime without starting its permanent network polling loop.
  vm.runInContext(source.slice(0, source.lastIndexOf('\nstartInstalledUpdateWatcher();')), context);
  return { context, storage, tabs, detached, run: (code) => vm.runInContext(code, context) };
}

async function main() {
  const h = harness();
  const [a, b] = await h.run(`Promise.all([
    dispatchCommand('state', { session_id: 'a' }),
    dispatchCommand('state', { session_id: 'b' })
  ])`);
  const aId = Number(a.page_info.tab_id), bId = Number(b.page_info.tab_id);
  assert.notEqual(aId, bId, 'simultaneous opens must get distinct tabs');
  assert.equal(h.storage.loomTabSessionIds[aId], 'a');
  assert.equal(h.storage.loomTabSessionIds[bId], 'b');
  assert(!a.tabs.some((tab) => Number(tab.tab_id) === bId));
  await assert.rejects(h.run(`dispatchCommand('switch_tab', { session_id: 'a', tab_id: '${bId}' })`), /another Loom task/);
  await assert.rejects(h.run(`dispatchCommand('close_tab', { session_id: 'a', tab_id: '${bId}' })`), /another Loom task/);
  assert(h.tabs.has(bId));

  const [navA, navB] = await h.run(`Promise.all([
    dispatchCommand('navigate', { session_id: 'a', tab_id: '${aId}', url: 'https://same.example/' }),
    dispatchCommand('navigate', { session_id: 'b', tab_id: '${bId}', url: 'https://same.example/' })
  ])`);
  assert.notEqual(navA.page_info.tab_id, navB.page_info.tab_id, 'navigation must not adopt another controller\'s tab');
  // Force the visible-tab screenshot fallback. Two captures in the same window
  // must not return the other task's page while changing/restoring focus.
  h.run(`withNativeInput = async () => {}`);
  const screenshots = await h.run(`Promise.all([
    dispatchCommand('screenshot', { session_id: 'a', tab_id: '${navA.page_info.tab_id}' }),
    dispatchCommand('screenshot', { session_id: 'b', tab_id: '${navB.page_info.tab_id}' })
  ])`);
  assert.equal(Buffer.from(screenshots[0].png_base64, 'base64').toString(), navA.page_info.tab_id);
  assert.equal(Buffer.from(screenshots[1].png_base64, 'base64').toString(), navB.page_info.tab_id);
  const ownedA = Object.keys(h.storage.loomTabSessionIds).filter((id) => h.storage.loomTabSessionIds[id] === 'a').map(Number);
  const ownedB = Object.keys(h.storage.loomTabSessionIds).filter((id) => h.storage.loomTabSessionIds[id] === 'b').map(Number);
  await h.run(`for (const id of ${JSON.stringify([...ownedA, ...ownedB])}) attachedDebuggerTabs.add(id)`);
  h.storage.loomAdoptedTabIds = [...ownedA, ...ownedB];
  const leasesBefore = JSON.stringify(h.storage.loomTabSessionIds);
  const adoptedBefore = [...h.storage.loomAdoptedTabIds];
  await h.run(`dispatchCommand('finish_turn', { session_id: 'a' })`);
  assert.equal(JSON.stringify(h.storage.loomTabSessionIds), leasesBefore);
  assert.deepEqual(h.storage.loomAdoptedTabIds, adoptedBefore);
  assert.deepEqual(h.detached, []);
  assert.equal(h.storage.loomBrowserSessionActive, true);
  assert(ownedB.every((id) => h.storage.loomHudTabIds.includes(id)));
  assert(ownedA.every((id) => !h.storage.loomHudTabIds.includes(id)));
  await h.run(`dispatchCommand('release_tabs', { session_id: 'a' })`);
  assert.deepEqual(h.detached.sort(), ownedA.sort());
  assert.deepEqual(h.storage.loomAdoptedTabIds, ownedB);
  assert.equal(h.storage.loomBrowserSessionActive, true);
  assert(ownedB.every((id) => h.storage.loomHudTabIds.includes(id)));
  await h.run(`dispatchCommand('state', { session_id: 'b', tab_id: '${navB.page_info.tab_id}' })`);
  await h.run(`dispatchCommand('release_tabs', { session_id: 'b' })`);
  assert.equal(h.storage.loomBrowserSessionActive, false);
  assert.deepEqual(h.storage.loomTabSessionIds, {});

  // Exercise the real poll scheduler: a wait in A must not hold up B, while a
  // second command in A must stay behind the first one.
  const p = harness();
  await p.run(`(async () => {
    globalThis.events = [];
    globalThis.releaseA = null;
    globalThis.waitA = new Promise((resolve) => { releaseA = resolve; });
    globalThis.commands = [
      { id: 'a1', action: 'wait', args: { session_id: 'a' } },
      ...Array.from({ length: 7 }, (_, i) => ({ id: 'a' + (i + 2), action: 'state', args: { session_id: 'a' } })),
      { id: 'b1', action: 'state', args: { session_id: 'b' } }
    ];
    getClientId = async () => 'client';
    browserName = () => 'Edge';
    bridgeFetch = async (url, options) => {
      if (options) { events.push(JSON.parse(options.body).id + ':result'); return {}; }
      return { ok: true, json: async () => ({ command: commands.shift() }) };
    };
    dispatchCommand = async (action, args) => {
      events.push(args.session_id + ':' + action);
      if (action === 'wait') await waitA;
      return {};
    };
    for (let i = 0; i < 9; i++) await pollOnce();
  })()`);
  await new Promise((resolve) => setImmediate(resolve));
  assert(p.run(`events.includes('b1:result')`), 'B must finish before A stops waiting');
  assert(!p.run(`events.includes('a:state')`), 'commands in A must remain ordered');
  await p.run(`(async () => { releaseA(); await Promise.all([...inFlightCommands]); })()`);
  assert(p.run(`events.indexOf('a1:result') < events.indexOf('a:state')`));

  // A restarted broker must retire old leases and debugger attachments without
  // closing the pages. Exercise reset through the real poll path.
  const r = harness();
  await r.run(`dispatchCommand('state', { session_id: 'dead-runtime:tab' })`);
  r.run(`attachedDebuggerTabs.add(1)`);
  r.storage.loomBrowserBrokerId = 'old-broker';
  const pageCount = r.tabs.size;
  await r.run(`(async () => {
    getClientId = async () => 'client'; browserName = () => 'Edge';
    bridgeFetch = async (url, options) => options ? {} : {
      ok: true, json: async () => ({ command: { id: 'new', action: 'state', args: {
        session_id: 'new-runtime:tab', broker_id: 'new-broker'
      } } })
    };
    await pollOnce(); await Promise.all([...inFlightCommands]);
  })()`);
  assert.deepEqual(r.detached, [1]);
  assert.equal(r.storage.loomBrowserBrokerId, 'new-broker');
  assert.equal(r.storage.loomTabSessionIds[1], 'new-runtime:tab');
  assert.equal(r.tabs.size, pageCount);
  console.log('Browser extension concurrency: independent tabs, scoped release, cross-session progress and ordering passed.');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
