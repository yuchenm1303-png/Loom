// Motion lab: two feel runs side by side (feel.mjs --json / feel-electron.mjs FEEL_OUT): for each interaction's first
// subject that changes, ms from the first input to the first visible change, to half way and to 90%, and the largest
// one-frame step. Times are from the pointer going down; a click lands about 30 ms later in these drivers.
//   node feel-compare.mjs before.json after.json
import fs from "node:fs";

const [before, after] = process.argv.slice(2).map((file) => new Map(JSON.parse(fs.readFileSync(file, "utf8")).map((run) => [run.name, run])));
const pick = (run) => {
  const row = run?.rows.find((r) => r.channels && Object.keys(r.channels).length);
  const ch = row && (row.channels.o || row.channels.h || row.channels.x || Object.values(row.channels)[0]);
  return ch ? ch : null;
};
const cell = (c) => (c ? [c.first, c.t50, c.t90].map((v) => String(v ?? "-").padStart(4)).join(" ") + ` ${String(c.jump).padStart(3)}%` : "     (none)        ");
console.log(`${"interaction".padEnd(18)} | before: first  t50  t90 jump | after: first  t50  t90 jump`);
for (const name of new Set([...before.keys(), ...after.keys()])) {
  console.log(`${name.padEnd(18)} |        ${cell(pick(before.get(name)))} |       ${cell(pick(after.get(name)))}`);
}
