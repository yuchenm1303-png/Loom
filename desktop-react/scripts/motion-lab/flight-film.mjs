// Frame-exact pictures of the send flight (src/sendFlight.ts): its animations are paused and seeked, so a frame is the
// state at that time instead of whatever a busy machine got round to drawing.
//   node scripts/motion-lab/flight-film.mjs [--scene closeup,handoff,strip] [--theme dark|light] [--empty 1] [--height 860]
//        [--text "..."] [--times 0,30,60,...] [--zoom 1.4] [--out file.png]
//
//   closeup  each frame cropped around the message and enlarged: the card coming up around the text, the text's own
//            crispness, where the capsule is. (default)
//   handoff  the ghost at its last moment against the real bubble it hands over to, enlarged, with a difference image
//            (x6) and the numbers: what the reader would see change at the moment of landing.
//   strip    the lower part of the page at each time, small: the whole choreography in one picture.
//   (the first frame is the field's own text under the ghost: compare it with the field before Enter with lift-off.)
import fs from "node:fs";
import path from "node:path";
import { launch } from "./launch.mjs";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const scenes = (args.scene || "closeup,handoff").split(",");
const height = Number(args.height || 860);
const zoom = Number(args.zoom || 1.4);
const text = args.text || "Add email format validation to the login page and cover it with a unit test.";
const out = args.out || path.join(process.env.TEMP || "/tmp", "loom-motion-lab", "flight-film.png");
fs.mkdirSync(path.dirname(out), { recursive: true });
const browser = await launch();

/** A fresh page with the message typed and the flight's long timers held (its safety net must not land a paused film). */
async function typed() {
  const ctx = await browser.newContext({ viewport: { width: 1360, height }, deviceScaleFactor: 1.5 });
  const page = await ctx.newPage();
  await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${args.theme || "dark"}&lang=en&latency=3000${args.empty === "1" ? "&empty=1" : ""}`);
  await page.waitForSelector(".app-shell .composer textarea:not([disabled])", { timeout: 90000 });
  await page.waitForTimeout(1500);
  await page.locator(".composer textarea").click();
  await page.keyboard.type(text);
  await page.waitForTimeout(500);
  await page.addStyleTag({ content: ".composer textarea { caret-color: transparent !important; }" });
  await page.evaluate(() => { window.__setTimeout = window.setTimeout; window.setTimeout = (fn, ms, ...rest) => (ms >= 500 && ms < 5000 ? -1 : window.__setTimeout(fn, ms, ...rest)); });
  return { ctx, page };
}
/** Pause what the flight started; the length of the longest of it. */
const freeze = (page) => page.evaluate(() => {
  const mine = document.getAnimations().filter((animation) => animation.effect?.target?.closest?.(".send-flight-layer"));
  mine.forEach((animation) => animation.pause());
  window.__held = mine;
  return Math.max(...mine.map((animation) => Number(animation.effect.getComputedTiming().endTime) || 0));
});
const seek = async (page, time) => {
  await page.evaluate((time) => window.__held.forEach((animation) => { animation.currentTime = time; }), time);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
};
const ghostBox = (page) => page.evaluate(() => { const r = document.querySelector(".send-flight-text .user-message").getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });

const { ctx, page } = await typed();
await page.keyboard.press("Enter");
const duration = await freeze(page);
console.log("flight duration", duration, "ms");
// At the very end the animations finish and the flight lands: stop two milliseconds short.
const times = (args.times || "0,30,60,100,140,190,250,320,380").split(",").map(Number).filter((time) => time < duration - 2).concat(Math.round(duration) - 2);
const pictures = [];
const crop = { x: 340, y: args.empty === "1" ? 60 : 250, width: 900, height: height - (args.empty === "1" ? 60 : 250) };
for (const time of times) {
  await seek(page, time);
  const box = await ghostBox(page);
  const close = { x: Math.max(0, box.x - 26), y: Math.max(0, box.y - 22), width: Math.min(1360, box.w + 52), height: box.h + 44 };
  pictures.push({
    t: time,
    close: scenes.includes("closeup") ? { data: (await page.screenshot({ clip: close, type: "png" })).toString("base64"), w: close.width, h: close.height } : null,
    strip: scenes.includes("strip") ? { data: (await page.screenshot({ clip: crop, type: "png" })).toString("base64"), w: crop.width, h: crop.height } : null,
  });
}
const last = await ghostBox(page);
await ctx.close();

let handoff = null;
if (scenes.includes("handoff")) {
  const second = await typed();
  await second.page.keyboard.press("Enter");
  await freeze(second.page);
  await second.page.evaluate(() => window.__held.forEach((animation) => { animation.currentTime = (Number(animation.effect.getComputedTiming().endTime) || 0) - 2; }));
  await second.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const clip = { x: last.x - 6, y: last.y - 6, width: last.w + 12, height: last.h + 12 };
  const ghost = await second.page.screenshot({ clip, type: "png" });
  await second.page.evaluate(() => { window.setTimeout = window.__setTimeout; window.__held.forEach((animation) => animation.play()); });
  await second.page.waitForFunction(() => !document.querySelector(".send-flight-ghost"), null, { timeout: 5000 });
  await second.page.waitForTimeout(1300);
  handoff = { ghost: ghost.toString("base64"), real: (await second.page.screenshot({ clip, type: "png" })).toString("base64") };
  await second.ctx.close();
}

const sheet = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await sheet.setContent(`<body style="margin:0;background:#0c0d10;color:#bbb;font:11px sans-serif;padding:8px">
  <div id=close></div><div id=strip style="display:flex;flex-wrap:wrap;gap:6px"></div>
  <div id=hand style="display:none"><div style="padding:6px">hand-off: the ghost at its last moment | the real bubble | |difference| x6</div><div style="display:flex;gap:6px"><canvas id=a></canvas><canvas id=b></canvas><canvas id=d></canvas></div><pre id=r></pre></div><style>canvas{width:480px;height:auto;image-rendering:pixelated}</style></body>`);
const report = await sheet.evaluate(async ({ pictures, handoff, zoom }) => {
  const load = (data) => new Promise((resolve) => { const image = new Image(); image.onload = () => resolve(image); image.src = "data:image/png;base64," + data; });
  for (const picture of pictures) {
    if (picture.close) { const image = await load(picture.close.data); document.getElementById("close").insertAdjacentHTML("beforeend", `<div style="margin:0 0 6px"><span style="display:inline-block;width:52px">${picture.t} ms</span><img style="width:${picture.close.w * zoom}px;height:${picture.close.h * zoom}px;vertical-align:middle" src="${image.src}"></div>`); }
    if (picture.strip) { const image = await load(picture.strip.data); document.getElementById("strip").insertAdjacentHTML("beforeend", `<div style="position:relative"><img style="width:${picture.strip.w * 0.5}px" src="${image.src}"><span style="position:absolute;left:4px;top:2px;background:#000a;padding:0 3px">${picture.t} ms</span></div>`); }
  }
  if (!handoff) return null;
  document.getElementById("hand").style.display = "block";
  const [ghost, real] = await Promise.all([load(handoff.ghost), load(handoff.real)]);
  const scale = 3;
  const draw = (id, image) => { const c = document.getElementById(id); c.width = image.width * scale; c.height = image.height * scale; const x = c.getContext("2d"); x.imageSmoothingEnabled = false; x.drawImage(image, 0, 0, c.width, c.height); };
  draw("a", ghost); draw("b", real);
  const pixels = (image) => { const c = document.createElement("canvas"); c.width = image.width; c.height = image.height; const x = c.getContext("2d"); x.drawImage(image, 0, 0); return x.getImageData(0, 0, image.width, image.height); };
  const pa = pixels(ghost), pb = pixels(real);
  const d = document.getElementById("d"); d.width = ghost.width * scale; d.height = ghost.height * scale;
  const diff = new ImageData(ghost.width, ghost.height);
  let sum = 0, strong = 0, max = 0;
  for (let i = 0; i < pa.data.length; i += 4) {
    const delta = Math.abs(pa.data[i] - pb.data[i]) + Math.abs(pa.data[i + 1] - pb.data[i + 1]) + Math.abs(pa.data[i + 2] - pb.data[i + 2]);
    sum += delta; if (delta > 45) strong += 1; max = Math.max(max, delta);
    const v = Math.min(255, delta * 6); diff.data[i] = v; diff.data[i + 1] = v; diff.data[i + 2] = v; diff.data[i + 3] = 255;
  }
  const tmp = document.createElement("canvas"); tmp.width = ghost.width; tmp.height = ghost.height; tmp.getContext("2d").putImageData(diff, 0, 0);
  const dx = d.getContext("2d"); dx.imageSmoothingEnabled = false; dx.drawImage(tmp, 0, 0, d.width, d.height);
  const result = { size: [ghost.width, ghost.height], meanAbsDiff: +(sum / (pa.data.length / 4)).toFixed(3), pixelsOverThreshold: strong, maxDiff: max };
  document.getElementById("r").textContent = JSON.stringify(result);
  return result;
}, { pictures, handoff, zoom });
if (report) console.log("hand-off difference (of 765 per pixel):", JSON.stringify(report));
await sheet.screenshot({ path: out, fullPage: true });
console.log(out);
await browser.close();
