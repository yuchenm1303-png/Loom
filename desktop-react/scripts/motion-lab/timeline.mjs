// Print how selected elements' heights/opacity evolve (only when something changes): node timeline.mjs trace.json "label-regex" [fromMs] [toMs]
import fs from "node:fs";
const [file, pattern, from = "0", to = "999999"] = process.argv.slice(2);
const { frames } = JSON.parse(fs.readFileSync(file, "utf8"));
const re = new RegExp(pattern);
let last = "";
for (const frame of frames) {
  if (frame.t < Number(from) || frame.t > Number(to)) continue;
  const parts = frame.rows.filter((row) => re.test(row[1])).map((row) => `${row[1].slice(0, 26)}:h${row[3]}/o${row[4]}/y${Math.round(row[2] + frame.st)}`);
  const line = parts.join("  ");
  if (line !== last) { console.log(String(frame.t).padStart(6), line); last = line; }
}
