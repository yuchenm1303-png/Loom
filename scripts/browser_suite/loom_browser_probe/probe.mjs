// Loom Browser's agent commands against the browser suite's test site, in a local Electron.
//
//   node desktop-react/node_modules/typescript/bin/tsc -p desktop-react/tsconfig.electron.json --outDir <BUILD>
//   python scripts/browser_suite/suite_site.py-style server (see run_suite.py), then:
//   PROBE_BUILD=<BUILD> PROBE_BASE=http://127.0.0.1:<port> PROBE_EXT=extensions/browser-current-tab //     [PROBE_ONSCREEN=1] desktop-react/node_modules/electron/dist/electron.exe scripts/browser_suite/loom_browser_probe
//
// Default: the window is hidden, so every tab is off screen (page-code input). PROBE_ONSCREEN=1 shows
// an invisible (opacity 0, unfocused) window with the panel open: native input. Paths come from the
// environment: Electron exits silently when an app folder is given extra arguments.
import { app, BrowserWindow } from "electron";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
app.setPath("userData", path.join(process.env.PROBE_BUILD, "probe-user-data"));
const base = process.env.PROBE_BASE, extension = process.env.PROBE_EXT;

const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 300) }); console.log(ok ? "PASS" : "FAIL", name, ok ? "" : String(detail).slice(0, 400)); };

app.whenReady().then(async () => {
  const { EmbeddedBrowserManager } = await import(pathToFileURL(path.join(process.env.PROBE_BUILD, "embeddedBrowser.js")).href);
  const { loadRunPageAction } = await import(pathToFileURL(path.join(process.env.PROBE_BUILD, "pageActions.js")).href);
  const onScreen = Boolean(process.env.PROBE_ONSCREEN);
  const win = new BrowserWindow({ x: 200, y: 120, width: 1200, height: 800, show: false, skipTaskbar: true });
  if (onScreen) { win.setOpacity(0); win.showInactive(); }
  await win.loadURL("data:text/html,<body>shell</body>");
  const manager = new EmbeddedBrowserManager(win,
    () => fs.readFile(path.join(extension, "browser-hud.js"), "utf8"),
    () => loadRunPageAction(path.join(extension, "background.js")));
  if (onScreen) await manager.command({ action: "bounds", bounds: { x: 0, y: 0, width: 1180, height: 760 } });
  const owner = "probe-session";
  const run = async (action, args = {}) => manager.agentCommand(action, { session_id: owner, ...args });
  const idx = (dom, re) => { const m = re.exec(dom); return m ? Number(m[1]) : -1; };
  try {
    let s = await run("state");
    const tab = s.page_info.tab_id;
    s = await run("navigate", { tab_id: tab, url: base + "/login" });
    check("login state has labelled fields", /label="Username"/.test(s.dom) && /\[2\] <button> text="Sign in"/.test(s.dom), s.dom);
    s = await run("type_text", { tab_id: tab, index: 0, text: "tester" });
    s = await run("type_text", { tab_id: tab, index: 1, text: "orange-42" });
    s = await run("click", { tab_id: tab, index: 2 });
    await new Promise(r => setTimeout(r, 600));
    s = await run("state", { tab_id: tab });
    check((onScreen ? "on-screen " : "off-screen ") + "sign-in navigates", s.url.endsWith("/account"), s.url);
    check("checkbox state is listed", /label="Email notifications" checked="false"/.test(s.dom), s.dom);
    s = await run("click", { tab_id: tab, index: 0 });
    s = await run("state", { tab_id: tab });
    check((onScreen ? "on-screen " : "off-screen ") + "checkbox click ticks it", /label="Email notifications" checked="true"/.test(s.dom), s.dom);

    s = await run("navigate", { tab_id: tab, url: base + "/embed-cross" });
    check("cross-origin frame fields listed", /Frame 1 \(http:\/\/localhost:\d+\/payframe/.test(s.dom) && /label="Card holder"/.test(s.dom), s.dom);
    const holder = idx(s.dom, /\[(\d+)\] <input>[^\n]*label="Card holder"/);
    const amount = idx(s.dom, /\[(\d+)\] <input>[^\n]*label="Amount"/);
    const pay = idx(s.dom, /\[(\d+)\] <button> text="Pay"/);
    await run("type_text", { tab_id: tab, index: holder, text: "Alan Turing" });
    await run("type_text", { tab_id: tab, index: amount, text: "40" });
    await run("click", { tab_id: tab, index: pay });
    await new Promise(r => setTimeout(r, 500));
    const paid = await fetch(base + "/api/result/xframe").then(r => r.json());
    check((onScreen ? "on-screen " : "off-screen ") + "payment in a cross-origin frame", paid.holder === "Alan Turing" && paid.amount === "40", JSON.stringify(paid));

    s = await run("navigate", { tab_id: tab, url: base + "/widget" });
    const qty = idx(s.dom, /\[(\d+)\] <input>[^\n]*label="Quantity"/);
    const apply = idx(s.dom, /\[(\d+)\] <button> text="Apply"/);
    await run("type_text", { tab_id: tab, index: qty, text: "4" });
    await run("click", { tab_id: tab, index: apply });
    await new Promise(r => setTimeout(r, 400));
    const widget = await fetch(base + "/api/result/widget").then(r => r.json());
    check((onScreen ? "on-screen " : "off-screen ") + "shadow-DOM widget", widget.quantity === "4", JSON.stringify(widget));

    s = await run("navigate", { tab_id: tab, url: base + "/feed" });
    await new Promise(r => setTimeout(r, 1500));
    let last = 0;
    for (let i = 0; i < 4; i += 1) {
      s = await run("scroll", { tab_id: tab, direction: "down", amount: 3000 });
      last = Math.max(0, ...[...s.dom.matchAll(/context="Item (\d+)"/g)].map(m => Number(m[1])));
    }
    check((onScreen ? "on-screen " : "off-screen ") + "scroll loads more rows", last > 40, "last row " + last);
  } catch (cause) {
    check("no exception", false, cause instanceof Error ? cause.stack : String(cause));
  }
  console.log("RESULTS " + JSON.stringify(results));
  app.quit();
});
