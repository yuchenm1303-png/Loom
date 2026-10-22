// The recorder behind spec.mjs and tests/motion-language-browser.mjs.
//
// It watches document.getAnimations() every frame while a step runs and notes each animation or transition it has
// not seen yet with its EFFECTIVE timing (what the browser really runs after the cascade, not what a stylesheet says
// somewhere): which element, which property or keyframes, how long, after what delay, on which curve, and what the
// keyframes move. That is the only honest answer to "what happens when I click this".
//
// The scenarios are the interactions people make most, written against the live-app fixture (the real App over an
// in-memory host), in English.

export const RECORDER = () => {
  const spec = { on: false, step: "", rows: [], seen: new WeakSet() };
  window.__spec = spec;
  const label = (el) => {
    if (!el || el.nodeType !== 1) return "?";
    const cls = String(el.className?.baseVal ?? el.className ?? "").trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".");
    return el.tagName.toLowerCase() + (cls ? "." + cls : "");
  };
  const summarize = (a) => {
    const eff = a.effect;
    if (!eff) return null;
    const timing = eff.getTiming();
    let keys = [];
    try { keys = eff.getKeyframes(); } catch { /* an effect without keyframes */ }
    const first = {}, last = {};
    for (const k of keys) {
      for (const [p, v] of Object.entries(k)) {
        if (["offset", "easing", "composite", "computedOffset"].includes(p)) continue;
        if (!(p in first)) first[p] = String(v).slice(0, 60);
        last[p] = String(v).slice(0, 60);
      }
    }
    const kind = a instanceof CSSTransition ? "transition" : a instanceof CSSAnimation ? "animation" : "script";
    return {
      step: spec.step,
      kind,
      name: kind === "transition" ? a.transitionProperty : kind === "animation" ? a.animationName : (a.id || ""),
      target: label(eff.target) + (eff.pseudoElement ? eff.pseudoElement : ""),
      duration: Math.round((timing.duration || 0) * 10) / 10,
      delay: Math.round((timing.delay || 0) * 10) / 10,
      iterations: timing.iterations === Infinity ? "infinite" : timing.iterations,
      easing: String(timing.easing || "").slice(0, 90),
      first,
      last,
    };
  };
  const tick = () => {
    if (spec.on) {
      for (const a of document.getAnimations()) {
        if (spec.seen.has(a)) continue;
        spec.seen.add(a);
        const row = summarize(a);
        if (row) spec.rows.push(row);
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};

export async function open(chromium, { origin, executablePath, theme = "dark", lang = "en", width = 1440, height = 900, reducedMotion = "no-preference" }) {
  const browser = await chromium.launch({ executablePath, headless: true });
  const page = await browser.newPage({ viewport: { width, height }, reducedMotion });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(RECORDER);
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=${lang}`);
  await page.locator(".thread-header").waitFor({ timeout: 90000 });
  await page.waitForTimeout(1200);
  return { browser, page, errors };
}

export async function runStep(page, name, action, settle = 900) {
  await page.evaluate((n) => { window.__spec.step = n; window.__spec.on = true; }, name);
  let failure = "";
  try { await action(); } catch (error) { failure = String(error.message).split("\n")[0].slice(0, 120); }
  await page.waitForTimeout(settle);
  await page.evaluate(() => { window.__spec.on = false; });
  return failure;
}

export const readRows = (page) => page.evaluate(() => window.__spec.rows);

/** The interactions. Each step is [name, (page) => action, settle ms]. Order matters: some close what the last opened. */
export function scenarios() {
  const tool = (id) => (page) => page.locator(`.loom-tool-button[data-tool-id="${id}"]`).evaluate((el) => el.click());
  const rowOf = (text) => (page) => page.locator(".compact-thread-main", { hasText: text }).first();
  return [
    ["permission-popover-open", (page) => page.locator(".composer button", { hasText: /Full access/ }).first().click(), 700],
    ["permission-popover-close", (page) => page.keyboard.press("Escape"), 600],
    ["model-popover-open", (page) => page.locator(".composer .model-chip").click(), 800],
    ["model-popover-close", (page) => page.keyboard.press("Escape"), 600],
    ["message-menu-open", (page) => page.locator(".assistant-message").first().click({ button: "right" }), 600],
    ["message-menu-close", (page) => page.keyboard.press("Escape"), 600],
    ["process-expand", (page) => page.locator(".turn-process-header").first().click(), 900],
    ["process-collapse", (page) => page.locator(".turn-process-header").first().click(), 900],
    ["sidebar-search-open", (page) => page.locator('button[aria-label="Search conversations"]').first().click(), 600],
    ["sidebar-search-close", (page) => page.keyboard.press("Escape"), 600],
    ["project-unfold", (page) => page.locator(".project-disclosure-button").first().click(), 800],
    ["thread-switch", async (page) => rowOf("Image attachments")(page).click({ position: { x: 36, y: 12 } }), 900],
    ["thread-menu-open", async (page) => rowOf("Light theme audit")(page).click({ button: "right", position: { x: 36, y: 12 } }), 600],
    ["thread-menu-close", (page) => page.keyboard.press("Escape"), 600],
    ["project-fold", (page) => page.locator(".project-disclosure-button").first().click(), 800],
    ["new-conversation", (page) => page.locator("button.sidebar-new-conversation").click(), 900],
    ["settings-open", tool("settings"), 1100],
    ["settings-page", (page) => page.locator(".settings-nav button").nth(2).click(), 900],
    ["settings-page-far", (page) => page.locator(".settings-nav button").nth(6).click(), 900],
    ["settings-close", (page) => page.locator(".settings-back").first().click(), 900],
    ["profile-open", tool("profile"), 1100],
    ["profile-close", (page) => page.locator(".profile-back-button").click(), 900],
    ["background-open", tool("background"), 800],
    ["background-close", tool("background"), 800],
    ["dock-open", tool("inspector"), 900],
    ["dock-close", tool("inspector"), 900],
    ["sidebar-close", (page) => page.locator(".codex-sidebar-topbar .sidebar-toggle").click(), 900],
    ["sidebar-open", (page) => page.locator(".thread-header .sidebar-toggle").click(), 900],
  ];
}

// ---- reading the rows -------------------------------------------------------------------------------------------

const ICON = /^(svg|g|path|rect|circle|line|polyline|polygon|ellipse)(\.|$)/;
const COLOUR = /^(color|background-color|background|border-[a-z]+-color|border-color|box-shadow|outline-color|fill|stroke|text-decoration-color|filter)$/;
const MOVES = /^(transform|translate|scale|rotate|filter)$/;

export function describeRow(row) {
  const moves = Object.keys(row.last).filter((p) => MOVES.test(p)) .concat(row.kind === "transition" && MOVES.test(row.name) ? [row.name] : []);
  return {
    icon: ICON.test(row.target) || /^li-/.test(row.name),
    colour: row.kind === "transition" && COLOUR.test(row.name),
    loop: row.iterations === "infinite" || row.iterations > 1,
    viewTransition: /::view-transition/.test(row.target),
    layoutAnchor: row.name === "loom-layout-anchor",
    pseudo: /::(before|after)$/.test(row.target),
    reversed: Math.abs(row.duration - Math.round(row.duration)) > 0.01,
    moves: [...new Set(moves)],
  };
}

export function interesting(rows) {
  return rows.filter((row) => {
    const d = describeRow(row);
    return !d.icon && !d.colour && !d.loop && !/ambient-glow|sidebar-brand|li-/.test(row.target + " " + row.name);
  });
}
