// Filmstrips of REAL controls in the live app fixture (colours, sizes, clipping and
// neighbouring transitions included): hover the control, pause everything under it,
// seek, photograph a padded box around it.
//   node scripts/motion-lab/icon-film-live.mjs --target "button[aria-label='复制消息']" [--reveal ".compact-thread-row"] [--click "sel||sel"] [--times ...] [--zoom 3] [--out file.png]
//        [--theme dark] [--zoom 3] [--click "sel||sel"] [--reveal "row selector"] [--mode in|out|both] [--out file.png]
// Several targets: separate with "##".
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const targets = (args.target || "").split("##").filter(Boolean);
const times = (args.times || "0,60,120,200,300,420,560").split(",").map(Number);
const pad = Number(args.pad || 6);
const zoom = Number(args.zoom || 3);
const theme = args.theme || "dark";
const mode = args.mode || "both";
const out = args.out || path.join(os.tmpdir(), "loom-motion-lab", "icon-film-live.png");
fs.mkdirSync(path.dirname(out), { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1.5 });
const page = await ctx.newPage();
await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${theme}&lang=zh-CN${args.suffix || ""}`);
await page.waitForSelector(".app-shell .composer", { timeout: 90000 });
await page.waitForTimeout(1500);
for (const sel of (args.click || "").split("||").filter(Boolean)) { await page.locator(sel).first().click({ force: true }); await page.waitForTimeout(900); }

const rows = [];
async function shoot(loc, label, host) {
  const frames = [];
  for (const t of times) {
    await page.evaluate((t) => { for (const a of window.__filmAnims) { a.pause(); a.currentTime = t; } }, t);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const box = await loc.boundingBox();
    const buf = await page.screenshot({ type: "png", clip: { x: box.x - pad, y: box.y - pad, width: box.width + pad * 2, height: box.height + pad * 2 } });
    frames.push({ t, data: buf.toString("base64"), w: box.width + pad * 2, h: box.height + pad * 2 });
  }
  rows.push({ label, frames });
}

for (const sel of targets) {
  const loc = page.locator(sel).first();
  await page.evaluate(() => document.querySelectorAll("[data-film-target]").forEach((n) => n.removeAttribute("data-film-target")));
  await loc.evaluate((el) => el.setAttribute("data-film-target", "1")).catch(() => {});
  if (args.reveal) await page.locator(args.reveal).first().hover().catch(() => {});
  await page.waitForTimeout(300);
  const box = await loc.boundingBox();
  if (!box) { console.log("not visible:", sel); continue; }
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  const leave = async () => { await page.mouse.move(2, 850); await page.waitForTimeout(args.reveal ? 120 : 700); if (args.reveal) { await page.locator(args.reveal).first().hover().catch(() => {}); await page.waitForTimeout(500); } };
  await leave(); await page.waitForTimeout(600);
  if (mode === "in" || mode === "both") {
    await page.evaluate(() => { window.__filmAnims = []; });
    await page.mouse.move(cx, cy);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    await page.evaluate((sel) => { window.__filmAnims = document.querySelector("[data-film-target]").getAnimations({ subtree: true }); }, sel);
    const n = await page.evaluate(() => window.__filmAnims.length);
    await shoot(loc, `${sel.slice(0, 50)} · in (${n} animations)`, sel);
    await page.evaluate(() => window.__filmAnims.forEach((a) => a.play()));
    await page.waitForTimeout(900);
  }
  if (mode === "out" || mode === "both") {
    if (mode === "out") { await page.mouse.move(cx, cy); await page.waitForTimeout(900); }
    await page.evaluate(() => { window.__filmAnims = []; });
    await leave();
    await page.evaluate((sel) => { window.__filmAnims = document.querySelector("[data-film-target]").getAnimations({ subtree: true }); }, sel);
    await shoot(loc, `${sel.slice(0, 50)} · out`, sel);
    await page.evaluate(() => window.__filmAnims.forEach((a) => a.play()));
    await page.waitForTimeout(500);
  }
}

const sheet = await ctx.newPage();
const html = `<body style="margin:0;padding:12px;background:#08090c;color:#9ba0b0;font:11px system-ui">` + rows.map((row) => `<div style="margin:0 0 10px"><div style="margin:0 0 4px">${row.label}</div><div style="display:flex;gap:6px">${
  row.frames.map((f) => `<div style="text-align:center"><img style="width:${f.w * zoom}px;height:${f.h * zoom}px;display:block;background:#101115" src="data:image/png;base64,${f.data}"><span style="font-size:10px;opacity:.7">${f.t}ms</span></div>`).join("")
}</div></div>`).join("") + "</body>";
await sheet.setContent(html);
const maxW = Math.max(...rows.map((r) => r.frames.reduce((s, f) => s + f.w * zoom + 6, 0))) + 30;
await sheet.setViewportSize({ width: Math.ceil(maxW), height: 200 });
await sheet.waitForTimeout(250);
await sheet.screenshot({ path: out, fullPage: true });
console.log(out);
await browser.close();
