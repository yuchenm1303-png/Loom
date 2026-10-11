// Motion lab: which controls change their look on hover, focus or press without a transition to carry it?
//   node state-audit.mjs <url-of-live-app.html> [--scene home|settings|composer|menu] [--states hover,focus,active] [--theme dark|light]
// For every visible control it forces :hover (then :focus-visible, :active) through the DevTools protocol, reads the
// computed style before and after, and checks that every property that changed is covered by a transition with a real
// duration. A control that flips colour, background, border, shadow, opacity or position in one frame shows up here
// with the properties it flips. Scenes open a surface first (settings, the composer's popovers, a context menu).
import { launch } from "./launch.mjs";

const argv = process.argv.slice(2);
const option = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const url = argv.find((arg) => !arg.startsWith("--") && !["--scene", "--states", "--theme"].includes(argv[argv.indexOf(arg) - 1]));
const scene = option("--scene", "home");
const states = option("--states", "hover,focus,active").split(",");
const theme = option("--theme", "dark");
const PROPS = ["color", "background-color", "border-top-color", "border-left-color", "box-shadow", "opacity", "transform", "translate", "scale", "rotate", "filter", "outline-color", "outline-width", "text-decoration-color", "fill", "stroke", "width", "height", "padding-left", "margin-left", "background-image", "letter-spacing"];
const FORCE = { hover: ["hover"], focus: ["focus", "focus-visible"], active: ["active", "hover"] };

const browser = await launch();
try {
  const page = await browser.newPage({ viewport: { width: 1180, height: 780 }, deviceScaleFactor: 1 });
  await page.goto(`${url}${url.includes("?") ? "&" : "?"}theme=${theme}`);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  await page.waitForTimeout(1500);
  if (scene === "settings") { await page.locator(".thread-settings-button").click(); await page.waitForTimeout(1600); }
  if (scene === "permission") { await page.locator('[aria-label^="权限设置"], [title^="权限设置"]').first().click(); await page.waitForTimeout(900); }
  if (scene === "model") { await page.locator(".model-chip").click(); await page.waitForTimeout(1000); }
  if (scene === "sticker") { await page.locator('[aria-label^="表情设置"], [title^="表情设置"]').first().click(); await page.waitForTimeout(900); }
  if (scene === "menu") { await page.locator(".compact-thread-main").nth(1).click({ button: "right", position: { x: 36, y: 12 } }); await page.waitForTimeout(800); }
  if (scene === "inspector") { await page.locator(".thread-inspector-button").click(); await page.waitForTimeout(1000); }

  const client = await page.context().newCDPSession(page);
  await client.send("DOM.enable");
  await client.send("CSS.enable");
  await client.send("Runtime.enable");

  const candidates = await page.evaluate(() => {
    const selector = "button, a[href], [role=button], [role=tab], [role=menuitem], [role=option], [role=switch], input, textarea, select, summary, .starter-card, .compact-thread-main";
    const seen = new Set();
    const out = [];
    for (const el of document.querySelectorAll(selector)) {
      const rect = el.getBoundingClientRect();
      if (rect.width < 4 || rect.height < 4 || rect.bottom < 0 || rect.top > innerHeight || getComputedStyle(el).visibility === "hidden") continue;
      if (el.closest("[inert]")) continue;
      const signature = `${el.tagName}.${String(el.className).split(/\s+/).filter(Boolean).slice(0, 3).join(".")}|${el.getAttribute("role") || ""}`;
      if (seen.has(signature)) continue;
      seen.add(signature);
      el.setAttribute("data-audit", String(out.length));
      out.push({ index: out.length, signature, label: (el.getAttribute("aria-label") || el.title || el.textContent || "").trim().slice(0, 28) });
    }
    return out;
  });

  const doc = await client.send("DOM.getDocument", { depth: 0 });
  const rows = [];
  for (const candidate of candidates) {
    const { nodeId } = await client.send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: `[data-audit="${candidate.index}"]` });
    if (!nodeId) continue;
    const read = async () => {
      const { computedStyle } = await client.send("CSS.getComputedStyleForNode", { nodeId });
      const map = Object.fromEntries(computedStyle.map((entry) => [entry.name, entry.value]));
      return map;
    };
    await client.send("CSS.forcePseudoState", { nodeId, forcedPseudoClasses: [] });
    const rest = await read();
    for (const state of states) {
      await client.send("CSS.forcePseudoState", { nodeId, forcedPseudoClasses: FORCE[state] });
      const after = await read();
      await client.send("CSS.forcePseudoState", { nodeId, forcedPseudoClasses: [] });
      const changed = PROPS.filter((prop) => rest[prop] !== after[prop] && !(prop === "outline-width" && after["outline-style"] === "none"));
      if (!changed.length) continue;
      // A transition on the state's own style carries it (the new style's transition list is what runs).
      const properties = (after["transition-property"] || "").split(",").map((value) => value.trim());
      const durations = (after["transition-duration"] || "").split(",").map((value) => parseFloat(value) * (value.includes("ms") ? 1 : 1000));
      const covered = (prop) => properties.some((name, index) => (name === "all" || name === prop || (name === "background" && prop.startsWith("background")) || (name === "border-color" && prop.startsWith("border")) || (name === "outline" && prop.startsWith("outline")) || (name === "text-decoration" && prop.startsWith("text-decoration")) || (name === "padding" && prop.startsWith("padding")) || (name === "margin" && prop.startsWith("margin"))) && (durations[index % durations.length] || 0) >= 40);
      const bare = changed.filter((prop) => !covered(prop));
      if (bare.length) rows.push({ state, signature: candidate.signature, label: candidate.label, bare, transition: `${after["transition-property"]} ${after["transition-duration"]}` });
    }
  }
  console.log(`scene ${scene}, theme ${theme}: ${candidates.length} control kinds checked, ${rows.length} state changes without a transition`);
  for (const row of rows.slice(0, 80)) console.log(`  ${row.state.padEnd(6)} ${row.signature.slice(0, 64).padEnd(64)} "${row.label}"  flips: ${row.bare.join(", ")}   [${row.transition.slice(0, 50)}]`);
} finally { await browser.close(); }
