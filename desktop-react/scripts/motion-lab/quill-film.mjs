// Frame-exact films of the new-conversation row's quill on the real sidebar (the live-app fixture), at device pixels and
// enlarged without smoothing: what is drawn at 20 px is what is judged.
//   node scripts/motion-lab/quill-film.mjs [--scene in|out|press|click|shortcut] [--theme dark|light] [--zoom 5] [--dsf 1.5]
//        [--times 0,60,120,...] [--pad 5] [--fixed 1] [--probe 700] [--reduced 1] [--out file.png]
//
//   in        the pointer arrives: the quill takes its pose about the nib (the barb a beat later).
//   out       the pointer leaves: back to rest.
//   press     the button is held: the glyph gives toward its nib.
//   click     a click: the line is pulled back into the nib and written again, the barb struck last (data-written).
//   shortcut  the same start without the pointer (Ctrl+N, sent to the sidebar as the event the app listens for).
//
// Every animation and transition under the row is paused and seeked, so frames are exact and not timed. `--fixed 1` shoots
// the same box in every frame (the default follows the glyph, which shrinks under a press). `--probe <ms>` also prints, every
// 20 ms, the turn of the quill and the barb and the length of the line (1 = whole) from the computed styles.
import fs from "node:fs";
import path from "node:path";
import { launch } from "./launch.mjs";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const scene = args.scene || "click";
const theme = args.theme || "dark";
const zoom = Number(args.zoom || 5);
const pad = Number(args.pad || 5);
const times = (args.times || (scene === "press" ? "0,30,60,90" : scene === "in" || scene === "out" ? "0,40,80,120,180,260,360,520" : "0,60,100,140,200,300,420,540,640")).split(",").map(Number);
const out = args.out || path.join(process.env.TEMP || "/tmp", "loom-motion-lab", `quill-${scene}.png`);
fs.mkdirSync(path.dirname(out), { recursive: true });
const ROW = ".sidebar-new-conversation";

const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: Number(args.dsf || 1.5), reducedMotion: args.reduced ? "reduce" : "no-preference" });
const page = await ctx.newPage();
page.on("pageerror", (error) => console.error("pageerror:", error.message));
await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${theme}&lang=zh-CN`);
await page.waitForSelector(".app-shell .composer", { timeout: 90000 });
await page.waitForTimeout(1500);

const row = page.locator(ROW).first();
const glyph = page.locator(`${ROW} > svg`).first();
const box = await row.boundingBox();
const fixed = await glyph.boundingBox();
const [cx, cy] = [box.x + box.width / 2, box.y + box.height / 2];
const frame = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
const settle = async () => { await page.mouse.move(2, 850); await page.waitForTimeout(800); };

/** Pause what is running under the row and say what it is. */
const hold = () => page.evaluate((row) => {
  window.__film = document.querySelector(row).getAnimations({ subtree: true });
  return window.__film.map((a) => `${a.animationName || a.transitionProperty}:${Math.round(a.effect.getComputedTiming().duration)}`).join(" ");
}, ROW);

await settle();
if (scene === "in") {
  await page.mouse.move(cx, cy);
} else if (scene === "out") {
  await page.mouse.move(cx, cy);
  await page.waitForTimeout(900);
  await page.mouse.move(2, 850);
} else if (scene === "press") {
  await page.mouse.move(cx, cy);
  await page.waitForTimeout(900);
  await page.mouse.down();
} else if (scene === "click") {
  await page.mouse.move(cx, cy);
  await page.waitForTimeout(900);
  await page.mouse.down();
  await page.waitForTimeout(160);
  await page.mouse.up();
} else if (scene === "shortcut") {
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "n", ctrlKey: true, bubbles: true, cancelable: true })));
} else {
  throw new Error(`unknown scene ${scene}`);
}
await frame();
console.log(`${scene}:`, await hold());

const frames = [];
for (const t of times) {
  await page.evaluate((t) => window.__film.forEach((a) => { a.pause(); a.currentTime = t; }), t);
  await frame();
  const b = args.fixed ? fixed : await glyph.boundingBox();
  const shot = await page.screenshot({ type: "png", clip: { x: b.x - pad, y: b.y - pad, width: b.width + pad * 2, height: b.height + pad * 2 } });
  frames.push({ t, data: shot.toString("base64"), w: b.width + pad * 2, h: b.height + pad * 2 });
}

if (args.probe) {
  const turn = (transform) => { const m = /matrix\(([^)]+)\)/.exec(transform); if (!m) return 0; const v = m[1].split(",").map(Number); return (Math.atan2(v[1], v[0]) * 180) / Math.PI; };
  for (let t = 0; t <= Number(args.probe); t += 20) {
    const r = await page.evaluate((t) => {
      window.__film.forEach((a) => { a.pause(); a.currentTime = t; });
      const style = (sel) => getComputedStyle(document.querySelector(`.sidebar-new-conversation ${sel}`));
      return { quill: style(".li-quill").transform, barb: style(".li-barb").transform, line: style(".li-outline").strokeDasharray, tip: style(".li-barb").strokeDasharray, glyph: getComputedStyle(document.querySelector(".sidebar-new-conversation > svg")).scale };
    }, t);
    const length = (dash) => (dash === "none" ? 1 : parseFloat(dash));
    console.log(`${String(t).padStart(4)} ms  quill ${turn(r.quill).toFixed(2).padStart(6)}°  barb ${turn(r.barb).toFixed(2).padStart(6)}°  line ${length(r.line).toFixed(3)}  barb-line ${length(r.tip).toFixed(3)}  scale ${r.glyph}`);
  }
}

const sheet = await ctx.newPage();
await sheet.setContent(`<body style="margin:0;padding:10px;background:#08090c;color:#9ba0b0;font:11px system-ui"><div style="margin:0 0 4px">${scene} · ${theme} · dsf ${args.dsf || 1.5}${args.reduced ? " · reduced motion" : ""}</div><div style="display:flex;gap:4px;flex-wrap:wrap">${
  frames.map((f) => `<div style="text-align:center"><img style="width:${f.w * zoom}px;height:${f.h * zoom}px;display:block;background:#101115;image-rendering:pixelated" src="data:image/png;base64,${f.data}"><span style="font-size:10px;opacity:.7">${f.t}ms</span></div>`).join("")
}</div></body>`);
await sheet.setViewportSize({ width: 1500, height: 200 });
await sheet.waitForTimeout(200);
await sheet.screenshot({ path: out, fullPage: true });
console.log(out);
await browser.close();
