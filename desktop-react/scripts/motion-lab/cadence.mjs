// Typing cadence of the receiving message: characters revealed per frame and the gaps between reveals.
import fs from "node:fs";
const { frames } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const events = [];
let last = -1;
let lastT = 0;
for (const frame of frames) {
  if (frame.tx < 0) { last = -1; continue; }
  if (last >= 0 && frame.tx > last) events.push({ t: frame.t, add: frame.tx - last, gap: frame.t - lastT });
  if (frame.tx !== last) lastT = frame.t;
  last = frame.tx;
}
const adds = events.map((e) => e.add).sort((a, b) => a - b);
const gaps = events.map((e) => e.gap).sort((a, b) => a - b);
const q = (arr, p) => arr[Math.min(arr.length - 1, Math.floor(arr.length * p))];
console.log(`reveals ${events.length}; chars per reveal p50 ${q(adds, .5)} p90 ${q(adds, .9)} max ${adds[adds.length - 1]}; gap ms p50 ${q(gaps, .5)} p90 ${q(gaps, .9)} max ${gaps[gaps.length - 1]}`);
const buckets = new Map();
for (const e of events) { const k = Math.floor(e.t / 1000); buckets.set(k, (buckets.get(k) || 0) + e.add); }
console.log("chars per second:", [...buckets].map(([k, v]) => `${k}s:${v}`).join(" "));
console.log("sample:", events.slice(60, 100).map((e) => `${e.add}@${e.gap}`).join(" "));
