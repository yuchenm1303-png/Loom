// The send flight, frame by frame, on the real App (the live-app fixture): the path the message takes, how fast it goes,
// where the real entry is, when the capsule arrives, and whether the hand-off at the end is clean.
//   node scripts/motion-lab/flight-trace.mjs [--empty 1] [--latency 1200] [--theme dark|light] [--text "..."] [--dsf 1.5]
//        [--height 860] [--png out.png]
// --empty 1 sends from the home screen; --height 520 makes the page scroll to make room; --text sets the message.
// Prints the launch/land events, the peak speed, a table of the ghost's first glyph against the real entry, the hand-off
// (last ghost frame against the first frame of the real bubble) and the worst frame gaps. --png draws the path.
import fs from "node:fs";
import path from "node:path";
import { launch } from "./launch.mjs";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const text = args.text || "Add email format validation to the login page and cover it with a unit test.";
const browser = await launch();
try {
  const ctx = await browser.newContext({ viewport: { width: Number(args.width || 1360), height: Number(args.height || 860) }, deviceScaleFactor: Number(args.dsf || 1.5) });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const rec = { frames: [], events: [], keydownAt: null };
    window.__rec = rec;
    window.addEventListener("loom:send-flight", (event) => rec.events.push({ t: performance.now(), ...event.detail }));
    window.addEventListener("keydown", (event) => { if (event.key === "Enter" && rec.keydownAt === null) rec.keydownAt = performance.now(); }, true);
    const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
    let on = false;
    window.__record = (value) => { on = value; };
    const loop = () => {
      if (on) {
        const ghost = document.querySelector(".send-flight-ghost");
        const textLayer = ghost?.querySelector(".send-flight-text .user-message");
        const cardLayer = ghost?.querySelector(".send-flight-card .user-message");
        const entry = [...document.querySelectorAll(".transcript > .turn-block > .entry-user_message")].at(-1);
        const scroller = document.querySelector(".conversation-stage > .transcript-scroll");
        const capsule = document.querySelector(".turn-block:last-child .pending-thinking-presence");
        rec.frames.push({
          t: performance.now(), ghost: box(textLayer),
          cardOp: cardLayer ? Number(getComputedStyle(cardLayer.closest(".send-flight-card")).opacity) : null,
          real: box(entry?.querySelector(".user-message")), realVis: entry ? getComputedStyle(entry).visibility : null,
          st: scroller ? scroller.scrollTop : null,
          cap: capsule ? Number(getComputedStyle(capsule).opacity) : null, capH: capsule ? capsule.getBoundingClientRect().height : null,
        });
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  });
  await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${args.theme || "dark"}&lang=en${args.empty === "1" ? "&empty=1" : ""}&latency=${args.latency || 1200}`);
  await page.waitForSelector(".app-shell .composer textarea:not([disabled])", { timeout: 90000 });
  await page.waitForTimeout(1500);
  await page.locator(".composer textarea").click();
  await page.keyboard.type(text);
  await page.waitForTimeout(600);
  await page.evaluate(() => window.__record(true));
  await page.keyboard.press("Enter");
  await page.waitForTimeout(Number(args.duration || 1500));
  const rec = await page.evaluate(() => window.__rec);

  const t0 = rec.keydownAt;
  const rel = (t) => Math.round(t - t0);
  console.log("events:", rec.events.map((e) => `${rel(e.t)}ms ${e.phase}${e.duration ? ` D=${e.duration} dx=${Math.round(e.dx)} dy=${Math.round(e.dy)} ratio=${e.ratio?.toFixed(3)}` : ""}${e.missX !== undefined ? ` miss=${e.missX.toFixed(1)},${e.missY.toFixed(1)}` : ""}${e.reason ? ` ${e.reason}` : ""}`).join(" | "));
  const flying = rec.frames.filter((f) => f.ghost);
  console.log("ghost frames:", flying.length, flying.length ? `from ${rel(flying[0].t)} to ${rel(flying.at(-1).t)} ms` : "");
  let peak = 0, peakAt = 0;
  for (let i = 1; i < flying.length; i += 1) {
    const speed = Math.hypot(flying[i].ghost[0] - flying[i - 1].ghost[0], flying[i].ghost[1] - flying[i - 1].ghost[1]) / (flying[i].t - flying[i - 1].t);
    if (speed > peak) { peak = speed; peakAt = rel(flying[i].t); }
  }
  console.log(`peak speed ${(peak * 1000).toFixed(0)} px/s at ${peakAt} ms`);
  console.log("t(ms)   ghost x,y        card op   page scrollTop  real-entry top  capsule op/h");
  for (const f of flying.filter((_, i) => i % Math.max(1, Math.floor(flying.length / 22)) === 0)) {
    console.log(String(rel(f.t)).padStart(5), `${f.ghost[0].toFixed(1)},${f.ghost[1].toFixed(1)}`.padEnd(16), String((f.cardOp ?? 0).toFixed(2)).padStart(6), String(f.st?.toFixed(1)).padStart(14), String(f.real ? f.real[1].toFixed(1) : "-").padStart(14), `${f.cap?.toFixed?.(2)}/${f.capH?.toFixed?.(0)}`);
  }
  const lastGhost = flying.at(-1);
  const firstReal = rec.frames.find((f) => f.t > (lastGhost?.t ?? 0) && f.realVis === "visible" && f.real);
  if (lastGhost && firstReal) console.log("hand-off: last ghost", lastGhost.ghost.map((v) => v.toFixed(1)).join(","), "| first real", firstReal.real.map((v) => v.toFixed(1)).join(","), "| delta", (firstReal.real[0] - lastGhost.ghost[0]).toFixed(2), (firstReal.real[1] - lastGhost.ghost[1]).toFixed(2), "| frames between", rec.frames.indexOf(firstReal) - rec.frames.indexOf(lastGhost));
  const gaps = rec.frames.slice(1).map((f, i) => ({ at: rel(f.t), dt: f.t - rec.frames[i].t })).filter((g) => g.at >= -30);
  console.log("worst frame gaps (at:ms):", [...gaps].sort((a, b) => b.dt - a.dt).slice(0, 4).map((g) => `${g.at}:${Math.round(g.dt)}`).join(" "), "| frames", rec.frames.length);
  if (errors.length) console.log("page errors:", errors);

  if (args.png) {
    const sheet = await ctx.newPage();
    await sheet.setContent(`<canvas id=c width=${args.width || 1360} height=${args.height || 860} style="background:#101216"></canvas>`);
    await sheet.evaluate((points) => {
      const c = document.getElementById("c").getContext("2d");
      points.forEach((p, i) => {
        c.fillStyle = `hsl(${260 - (i / points.length) * 200} 80% 65%)`; c.beginPath(); c.arc(p.x, p.y, 4, 0, 7); c.fill();
        if (i % 5 === 0) { c.fillStyle = "#aab"; c.font = "11px sans-serif"; c.fillText(String(p.t), p.x + 6, p.y - 6); }
      });
    }, flying.map((f) => ({ t: rel(f.t), x: f.ghost[0], y: f.ghost[1] })));
    fs.mkdirSync(path.dirname(args.png), { recursive: true });
    await sheet.locator("#c").screenshot({ path: args.png });
    console.log("path:", args.png);
  }
} finally {
  await browser.close();
}
