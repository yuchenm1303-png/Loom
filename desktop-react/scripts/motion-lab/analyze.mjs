// Analyze a trace: report discrete jumps (position/height/opacity) per element.
//   node analyze.mjs <trace.json> [jumpPx=14]
import fs from "node:fs";
const [file, jumpArg = "14"] = process.argv.slice(2);
const jump = Number(jumpArg);
const { frames, st } = JSON.parse(fs.readFileSync(file, "utf8"));
const byId = new Map();
for (const frame of frames) {
  for (const [id, label, top, height, opacity, left] of frame.rows) {
    if (!byId.has(id)) byId.set(id, { label, samples: [] });
    byId.get(id).samples.push({ t: frame.t, top, height, opacity, left, st: frame.st });
  }
}
const events = [];
for (const [id, { label, samples }] of byId) {
  for (let i = 1; i < samples.length; i += 1) {
    const a = samples[i - 1];
    const b = samples[i];
    if (b.t - a.t > 90) continue; // a stalled frame is reported separately
    // Compare in content space so a scroll position change is not a "move".
    const dTop = (b.top + b.st) - (a.top + a.st);
    const dH = b.height - a.height;
    const dLeft = b.left - a.left;
    const dOp = b.opacity - a.opacity;
    if (Math.abs(dTop) >= jump && Math.abs(dH) < 1) events.push({ t: b.t, label, kind: "move", d: dTop, from: a.top, to: b.top });
    if (Math.abs(dH) >= jump * 1.5) events.push({ t: b.t, label, kind: "height", d: dH, from: a.height, to: b.height });
    if (Math.abs(dLeft) >= 4) events.push({ t: b.t, label, kind: "left", d: dLeft });
    if (Math.abs(dOp) >= .45) events.push({ t: b.t, label, kind: "opacity", d: dOp, from: a.opacity, to: b.opacity });
  }
}
// scroll jumps
const scrolls = [];
for (let i = 1; i < frames.length; i += 1) {
  const a = frames[i - 1];
  const b = frames[i];
  if (a.st === null || b.st === null) continue;
  const d = b.st - a.st;
  if (Math.abs(d) >= 40) scrolls.push({ t: b.t, d, from: a.st, to: b.st, sh: b.sh });
}
// long frames
const long = [];
for (let i = 1; i < frames.length; i += 1) {
  const d = frames[i].t - frames[i - 1].t;
  if (d > 40) long.push({ t: frames[i].t, d });
}
events.sort((x, y) => x.t - y.t);
console.log(`== ${events.length} geometry events (>=${jump}px, content space)`);
for (const e of events.slice(0, 120)) console.log(`${String(e.t).padStart(6)}ms ${e.kind.padEnd(7)} ${String(Math.round(e.d * 10) / 10).padStart(7)}  ${e.label}${e.from !== undefined ? `  (${e.from} -> ${e.to})` : ""}`);
console.log(`== ${scrolls.length} scroll jumps >=40px`);
for (const s of scrolls.slice(0, 40)) console.log(`${String(s.t).padStart(6)}ms scroll ${s.d}  (${s.from} -> ${s.to}, scrollHeight ${s.sh})`);
console.log(`== ${long.length} frames longer than 40ms`);
console.log(long.slice(0, 20).map((l) => `${l.t}:${Math.round(l.d)}`).join("  "));
