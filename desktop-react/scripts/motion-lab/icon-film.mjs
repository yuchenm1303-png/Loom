// Deterministic filmstrips of one glyph's gesture: hover the live cell, pause every
// transition/animation in it, seek to each time, photograph the cell.
//   node scripts/motion-lab/icon-film.mjs --icon trash,plus [--times 0,50,100,200,340] [--size 56] [--dsf 3] [--zoom 1] [--mode in|out|both] [--out file.png]
//        [--theme dark] [--mode both|in|out] [--state hover|down|open] [--out path.png]
// Several icons: --icon trash,copy,plus (one strip row each).
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const icons = (args.icon || "trash").split(",");
const times = (args.times || "0,50,100,150,200,260,340,440").split(",").map(Number);
const size = Number(args.size || 56);
const dsf = Number(args.dsf || 3);
const theme = args.theme || "dark";
const mode = args.mode || "both";
const zoom = Number(args.zoom || 1);
const out = args.out || path.join(os.tmpdir(), "loom-motion-lab", `icon-film-${icons.join("-")}.png`);
fs.mkdirSync(path.dirname(out), { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const ctx = await browser.newContext({ viewport: { width: 900, height: 700 }, deviceScaleFactor: dsf });
const page = await ctx.newPage();
await page.goto(`${origin}/scripts/fixtures/icon-lab.html?theme=${theme}&only=${icons.join(",")}&size=${size}`);
await page.waitForSelector("[data-lab-cell]", { timeout: 60000 });
await page.waitForTimeout(500);

async function seekAndShoot(cell, label, rows) {
  const frames = [];
  for (const t of times) {
    await page.evaluate(({ t }) => {
      for (const a of window.__filmAnims) { a.pause(); a.currentTime = t; }
    }, { t });
    // one frame so the paused state paints
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const buf = await cell.screenshot({ type: "png" });
    frames.push({ t, data: buf.toString("base64") });
  }
  rows.push({ label, frames });
}

async function capture(name, kind) {
  const cell = page.locator(`[data-lab-row="${name}"] [data-lab-cell="live"]`);
  const box = await cell.boundingBox();
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  await page.mouse.move(5, 690);
  await page.waitForTimeout(700);
  const rows = [];
  if (kind === "in" || kind === "both") {
    await page.evaluate(() => { window.__filmAnims = []; });
    await page.mouse.move(cx, cy);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    await page.evaluate((selector) => {
      const cellEl = document.querySelector(selector);
      window.__filmAnims = cellEl.getAnimations({ subtree: true });
    }, `[data-lab-row="${name}"] [data-lab-cell="live"]`);
    const n = await page.evaluate(() => window.__filmAnims.length);
    await seekAndShoot(cell, `${name} · in (${n} animations)`, rows);
    // let it finish for real
    await page.evaluate(() => window.__filmAnims.forEach((a) => a.play()));
    await page.waitForTimeout(900);
  }
  if (kind === "out" || kind === "both") {
    if (kind === "out") { await page.mouse.move(cx, cy); await page.waitForTimeout(900); }
    await page.evaluate(() => { window.__filmAnims = []; });
    await page.mouse.move(5, 690);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    await page.evaluate((selector) => {
      window.__filmAnims = document.querySelector(selector).getAnimations({ subtree: true });
    }, `[data-lab-row="${name}"] [data-lab-cell="live"]`);
    const n = await page.evaluate(() => window.__filmAnims.length);
    await seekAndShoot(cell, `${name} · out (${n})`, rows);
    await page.evaluate(() => window.__filmAnims.forEach((a) => a.play()));
    await page.waitForTimeout(500);
  }
  return rows;
}

const allRows = [];
for (const name of icons) allRows.push(...(await capture(name, mode)));

// Compose the strip in a second page.
const sheet = await ctx.newPage();
const cellPx = Math.round((size + 18) * zoom);
const html = `<body style="margin:0;padding:14px;background:${theme === "dark" ? "#0b0c10" : "#eeeef2"};color:${theme === "dark" ? "#9ba0b0" : "#555"};font:11px system-ui">` +
  allRows.map((row) => `<div style="margin:0 0 10px"><div style="margin:0 0 4px">${row.label}</div><div style="display:flex;gap:6px">${
    row.frames.map((f) => `<div style="text-align:center"><img style="width:${cellPx}px;height:${cellPx}px;display:block;image-rendering:${zoom > 1 ? "pixelated" : "auto"}" src="data:image/png;base64,${f.data}"><span style="font-size:10px;opacity:.7">${f.t}ms</span></div>`).join("")
  }</div></div>`).join("") + "</body>";
await sheet.setContent(html);
await sheet.setViewportSize({ width: Math.ceil(times.length * (cellPx + 6) + 30), height: allRows.length * (cellPx + 44) + 30 });
await sheet.waitForTimeout(200);
await sheet.screenshot({ path: out, fullPage: true });
console.log(out);
await browser.close();
