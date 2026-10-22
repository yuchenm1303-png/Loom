// Motion lab: a contact sheet of controls at rest, under the pointer and pressed (states forced through the DevTools
// protocol), for reading what a press looks like across the app: rows, chips, icon buttons, menu items, cards, the
// docks' close button, settings. motion/press.css is what draws the press.
//   node press-sheet.mjs <origin> <out.png> [--theme dark|light]

const argv = process.argv.slice(2);
const option = (name, fallback = "") => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const origin = argv.find((a) => /^https?:/.test(a)) || process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const out = argv.find((a) => a.endsWith(".png"));
const theme = option("--theme", "dark");
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const cdp = await page.context().newCDPSession(page);
await cdp.send("DOM.enable");
await cdp.send("CSS.enable");

const go = async (q = "") => {
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=en&agents=1${q}`);
  await page.locator(".thread-header").waitFor({ timeout: 90000 });
  await page.waitForTimeout(1200);
};
const shots = [];
async function shoot(label, selector, { index = 0, force = null } = {}) {
  const handle = (await page.$$(selector))[index];
  if (!handle) { shots.push({ label, missing: selector }); return; }
  const box = await handle.boundingBox();
  if (!box) { shots.push({ label, missing: selector + " (no box)" }); return; }
  const clip = { x: Math.max(0, box.x - 6), y: Math.max(0, box.y - 6), width: Math.min(560, box.width + 12), height: box.height + 12 };
  const { root } = await cdp.send("DOM.getDocument", { depth: 0 });
  const target = force ? (await page.$$(force))[index] : handle;
  await target.evaluate((el) => el.setAttribute("data-sheet-target", "1"));
  const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector: "[data-sheet-target]" });
  const states = [];
  for (const pseudo of [[], ["hover"], ["hover", "active"]]) {
    await cdp.send("CSS.forcePseudoState", { nodeId, forcedPseudoClasses: pseudo });
    await page.waitForTimeout(pseudo.includes("active") ? 140 : 420);
    states.push((await page.screenshot({ clip })).toString("base64"));
  }
  await cdp.send("CSS.forcePseudoState", { nodeId, forcedPseudoClasses: [] });
  await target.evaluate((el) => el.removeAttribute("data-sheet-target"));
  shots.push({ label, states, w: clip.width, h: clip.height });
}

await page.mouse.move(2, 890);
await go();
await page.locator(".project-disclosure-button").first().click();
await page.waitForTimeout(600);
await page.mouse.move(2, 890);
await shoot("sidebar conversation row", ".compact-thread-row", { index: 1 });
await shoot("project row", ".project-group-main");
await shoot("sidebar toggle", ".codex-sidebar-topbar .sidebar-toggle");
await shoot("message action", ".message-action-button");
await shoot("process header", ".turn-process-header");
await shoot("composer chip (model)", ".composer .model-chip");
await shoot("composer tool (attach)", ".composer .composer-tool");
await shoot("dock tool", ".loom-tool-button", { index: 2 });
await page.locator(".composer .model-chip").click();
await page.waitForTimeout(700);
await shoot("model picker row", ".model-popover [role=option], .model-popover .mp-row, .model-popover button", { index: 3 });
await page.keyboard.press("Escape");
await page.waitForTimeout(400);
await page.locator(".composer button", { hasText: /Full access/ }).first().click();
await page.waitForTimeout(700);
await shoot("permission option", ".composer-option", { index: 0 });
await page.keyboard.press("Escape");
await page.waitForTimeout(400);
await page.locator(".assistant-message").first().click({ button: "right" });
await page.waitForTimeout(500);
await shoot("context menu item", ".loom-context-action", { index: 1 });
await page.keyboard.press("Escape");
await page.waitForTimeout(400);
await page.locator('.loom-tool-button[data-tool-id="review"]').click();
await page.waitForTimeout(900);
await shoot("dock close", ".pl-close");
await page.locator('.loom-tool-button[data-tool-id="settings"]').click();
await page.waitForTimeout(1300);
await shoot("settings nav row", ".settings-nav button", { index: 2 });
await shoot("settings back", ".settings-back");
await shoot("settings switch", ".settings-switch");
await go("&empty=1");
await page.mouse.move(2, 890);
await shoot("starter card", ".starter-card");

const cells = shots.map((s) => s.missing
  ? `<div class="row"><div class="label">${s.label}<br><small>missing ${s.missing}</small></div></div>`
  : `<div class="row"><div class="label">${s.label}</div>${s.states.map((b, i) => `<figure><img src="data:image/png;base64,${b}" style="width:${s.w}px;height:${s.h}px"><figcaption>${["rest", "hover", "press"][i]}</figcaption></figure>`).join("")}</div>`).join("");
const sheet = await browser.newPage({ viewport: { width: 1800, height: 900 }, deviceScaleFactor: 1 });
await sheet.setContent(`<html><body style="margin:0;padding:16px;background:${theme === "light" ? "#e9eaee" : "#202228"};font:13px Segoe UI;color:${theme === "light" ? "#222" : "#ddd"}">
<style>.row{display:flex;align-items:center;gap:16px;margin-bottom:10px}.label{width:180px}figure{margin:0;text-align:center}img{display:block;image-rendering:pixelated;outline:1px solid rgba(128,128,128,.25)}figcaption{font-size:11px;opacity:.6}</style>${cells}</body></html>`);
await sheet.screenshot({ path: out, fullPage: true });
await browser.close();
console.log("wrote", out, shots.length, "controls", shots.filter((s) => s.missing).map((s) => s.label));
