// Motion lab: what really runs when you click.
//   node spec.mjs <origin> [--theme dark|light] [--only step,step] [--json out.json] [--all]
// Drives the live-app fixture through the most common interactions (popovers and menus, folds, thread and page
// switches, settings, the docks) and prints, for each step, every animation and transition that started with its
// effective timing: element, property or keyframes, duration, delay, curve, and what the keyframes move. Icon
// gestures, colour changes and ambient loops are left out unless --all. The last block counts how many distinct
// (duration, curve) pairs the whole tour used: on the scale there are a dozen, not fifty.
import fs from "node:fs";
import { open, runStep, readRows, scenarios, describeRow, interesting } from "./spec-lib.mjs";

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const option = (name, fallback = "") => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const origin = argv.find((arg) => /^https?:/.test(arg)) || process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const only = option("--only") ? new Set(option("--only").split(",")) : null;

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const { browser, page, errors } = await open(chromium, { origin, executablePath: process.env.LOOM_CHROMIUM_PATH, theme: option("--theme", "dark") });
try {
  for (const [name, action, settle] of scenarios()) {
    if (only && !only.has(name)) continue;
    const failure = await runStep(page, name, () => action(page), settle);
    if (failure) console.log(`! ${name}: ${failure}`);
  }
  const rows = await readRows(page);
  if (option("--json")) fs.writeFileSync(option("--json"), JSON.stringify(rows, null, 1));

  const short = (text, n) => (text.length > n ? text.slice(0, n - 1) + "…" : text);
  const steps = new Map();
  for (const row of flag("--all") ? rows : interesting(rows)) {
    if (!steps.has(row.step)) steps.set(row.step, new Map());
    const key = [row.kind, row.name, row.target, row.duration, row.delay, row.easing].join("|");
    const bucket = steps.get(row.step);
    if (!bucket.has(key)) bucket.set(key, { ...row, n: 0 });
    bucket.get(key).n += 1;
  }
  for (const [step, bucket] of steps) {
    console.log(`\n== ${step}`);
    for (const row of bucket.values()) {
      const d = describeRow(row);
      const detail = Object.keys(row.first).filter((p) => !/color|Color|background|border|boxShadow|outline|fill|stroke/.test(p))
        .map((p) => `${p}: ${short(row.first[p], 22)} → ${short(row.last[p], 22)}`).join("; ");
      console.log(`  ${row.kind.slice(0, 5).padEnd(5)} ${short(row.target, 40).padEnd(40)} ${short(row.name, 26).padEnd(26)} ${String(row.duration).padStart(6)}ms +${String(row.delay).padEnd(4)} ${short(row.easing.replace("cubic-bezier", ""), 24).padEnd(24)} ${d.reversed ? "(reversed) " : ""}${short(detail, 70)}${row.n > 1 ? `  ×${row.n}` : ""}`);
    }
  }
  const pairs = new Map();
  for (const row of interesting(rows)) {
    const d = describeRow(row);
    if (d.reversed || d.viewTransition || d.layoutAnchor) continue;
    const key = `${Math.round(row.duration)}ms ${row.easing.replace(/\s+/g, "").replace("cubic-bezier", "cb").slice(0, 36)}`;
    pairs.set(key, (pairs.get(key) ?? 0) + 1);
  }
  console.log(`\n${pairs.size} distinct duration+curve pairs (docks and reversed transitions left out):`);
  for (const [key, n] of [...pairs.entries()].sort((a, b) => parseFloat(a[0]) - parseFloat(b[0]))) console.log(`  ${String(n).padStart(4)}  ${key}`);
  if (errors.length) console.log("page errors:", [...new Set(errors)].slice(0, 5));
} finally { await browser.close(); }
