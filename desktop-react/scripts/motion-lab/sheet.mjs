// Build a contact sheet from saved frames.
//   node sheet.mjs <frameDir> <out.png> <frames comma list> <x,y,w,h crop> [cols=2] [scale=1]
import fs from "node:fs";
import path from "node:path";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const [dir, out, list, crop, cols = "2", scale = "1"] = process.argv.slice(2);
const [x, y, w, h] = crop.split(",").map(Number);
const frames = list.split(",").map((name) => name.trim());
const s = Number(scale);
const cell = (name) => {
  const file = path.join(dir, `f${name.padStart(3, "0")}.png`);
  const data = fs.readFileSync(file).toString("base64");
  return `<div class="c"><b>${name}</b><div class="i" style="width:${w * s}px;height:${h * s}px;background:url(data:image/png;base64,${data}) -${x * s}px -${y * s}px / auto;"></div></div>`;
};
// background-size auto keeps native pixels; scale via zoom on the wrapper instead.
const html = `<style>body{margin:0;background:#888;font:12px monospace}.g{display:grid;grid-template-columns:repeat(${cols},${w}px);gap:6px;padding:6px}.c{position:relative;background:#fff}.c b{position:absolute;left:4px;top:2px;z-index:2;background:#000a;color:#fff;padding:1px 4px}.i{width:${w}px;height:${h}px;background-repeat:no-repeat}</style><div class="g">${frames.map((name) => {
  const file = path.join(dir, `f${name.padStart(3, "0")}.png`);
  const data = fs.readFileSync(file).toString("base64");
  return `<div class="c"><b>${name}</b><div class="i" style="background-image:url(data:image/png;base64,${data});background-position:-${x}px -${y}px"></div></div>`;
}).join("")}</div>`;
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const rows = Math.ceil(frames.length / Number(cols));
  const page = await browser.newPage({ viewport: { width: Number(cols) * (w + 6) + 6, height: rows * (h + 6) + 6 } });
  await page.setContent(html);
  await page.screenshot({ path: out });
  console.log("sheet", out);
} finally { await browser.close(); }
