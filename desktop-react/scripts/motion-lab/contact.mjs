// Motion lab: a contact sheet of the frames screencast.mjs recorded.
//   node contact.mjs <frameDir> <out.png> <fromMs> <toMs> [cols=6] [crop=x,y,w,h] [scale=0.5] [maxFrames=24]
// Picks up to maxFrames frames evenly from [fromMs, toMs], crops each to the region of interest and lays them out in a grid
// with their timestamps, so a motion can be read in one picture.
import fs from "node:fs";
import { launch } from "./launch.mjs";
const [dir, out, fromMs, toMs, cols = "6", crop = "", scale = "0.5", maxFrames = "24"] = process.argv.slice(2);
const files = fs.readdirSync(dir).filter((name) => /^frame-\d+\.jpg$/.test(name)).map((name) => ({ name, t: Number(name.slice(6, 12)) }))
  .filter((file) => file.t >= Number(fromMs) && file.t <= Number(toMs)).sort((a, b) => a.t - b.t);
const picked = [];
const wanted = Math.min(Number(maxFrames), files.length);
for (let index = 0; index < wanted; index += 1) picked.push(files[Math.round((index * (files.length - 1)) / Math.max(1, wanted - 1))]);
const [cx, cy, cw, ch] = crop ? crop.split(",").map(Number) : [0, 0, 0, 0];
const s = Number(scale);
const cells = picked.map((file) => {
  const data = fs.readFileSync(`${dir}/${file.name}`).toString("base64");
  const style = crop ? `width:${cw * s}px;height:${ch * s}px;background:url(data:image/jpeg;base64,${data}) -${cx * s}px -${cy * s}px / auto auto no-repeat;background-size:calc(var(--w) * ${s}px) auto;` : "";
  return `<div class=c><div class=img data-src="data:image/jpeg;base64,${data}"></div><span>${file.t}ms</span></div>`;
});
const browser = await launch();
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.setContent(`<style>body{margin:0;background:#222;font:11px sans-serif;color:#ccc}.g{display:grid;grid-template-columns:repeat(${cols},auto);gap:4px;padding:4px;width:max-content}.c{position:relative;overflow:hidden;background:#000}.c span{position:absolute;left:3px;top:2px;background:#000a;padding:0 3px}.c canvas{display:block}</style><div class=g>${cells.join("")}</div>`);
  await page.evaluate(async ({ crop, s }) => {
    for (const el of document.querySelectorAll(".img")) {
      const image = new Image();
      image.src = el.dataset.src;
      await image.decode();
      const [x, y, w, h] = crop ? crop : [0, 0, image.naturalWidth, image.naturalHeight];
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(w * s); canvas.height = Math.round(h * s);
      canvas.getContext("2d").drawImage(image, x, y, w, h, 0, 0, canvas.width, canvas.height);
      el.replaceWith(canvas);
    }
  }, { crop: crop ? [cx, cy, cw, ch] : null, s });
  await page.locator(".g").screenshot({ path: out });
  console.log(`${picked.length} frames, ${picked[0]?.t}-${picked.at(-1)?.t}ms -> ${out}`);
} finally { await browser.close(); }
