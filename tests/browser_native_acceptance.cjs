// Real Chromium regression for editing and event delivery. Never uses user tabs.
// Run with NODE_PATH pointing at a Playwright installation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage();
    const cdp = await page.context().newCDPSession(page);
    await page.setContent(`<input id="a" data-loom-bridge-id="a"><input id="max" maxlength="8" data-loom-bridge-id="max">
      <input id="ro" value="BASE" readonly data-loom-bridge-id="ro"><input id="disabled" disabled data-loom-bridge-id="disabled">
      <textarea id="ta" data-loom-bridge-id="ta"></textarea><div contenteditable id="ce" data-loom-bridge-id="ce"></div>
      <button id="button">Click</button>`);
    await page.evaluate(() => {
      window.events = [];
      for (const type of ['input', 'beforeinput', 'keydown', 'keyup', 'click', 'focus'])
        window.addEventListener(type, e => events.push({ type, key: e.key, trusted: e.isTrusted, id: e.target.id }), true);
    });
    // Real extension actions often run in a background tab after screenshot
    // restores the user's tab. Test that state, not only a focused page.
    const decoy = await browser.newPage();
    await decoy.bringToFront();
    const event = { addListener() {}, removeListener() {} };
    const chrome = {
      runtime: { getManifest: () => ({ version: '0.1.20' }), onMessage: event, onInstalled: event, onStartup: event },
      debugger: { onDetach: event, attach: async () => {}, sendCommand: async (_, method, params) => cdp.send(method, params) },
      tabs: { onRemoved: event, onUpdated: event }, alarms: { onAlarm: event }, action: { onClicked: event },
      storage: { session: { setAccessLevel() {} } },
      scripting: { executeScript: async ({ func, args }) => [{ result: await page.evaluate(
        ({ source, args }) => (0, eval)(`(${source})`)(...args), { source: func.toString(), args }) }] },
    };
    const context = vm.createContext({ chrome, console, setTimeout, clearTimeout, URL, Headers, navigator: {} });
    const source = fs.readFileSync(path.join(__dirname, '../extensions/browser-current-tab/background.js'), 'utf8');
    vm.runInContext(source.slice(0, source.lastIndexOf('\nstartInstalledUpdateWatcher();')), context);
    vm.runInContext(`actionTab = async () => ({id: 1, url: 'http://localhost/'});
      elementRefFor = async (_, index) => index;
      announceAction = async () => {};
      withNavigationWatch = async (_, action) => action();`, context);
    const run = (expr) => vm.runInContext(expr, context);
    const type = (id, text, clear = true) => run(`typeText(${JSON.stringify({ index: id, text, clear })})`);
    const value = id => page.$eval(`#${id}`, el => el.value);
    await type('a', '中文🚀');
    assert.equal(await value('a'), '中文🚀');
    assert.ok(await page.evaluate(() => events.some(e => e.type === 'input' && e.trusted)));
    await type('max', 'ACCEPT_PLAIN_001');
    assert.equal(await value('max'), 'ACCEPT_P');
    for (const id of ['ro', 'disabled']) await assert.rejects(type(id, 'BAD'), /disabled or readonly/);
    assert.equal(await value('ro'), 'BASE');
    await type('ta', '中文 line1\nline2 🚀');
    assert.equal(await value('ta'), '中文 line1\nline2 🚀');
    await type('a', 'AAA_BBB');
    await page.$eval('#a', el => el.setSelectionRange(3, 4));
    await type('a', 'MID', false);
    assert.equal(await value('a'), 'AAAMIDBBB');
    await type('a', '');
    assert.equal(await value('a'), '');
    await type('ce', 'ABC');
    await page.$eval('#ce', el => {
      const range = document.createRange(); range.setStart(el.firstChild, 1); range.collapse(true);
      const s = getSelection(); s.removeAllRanges(); s.addRange(range);
    });
    await type('ce', '中', false);
    assert.equal(await page.$eval('#ce', el => el.textContent), 'A中BC');
    await page.focus('#a');
    await run('pressKey({key: "Escape"})');
    assert.ok(await page.evaluate(() => events.some(e => e.type === 'keydown' && e.key === 'Escape' && e.trusted)));
    await run('pressKey({key: "Tab"})');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'max');
    const rect = await page.locator('#button').boundingBox();
    await run(`clickAt({x: ${rect.x + rect.width / 2}, y: ${rect.y + rect.height / 2}})`);
    assert.ok(await page.evaluate(() => events.some(e => e.type === 'click' && e.id === 'button' && e.trusted)));
    // The isolated backend uses this exact preparation function in the node's CDP frame.
    const py = fs.readFileSync(path.join(__dirname, '../app/agent_runtime/browser_native_edit.py'), 'utf8');
    const prepare = py.split('PREPARE_EDIT = r"""')[1].split('"""')[0];
    for (const [id, text, clear] of [['a', 'AAA_BBB', true], ['max', '123456789', true], ['ta', 'A\nB', true]]) {
      await page.$eval(`#${id}`, (el, source) => (0, eval)(`(${source})`).call(el, true), prepare);
      await cdp.send('Input.insertText', { text });
    }
    assert.equal(await value('max'), '12345678');
    assert.equal(await value('ta'), 'A\nB');
    await page.$eval('#a', el => el.setSelectionRange(3, 4));
    await page.$eval('#a', (el, source) => (0, eval)(`(${source})`).call(el, false), prepare);
    await cdp.send('Input.insertText', { text: 'MID' });
    assert.equal(await value('a'), 'AAAMIDBBB');
    await assert.rejects(page.$eval('#ro', (el, source) => (0, eval)(`(${source})`).call(el, true), prepare), /readonly/);
    console.log('Real Edge acceptance: editing, maxlength, readonly/disabled, newline, selection, clear, contenteditable, Escape, Tab, coordinate click PASS');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
