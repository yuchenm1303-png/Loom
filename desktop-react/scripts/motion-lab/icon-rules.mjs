// Which stylesheet rules set motion properties on an element? Uses the DevTools
// protocol (CSS.getMatchedStylesForNode) with :hover forced on the host.
//   node scripts/motion-lab/icon-rules.mjs --click ".model-chip" --target ".composer-popover [data-icon=chevron-right]"
//        [--hover ".model-core-selector"] [--props transform,translate,rotate,scale,filter,transition,animation]
import fs from "node:fs";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const props = (args.props || "transform,translate,rotate,scale,filter,transition,animation,transform-origin,will-change").split(",");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1.5 });
const page = await ctx.newPage();
await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${args.theme || "dark"}&lang=zh-CN${args.suffix || ""}`);
await page.waitForSelector(".app-shell .composer", { timeout: 90000 });
await page.waitForTimeout(1500);
for (const sel of (args.click || "").split("||").filter(Boolean)) { await page.locator(sel).first().click({ force: true }); await page.waitForTimeout(900); }

const cdp = await ctx.newCDPSession(page);
await cdp.send("DOM.enable"); await cdp.send("CSS.enable");
const { root } = await cdp.send("DOM.getDocument", { depth: 0 });
const find = async (sel) => (await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector: sel })).nodeId;
const targetId = await find(args.target);
if (!targetId) { console.log("target not found:", args.target); process.exit(2); }
const forced = [];
for (const sel of (args.hover || "").split("||").filter(Boolean)) {
  const id = await find(sel);
  if (id) { await cdp.send("CSS.forcePseudoState", { nodeId: id, forcedPseudoClasses: ["hover"] }); forced.push(sel); }
}
await page.waitForTimeout(700);
const { matchedCSSRules } = await cdp.send("CSS.getMatchedStylesForNode", { nodeId: targetId });
const sheets = new Map();
console.log(`target ${args.target}; forced :hover on ${forced.length ? forced.join(", ") : "nothing"}`);
for (const m of matchedCSSRules) {
  const rule = m.rule;
  const hit = rule.style.cssProperties.filter((p) => props.includes(p.name) && !p.disabled);
  if (!hit.length) continue;
  const header = rule.styleSheetId ? await cdp.send("CSS.getStyleSheetText", { styleSheetId: rule.styleSheetId }).then(() => null).catch(() => null) : null;
  const range = rule.style.range;
  console.log(`  ${rule.selectorList.text.replace(/\s+/g, " ").slice(0, 150)}`);
  console.log(`      ${hit.map((p) => `${p.name}: ${String(p.value).slice(0, 70)}${p.important ? " !important" : ""}`).join("; ")}   [${rule.origin}${range ? " line " + (range.startLine + 1) : ""}]`);
}
await browser.close();
