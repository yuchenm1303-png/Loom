// The way and the speed of the send flight (src/sendFlightMotion.ts), as one picture and one table, with no page and
// no animation clock: the module is compiled and run on its own, and what it answers is drawn the way a 60 Hz screen
// would show it. It is the tool for "is it near constant speed?" and "how bent is the path?", which a film of frames
// cannot say as plainly.
//   node scripts/motion-lab/flight-curve.mjs [--flights "-367,372;-650,110;-40,150"] [--out file.png]
//
//   For each flight (dx, dy: where the capsule starts, relative to where it rests): the path, with a dot for every
//   60 Hz frame (their spacing is the speed: bunched dots are slow), outlines of the capsule at a few moments, the
//   straight line it does not take, and under it a bar for the speed of every frame.
//   In the table: the time, when the clock is at 97%, the top speed and when it comes (share of the time), how long the
//   speed is within 80% of the top (a plateau reads as constant speed: it should be about a seventh), how far the path
//   bows from the straight line, and how fast the first frame is (it leaves from rest).
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { launch } from "./launch.mjs";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const out = args.out || path.join(process.env.TEMP || "/tmp", "loom-motion-lab", "flight-curve.png");
fs.mkdirSync(path.dirname(out), { recursive: true });
const source = fs.readFileSync(new URL("../../src/sendFlightMotion.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { capsuleMotion } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

const flights = (args.flights || "-367,372;-620,372;-650,110;-420,120;-140,70;-40,150;-40,520;200,-300")
  .split(";").map((pair) => pair.split(",").map(Number)).map(([dx, dy]) => ({ dx, dy, width: 300, height: 44 }));
const numbers = (text) => String(text).match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/g).map(Number);

const cells = flights.map((input) => {
  const motion = capsuleMotion(input);
  const points = motion.path.map((frame) => numbers(frame.translate));
  const frameMs = motion.duration / (motion.path.length - 1);
  const speeds = points.slice(1).map((point, index) => Math.hypot(point[0] - points[index][0], point[1] - points[index][1]) / frameMs * 1000);
  const peak = Math.max(...speeds);
  const near = speeds.filter((value) => value > 0.8 * peak).length / speeds.length;
  return { input, motion, points, speeds, peak, near, peakShare: (speeds.indexOf(peak) + 0.5) / speeds.length };
});
for (const { input, motion, peak, near, peakShare, speeds } of cells) {
  console.log(`${`${input.dx},${input.dy}`.padEnd(10)} D ${String(Math.round(motion.distance)).padStart(4)} px  T ${motion.duration} ms  clock 97% at ${String(motion.arrive).padStart(3)} ms  top ${String(Math.round(peak)).padStart(5)} px/s at ${(peakShare * 100).toFixed(0)}%  >80% of top for ${(near * 100).toFixed(0).padStart(2)}%  bow ${(motion.arc / motion.distance * 100).toFixed(0).padStart(2)}%  first frame ${(speeds[0] / peak * 100).toFixed(1)}% of top`);
}

const browser = await launch();
const page = await browser.newPage({ viewport: { width: 1740, height: 330 * Math.ceil(cells.length / 3) + 20 } });
await page.setContent(`<body style="margin:0;background:#101216;color:#cdd3de;font:12px sans-serif"><div id=g style="display:grid;grid-template-columns:repeat(3,570px);gap:8px;padding:8px"></div></body>`);
await page.evaluate((cells) => {
  const g = document.getElementById("g");
  for (const cell of cells) {
    const { input, motion, points, speeds, peak } = cell;
    const holder = document.createElement("div");
    holder.style.cssText = "background:#15181e;padding:4px";
    const canvas = document.createElement("canvas");
    canvas.width = 560; canvas.height = 312; holder.append(canvas); g.append(holder);
    const x = canvas.getContext("2d");
    const pad = 14, capsuleW = 150, capsuleH = 24;
    // As on the screen: the capsule starts (dx, dy) away from its place, so a flight that rises starts at the bottom.
    const k = Math.min((560 - 2 * pad - capsuleW) / Math.max(Math.abs(input.dx), 1), (200 - 2 * pad - capsuleH) / Math.max(Math.abs(input.dy), 1), 1);
    const left = pad + capsuleW / 2;
    const sx = (v) => left + (v - Math.min(input.dx, 0)) * k;
    const sy = (v) => pad + capsuleH / 2 + (v - Math.min(input.dy, 0)) * k;
    x.strokeStyle = "#2a2f3a"; x.setLineDash([4, 4]); x.beginPath(); x.moveTo(sx(input.dx), sy(input.dy)); x.lineTo(sx(0), sy(0)); x.stroke(); x.setLineDash([]);
    const n = points.length - 1;
    for (const share of [0, 0.15, 0.3, 0.45, 0.6, 0.8, 1]) {
      const p = points[Math.round(share * n)];
      x.strokeStyle = share === 1 ? "#8b7cf6" : "#3a4150"; x.beginPath(); x.roundRect(sx(p[0]) - capsuleW / 2, sy(p[1]) - capsuleH / 2, capsuleW, capsuleH, capsuleH / 2); x.stroke();
    }
    x.strokeStyle = "#566"; x.beginPath(); points.forEach((p, i) => (i ? x.lineTo(sx(p[0]), sy(p[1])) : x.moveTo(sx(p[0]), sy(p[1])))); x.stroke();
    const frames = Math.floor(motion.duration / 16.667);
    for (let i = 0; i <= frames; i += 1) {
      const p = points[Math.min(n, Math.round((i * 16.667 / motion.duration) * n))];
      x.fillStyle = i % 2 ? "#e9c46a" : "#f4a261"; x.beginPath(); x.arc(sx(p[0]), sy(p[1]), 2.6, 0, 7); x.fill();
    }
    const base = 300, top = 222, w = (560 - 2 * pad) / (frames + 1);
    x.fillStyle = "#1b1f27"; x.fillRect(pad, top, 560 - 2 * pad, base - top);
    for (let i = 0; i <= frames; i += 1) {
      const j = Math.min(speeds.length - 1, Math.round((i * 16.667 / motion.duration) * n));
      const h = (speeds[j] / peak) * (base - top - 4);
      x.fillStyle = speeds[j] > 0.8 * peak ? "#8ab4e8" : "#6c9bd2"; x.fillRect(pad + i * w + 1, base - h, w - 2, h);
    }
    x.fillStyle = "#cdd3de"; x.font = "12px sans-serif";
    x.fillText(`${input.dx}, ${input.dy}   ${Math.round(motion.distance)} px in ${motion.duration} ms   top ${Math.round(peak)} px/s   bow ${(motion.arc / motion.distance * 100).toFixed(0)}%`, pad, 214);
  }
}, cells.map(({ input, motion, points, speeds, peak }) => ({ input, motion: { duration: motion.duration, distance: motion.distance, arc: motion.arc }, points, speeds, peak })));
await page.screenshot({ path: out, fullPage: true });
console.log(out);
await browser.close();
