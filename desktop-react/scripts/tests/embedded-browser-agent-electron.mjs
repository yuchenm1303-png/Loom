// AI tasks in Loom Browser, in real Electron: a task opens a tab of its own (own cookie partition,
// never restored, panel revealed), the user's tabs still need their consent, background tabs keep
// answering, and the browser_look / browser_measure_motion page scripts work on a real page.
//
//   npm run build:electron
//   node_modules/electron/dist/electron.exe scripts/tests/embedded-browser-agent-electron.mjs
//
// LOOM_ELECTRON_DIST points at another compiled electron/ folder (default dist-electron).
// Exit code 1 when a check fails; each check prints one line.
import { app, BrowserWindow, session } from "electron";
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = process.env.LOOM_ELECTRON_DIST || path.join(here, "../../dist-electron");
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "loom-embedded-agent-"));
app.setPath("userData", userData);
const timeout = setTimeout(() => { console.error("FAIL timed out (a command never answered)"); app.exit(1); }, 60_000);
const failures = [];
const check = (name, ok, detail) => {
  console.log((ok ? "ok   " : "FAIL ") + name + (ok || detail === undefined ? "" : " " + JSON.stringify(detail)));
  if (!ok) failures.push(name);
};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

// The page scripts exactly as the Python tools send them.
const py = fs.readFileSync(path.join(here, "../../../app/agent_runtime/browser_iterate.py"), "utf8");
const block = name => py.match(new RegExp(name + ' = """([\\s\\S]*?)"""'))[1];
const quoted = name => py.match(new RegExp(name + ' = "(.*)"'))[1].replace(/\\"/g, '"');
const SETTLE = block("_SETTLE_SCRIPT").replace("%(settle)d", "200").replace("%(selector)s", JSON.stringify(".panel"));
const RECORD = block("_RECORD_SCRIPT"), ANIMS = block("_ANIMATIONS_SCRIPT"), COLLECT = block("_COLLECT_SCRIPT");
const MARK = quoted("_MARK_SCRIPT");

const page = `<!doctype html><style>body{margin:0;font:16px sans-serif}
.panel{position:absolute;left:40px;top:80px;width:200px;height:120px;background:#4a6cf7;transition:transform 400ms ease-out}
.panel.open{transform:translateX(300px)}</style>
<button onclick="document.querySelector('.panel').classList.toggle('open')">Toggle</button><div class="panel"></div>`;

app.whenReady().then(async () => {
  const { EmbeddedBrowserManager } = await import(pathToFileURL(path.join(dist, "embeddedBrowser.js")).href);
  const server = http.createServer((_req, res) => { res.setHeader("Set-Cookie", "probe=1; Path=/"); res.end(page); });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  const win = new BrowserWindow({ width: 900, height: 700, show: true, webPreferences: { sandbox: true } });
  await win.loadURL("about:blank");
  const sent = [];
  const send = win.webContents.send.bind(win.webContents);
  win.webContents.send = (channel, ...args) => { sent.push([channel, ...args]); return send(channel, ...args); };
  const manager = new EmbeddedBrowserManager(win, () => "");
  const full = { x: 0, y: 0, width: 900, height: 700 };
  await manager.command({ action: "bounds", bounds: full });
  const userTab = manager.snapshot().activeId;

  const first = await manager.agentCommand("state", { session_id: "S1" });
  const agentTab = first.page_info.tab_id;
  check("a task opens a tab of its own", agentTab && agentTab !== userTab);
  check("the panel is asked to show it", sent.some(([c, a]) => c === "loom:embedded-browser-reveal" && a.tabId === agentTab));
  const marked = manager.snapshot().tabs.find(t => t.id === agentTab);
  check("the tab is marked as the task's and allowed", marked?.agentOwned && marked?.aiAllowed, marked);

  const nav = await manager.agentCommand("navigate", { session_id: "S1", url });
  check("the task navigates its tab", nav.url === url && /Toggle/.test(nav.dom), nav.url);
  await manager.command({ action: "select-tab", tabId: userTab });
  const back = await manager.agentCommand("state", { session_id: "S1" });
  check("after the user switches away the task returns to its tab", back.page_info.tab_id === agentTab
    && manager.snapshot().tabs.filter(t => t.agentOwned).length === 1);
  const started = Date.now();
  const hidden = await manager.agentCommand("evaluate", { session_id: "S1", tab_id: agentTab, expression: SETTLE });
  check("a look at a background tab answers promptly and says it did not paint",
    hidden.value?.painted === false && Date.now() - started < 2400, { ms: Date.now() - started, value: hidden.value });
  const shot = await manager.agentCommand("screenshot", { session_id: "S1", tab_id: agentTab }).catch(e => ({ error: e.message }));
  check("a screenshot brings the task's tab back in front", String(shot.png_base64 || "").length > 1000
    && manager.snapshot().activeId === agentTab, shot.error);

  await manager.command({ action: "select-tab", tabId: userTab });
  await manager.command({ action: "navigate", url: url.replace("127.0.0.1", "localhost") });
  await manager.command({ action: "allow-ai", tabId: userTab, enabled: true });
  const userOnScreen = await manager.agentCommand("screenshot", { session_id: "S9" });
  await manager.command({ action: "select-tab", tabId: agentTab });
  const userShot = await manager.agentCommand("screenshot", { session_id: "S9", tab_id: userTab }).then(() => "", e => e.message);
  check("a user's tab is never pulled in front of what they look at", String(userOnScreen.png_base64 || "").length > 1000
    && /not on screen/.test(userShot) && manager.snapshot().activeId === agentTab, userShot);
  await manager.command({ action: "allow-ai", tabId: userTab, enabled: false });

  await manager.command({ action: "bounds", bounds: null });
  const before = sent.length;
  const closed = await Promise.race([
    manager.agentCommand("screenshot", { session_id: "S1", tab_id: agentTab }).then(() => "captured", e => e.message),
    delay(8000).then(() => "hang")]);
  check("with the panel closed a screenshot asks for it and fails clearly",
    sent.slice(before).some(([c]) => c === "loom:embedded-browser-reveal") && /not showing/.test(closed), closed);
  await manager.command({ action: "bounds", bounds: full });
  await manager.command({ action: "select-tab", tabId: agentTab });

  const settle = await manager.agentCommand("evaluate", { session_id: "S1", expression: SETTLE });
  check("the look settles and finds the element box", settle.value?.painted === true && settle.value?.box?.width === 200, settle.value);
  await manager.agentCommand("evaluate", { session_id: "S1", expression: RECORD });
  await delay(100);
  await manager.agentCommand("evaluate", { session_id: "S1", expression: MARK });
  await manager.agentCommand("click", { session_id: "S1", index: 0 });
  await delay(120);
  const animations = await manager.agentCommand("evaluate", { session_id: "S1", expression: ANIMS });
  check("the running transition is listed", (animations.value || []).some(a => a.name === "transform" && a.duration_ms === 400),
    animations.value);
  await delay(600);
  const record = (await manager.agentCommand("evaluate", { session_id: "S1", expression: COLLECT })).value;
  check("the page's frame clock is recorded around the trigger",
    (record?.frames || []).length > 20 && record.trigger > record.start, record && { frames: record.frames.length });

  const other = await manager.agentCommand("state", { session_id: "S2" });
  check("another task gets a tab of its own", other.page_info.tab_id !== agentTab && other.page_info.tab_id !== userTab);
  const agentCookies = await session.fromPartition("loom-embedded-agent").cookies.get({ url });
  const userCookies = await session.fromPartition("persist:loom-embedded-browser").cookies.get({ url });
  check("cookies a task's tab sets stay out of the user's partition", agentCookies.length === 1 && userCookies.length === 0,
    { agent: agentCookies.length, user: userCookies.length });
  const opened = await manager.agentCommand("navigate", { session_id: "S1", url, new_tab: true });
  check("a task may open another tab of its own", opened.page_info.tab_id !== agentTab
    && manager.snapshot().tabs.find(t => t.id === opened.page_info.tab_id)?.agentOwned);
  await delay(300);
  const saved = JSON.parse(fs.readFileSync(path.join(userData, "embedded-browser-tabs.json"), "utf8"));
  check("the task's tabs are not restored next launch", !saved.urls.includes(url), saved);

  await manager.command({ action: "allow-ai", tabId: agentTab, enabled: false });
  const blocked = await manager.agentCommand("state", { session_id: "S3" }).then(() => "", e => e.message);
  check("turning AI off on a task's tab stops new ones", /turned AI off/.test(blocked), blocked);
  await manager.command({ action: "select-tab", tabId: userTab });
  const refused = await manager.agentCommand("state", { session_id: "S4", tab_id: userTab }).then(() => "", e => e.message);
  check("a user's tab still needs their consent", /Enable AI/.test(refused), refused);
  await manager.command({ action: "allow-ai", tabId: userTab, enabled: true });
  const allowed = await manager.agentCommand("state", { session_id: "S5" });
  check("allowing a tab lifts the stop and that tab is used", allowed.page_info.tab_id === userTab);

  manager.dispose();
  server.close();
  clearTimeout(timeout);
  // Electron still holds its profile here; removing it is best effort.
  try { fs.rmSync(userData, { recursive: true, force: true }); } catch { /* left in the temp folder */ }
  console.log(failures.length ? `${failures.length} failed` : "all passed");
  app.exit(failures.length ? 1 : 0);
}).catch(error => { console.error("FAIL " + (error.stack || error)); app.exit(1); });
