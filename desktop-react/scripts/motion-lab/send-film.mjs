// Frame-exact films of the send button on the real composer (the live-app fixture), at device pixels and enlarged without
// smoothing: what is drawn at 36 px is what is judged.
//   node scripts/motion-lab/send-film.mjs [--scene swap,gesture,guidance] [--theme light|dark] [--zoom 4] [--dsf 1.5] [--out file.png]
//        [--times 0,20,40,...]   (swap and guidance only)
//
//   swap      a message is sent from the idle composer (Send leaves, a new Send and Stop arrive), then Stop is pressed (back).
//             The outgoing composer is a copy that React removes 280 ms after the swap, so its timer is held while the frames are
//             taken (one real second apart); every animation under the crossfade is paused and seeked.
//   gesture   the plane's poses and what leads to them: first character typed, pointer in, pointer out, press, text deleted.
//   guidance  the button that stays while a run is going: a guidance message is sent and the plane relaunches.
import fs from "node:fs";
import path from "node:path";
import { launch } from "./launch.mjs";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const scenes = (args.scene || "swap,gesture,guidance").split(",");
const theme = args.theme || "light";
const zoom = Number(args.zoom || 4);
const out = args.out || path.join(process.env.TEMP || "/tmp", "loom-motion-lab", "send-film.png");
fs.mkdirSync(path.dirname(out), { recursive: true });
const times = (args.times || "0,20,40,60,80,100,120,150,200,279").split(",").map(Number);

const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: Number(args.dsf || 1.5) });
const page = await ctx.newPage();
await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${theme}&lang=en&empty=1`);
await page.waitForSelector(".app-shell .composer .send-button", { timeout: 90000 });
await page.waitForTimeout(1500);

const rows = [];
const textarea = (steering) => page.locator(`.composer${steering ? ".is-steering" : ""} textarea`).first();

/** One picture of the box around the selector (or of the union of every send button under `.composer-mode-crossfade`). */
async function shoot(label, selector = ".composer .send-button", pad = 8) {
  const box = await page.evaluate(([selector, union]) => {
    const rects = [...document.querySelectorAll(union ? ".composer-mode-crossfade .send-button" : selector)].map((n) => n.getBoundingClientRect());
    const x0 = Math.min(...rects.map((r) => r.left)), x1 = Math.max(...rects.map((r) => r.right));
    const y0 = Math.min(...rects.map((r) => r.top)), y1 = Math.max(...rects.map((r) => r.bottom));
    return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
  }, [selector, selector === "union"]);
  const buf = await page.screenshot({ type: "png", clip: { x: box.x - pad, y: box.y - pad, width: box.width + pad * 2, height: box.height + pad * 2 } });
  return { label, data: buf.toString("base64"), w: box.width + pad * 2, h: box.height + pad * 2 };
}

/** Pause everything that was started under `host` and take a picture at each time. */
async function filmAnimations(label, getAnimations, at, selector) {
  const names = await page.evaluate(getAnimations);
  console.log(label, names.join(" | "));
  const frames = [];
  for (const t of at) {
    await page.evaluate((t) => window.__anims.forEach((a) => { a.currentTime = t; }), t);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    frames.push(await shoot(`${t} ms`, selector));
  }
  await page.evaluate(() => { window.__anims.forEach((a) => a.play()); const held = (window.__held || []).splice(0); held.forEach(([fn, rest]) => fn(...rest)); });
  rows.push({ label: `${theme} · ${label}`, frames });
}

const describe = () => window.__anims.map((a) => `${a.animationName || a.transitionProperty}@${a.effect.target.tagName}`);

if (scenes.includes("swap")) {
  // Hold React's 280 ms timer (Crossfade removes the outgoing copy then) and catch the copy the moment it appears.
  await page.evaluate(() => {
    window.__setTimeout = window.setTimeout;
    window.__held = [];
    window.setTimeout = (fn, ms, ...rest) => (ms === 280 ? (window.__held.push([fn, rest]), -1) : window.__setTimeout(fn, ms, ...rest));
  });
  const arm = () => page.evaluate(() => {
    window.__anims = null;
    new MutationObserver((_, observer) => {
      if (window.__anims || !document.querySelector(".composer-mode-crossfade > .crossfade-out")) return;
      window.__anims = document.querySelector(".composer-mode-crossfade").getAnimations({ subtree: true });
      window.__anims.forEach((a) => a.pause());
      observer.disconnect();
    }).observe(document.body, { childList: true, subtree: true });
  });
  await textarea().click();
  await page.keyboard.type("hello");
  await page.waitForTimeout(900);
  await arm();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => window.__anims, null, { timeout: 5000 });
  await filmAnimations("send: idle composer → guidance composer", describe, times, "union");
  await page.waitForTimeout(1500);
  await arm();
  await page.locator(".composer.is-steering .send-button.stop").click();
  await page.waitForFunction(() => window.__anims, null, { timeout: 5000 });
  await filmAnimations("stop: guidance composer → idle composer", describe, times, "union");
}

if (scenes.includes("gesture")) {
  // Start from a clean idle composer (the swap scene leaves a run going).
  await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${theme}&lang=en&empty=1`);
  await page.waitForSelector(".app-shell .composer .send-button", { timeout: 90000 });
  await page.waitForTimeout(1500);
  const sel = ".composer .send-button";
  const freeze = () => page.evaluate((sel) => { window.__anims = document.querySelector(sel).getAnimations({ subtree: true }); window.__anims.forEach((a) => a.pause()); return window.__anims.map((a) => `${a.animationName || a.transitionProperty}@${a.effect.target.tagName}`); }, sel);
  const box = await page.locator(sel).first().boundingBox();
  const centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  const film = async (label, at) => { await filmAnimations(label, () => window.__anims.map((a) => `${a.animationName || a.transitionProperty}@${a.effect.target.tagName}`), at, sel); };
  await textarea().click();
  await page.keyboard.type("h");
  await freeze();
  await film("wake: first character typed", [0, 60, 120, 180, 260, 360, 520, 700]);
  await page.waitForTimeout(900);
  await page.mouse.move(centre.x, centre.y);
  await freeze();
  await film("pointer in", [0, 40, 80, 140, 220, 340, 520]);
  await page.waitForTimeout(900);
  await page.mouse.down();
  await freeze();
  await film("press (held)", [0, 30, 60, 90, 140]);
  await page.mouse.move(2, 2);
  await page.mouse.up();
  await page.waitForTimeout(900);
  await page.mouse.move(centre.x, centre.y);
  await page.waitForTimeout(900);
  await page.mouse.move(2, 2);
  await freeze();
  await film("pointer out", [0, 40, 80, 140, 220, 340]);
  await page.waitForTimeout(900);
  await textarea().click();
  await page.keyboard.press("Backspace");
  await freeze();
  await film("text deleted: back to idle", [0, 80, 160, 240, 360]);
}

if (scenes.includes("guidance")) {
  await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${theme}&lang=en&empty=1`);
  await page.waitForSelector(".app-shell .composer .send-button", { timeout: 90000 });
  await page.waitForTimeout(1500);
  await textarea().click();
  await page.keyboard.type("start");
  await page.keyboard.press("Enter");
  await page.waitForSelector(".composer.is-steering .send-button:not(.stop)", { timeout: 10000 });
  await page.waitForTimeout(1500);
  const sel = ".composer.is-steering .send-button:not(.stop)";
  await textarea(true).click();
  await page.keyboard.type("more");
  await page.waitForTimeout(900);
  await page.keyboard.press("Enter");
  await page.evaluate((sel) => { window.__anims = document.querySelector(sel).getAnimations({ subtree: true }); window.__anims.forEach((a) => a.pause()); }, sel);
  await filmAnimations("guidance sent: the plane relaunches", describe, args.times ? times : [0, 20, 45, 70, 100, 130, 170, 220, 280, 360, 480, 640, 759], sel);
}

// Contact sheet: one row per film.
const sheet = await ctx.newPage();
await sheet.setContent(`<body style="margin:0;padding:12px;background:#777;color:#eee;font:11px system-ui">${rows.map((row) => `<div style="margin:0 0 12px"><div style="margin:0 0 4px">${row.label}</div><div style="display:flex;gap:8px;flex-wrap:wrap">${
  row.frames.map((f) => `<div style="text-align:center"><img style="width:${f.w * zoom}px;height:${f.h * zoom}px;display:block;image-rendering:pixelated;background:#444" src="data:image/png;base64,${f.data}"><span style="font-size:10px;opacity:.85">${f.label}</span></div>`).join("")
}</div></div>`).join("")}</body>`);
await sheet.setViewportSize({ width: Math.min(2400, Math.ceil(Math.max(...rows.map((r) => r.frames.reduce((s, f) => s + f.w * zoom + 8, 0))) + 30)), height: 300 });
await sheet.waitForTimeout(250);
await sheet.screenshot({ path: out, fullPage: true });
console.log(out);
await browser.close();
