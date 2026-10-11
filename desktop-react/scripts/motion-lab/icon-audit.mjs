// Hover audit across scenes: reach a state of the real app, then hover every icon
// control inside a scope and report what moves and what still interferes.
//   node scripts/motion-lab/icon-audit.mjs --scene home|settings|settings:<page index>|model|permission|menu|inspector|account|project
//        [--theme dark] [--suffix "&scenario=approval"] [--json out.json]
import fs from "node:fs";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = args.origin || process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const theme = args.theme || "dark";
const scene = args.scene || "home";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1.5 });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${theme}&lang=zh-CN${args.suffix || ""}`);
await page.waitForSelector(".app-shell .composer", { timeout: 90000 });
await page.waitForTimeout(1500);

async function click(sel, opts = {}) { await page.locator(sel).first().click({ timeout: 8000, ...opts }); await page.waitForTimeout(opts.wait ?? 700); }

let scope = "body";
const [name, arg] = scene.split(":");
if (name === "settings") {
  await click(".thread-settings-button", { wait: 1200 });
  await page.waitForSelector(".settings-host", { timeout: 8000 });
  scope = ".settings-host";
  if (arg) {
    const idx = Number(arg);
    const buttons = page.locator(".settings-nav button");
    const n = await buttons.count();
    if (!Number.isNaN(idx) && idx < n) { await buttons.nth(idx).click(); await page.waitForTimeout(900); console.log(`settings page #${idx} of ${n}: ${(await buttons.nth(idx).innerText()).trim()}`); }
  }
} else if (name === "model") { await click(".model-chip", { wait: 1000 }); scope = ".composer-popover"; }
else if (name === "permission") { await click(".permission-chip", { wait: 1000 }); scope = ".composer-popover"; }
else if (name === "menu") {
  const row = page.locator(".thread-quick-actions").first();
  await page.locator(".compact-thread, .project-thread-list li, .thread-row").first().hover().catch(() => {});
  await page.locator('button[aria-label="更多操作"]').first().click({ timeout: 8000, force: true });
  await page.waitForTimeout(700); scope = ".thread-context-menu";
} else if (name === "inspector") { await click(".thread-inspector-button", { wait: 1200 }); scope = ".inspector, .workspace-panels"; }
else if (name === "account") { await click(".thread-account-button", { wait: 1200 }); scope = ".loom-account-dialog, .loom-account-backdrop"; }
else if (name === "project") { await click(".project-group-main", { wait: 1500 }); scope = ".project-details-panel, .workspace-panels, main"; }
else if (name === "home") { scope = "body"; }

const SEL = 'button, [role="button"], [role="menuitem"], a[href], summary';
const count = await page.evaluate(({ SEL, scope }) => {
  document.querySelectorAll("[data-audit-id]").forEach((n) => n.removeAttribute("data-audit-id"));
  let i = 0;
  const roots = [...document.querySelectorAll(scope)];
  for (const el of document.querySelectorAll(SEL)) {
    if (!roots.some((r) => r.contains(el))) continue;
    if (el.querySelector("svg")) el.setAttribute("data-audit-id", String(i++));
  }
  return i;
}, { SEL, scope });

const probe = `(id) => {
  const el = document.querySelector('[data-audit-id="' + id + '"]');
  const root = el.getBoundingClientRect();
  const hs = getComputedStyle(el);
  const parts = [], roots = [];
  for (const svg of el.querySelectorAll("svg")) {
    const cs = getComputedStyle(svg);
    roots.push({ icon: svg.getAttribute("data-icon") || ("svg." + (svg.getAttribute("class") || "").replace(/\\s+/g, ".").slice(0, 36)),
      transform: cs.transform, translate: cs.translate, rotate: cs.rotate, filter: cs.filter, anim: cs.animationName, tprop: cs.transitionProperty, own: svg.hasAttribute("data-icon") });
    for (const n of [svg, ...svg.querySelectorAll("*")].slice(0, 60)) { const r = n.getBoundingClientRect(); parts.push({ x: r.x - root.x, y: r.y - root.y, w: r.width, h: r.height }); }
  }
  return { host: { transform: hs.transform, translate: hs.translate }, roots, parts };
}`;
const info = `(id) => {
  const el = document.querySelector('[data-audit-id="' + id + '"]');
  const r = el.getBoundingClientRect();
  let hidden = r.width === 0 || r.height === 0 || r.bottom < 0 || r.top > innerHeight;
  for (let p = el; p && !hidden; p = p.parentElement) { const s = getComputedStyle(p); if (s.visibility === "hidden" || s.display === "none" || s.opacity === "0") hidden = true; }
  return { label: el.getAttribute("aria-label") || el.getAttribute("title") || (el.textContent || "").trim().slice(0, 14) || String(el.className).slice(0, 24),
    cx: r.x + r.width / 2, cy: r.y + r.height / 2, hidden, disabled: el.disabled === true || el.getAttribute("aria-disabled") === "true" };
}`;

const rows = [];
for (let id = 0; id < count; id++) {
  await page.mouse.move(2, 850); await page.waitForTimeout(30);
  const meta = await page.evaluate(`(${info})(${id})`);
  if (meta.hidden || meta.disabled) { rows.push({ id, ...meta, skipped: true }); continue; }
  await page.waitForTimeout(450);
  const rest = await page.evaluate(`(${probe})(${id})`);
  await page.mouse.move(meta.cx, meta.cy, { steps: 2 });
  await page.waitForTimeout(950);
  const on = await page.evaluate(`(${probe})(${id})`);
  let moved = 0, maxMove = 0;
  if (rest.parts.length === on.parts.length) rest.parts.forEach((a, i) => { const b = on.parts[i]; const m = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y), Math.abs(b.w - a.w), Math.abs(b.h - a.h)); if (m > 0.04) moved++; maxMove = Math.max(maxMove, m); });
  const leftovers = [];
  if (on.host.transform !== "none") leftovers.push("host transform " + on.host.transform.slice(0, 36));
  if (on.host.translate !== "none") leftovers.push("host translate " + on.host.translate);
  for (const r of on.roots) {
    if (r.transform !== "none" && r.anim === "none") leftovers.push(`${r.icon} root transform ${r.transform.slice(0, 36)}`);
    if (r.translate !== "none") leftovers.push(`${r.icon} root translate ${r.translate}`);
    if (r.rotate !== "none" && r.anim === "none") leftovers.push(`${r.icon} root rotate ${r.rotate}`);
    if (r.filter !== "none") leftovers.push(`${r.icon} root filter ${r.filter.slice(0, 30)}`);
    if (/filter/.test(r.tprop)) leftovers.push(`${r.icon} transitions filter`);
  }
  rows.push({ id, ...meta, icons: on.roots.map((r) => r.icon), moved, maxMove: +maxMove.toFixed(2), leftovers });
}
await page.mouse.move(2, 850);
await browser.close();
if (args.json) fs.writeFileSync(args.json, JSON.stringify(rows, null, 1));
const done = rows.filter((r) => !r.skipped);
console.log(`scene ${scene}: controls with svg ${count}; measured ${done.length}; skipped ${rows.length - done.length}${errors.length ? "; PAGE ERRORS: " + errors.slice(0, 3).join(" | ") : ""}`);
for (const r of done) {
  if (args.quiet && !r.leftovers.length) continue;
  console.log(`${String(r.id).padStart(3)} ${String(r.label).slice(0, 20).padEnd(20)} | ${r.icons.join(" + ").slice(0, 44).padEnd(44)} | ${String(r.moved).padStart(3)} | ${String(r.maxMove).padStart(6)} | ${r.leftovers.join("; ").slice(0, 110)}`);
}
