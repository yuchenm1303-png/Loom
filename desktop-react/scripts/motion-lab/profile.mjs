// Motion lab: where does the main thread's JavaScript go during a live run?
//   node profile.mjs <url> [width=935] [height=731] [dsf=1.5] [--top 30]
// Samples the page with the DevTools CPU profiler from the moment the prompt is sent until the turn ends, then
// aggregates self time by function and by library bucket (react-dom, the Markdown pipeline, KaTeX/highlight, the app).
// The profiler inflates regex-heavy code (see loom-renderer-perf-findings): trust ratios, not absolute milliseconds.
import { launch } from "./launch.mjs";
const args = process.argv.slice(2).filter((arg) => !arg.startsWith("--"));
const topIndex = process.argv.indexOf("--top");
const top = topIndex >= 0 ? Number(process.argv[topIndex + 1]) : 30;
const [url, width = "935", height = "731", dsf = "1.5"] = args.filter((arg) => arg !== process.argv[topIndex + 1]);
const browser = await launch();
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: Number(dsf) });
  await page.goto(url);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  await page.waitForTimeout(800);
  const client = await page.context().newCDPSession(page);
  await client.send("Profiler.enable");
  await client.send("Profiler.setSamplingInterval", { interval: 250 });
  await client.send("Profiler.start");
  const started = Date.now();
  await page.evaluate(() => window.liveFixture.send());
  await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length > 0, null, { timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length === 0, null, { timeout: 300000 });
  const wall = Date.now() - started;
  const { profile } = await client.send("Profiler.stop");
  const byId = new Map(profile.nodes.map((node) => [node.id, node]));
  const self = new Map();
  const deltas = profile.timeDeltas;
  for (let index = 0; index < profile.samples.length; index += 1) {
    const node = byId.get(profile.samples[index]);
    const { functionName, url: file, lineNumber } = node.callFrame;
    const key = `${functionName || "(anonymous)"} ${shorten(file)}:${lineNumber}`;
    self.set(key, (self.get(key) ?? 0) + (deltas[index] ?? 0));
  }
  const total = [...self.values()].reduce((sum, value) => sum + value, 0);
  const idle = [...self.entries()].filter(([key]) => /^\((idle|program)\)/.test(key)).reduce((sum, [, value]) => sum + value, 0);
  const buckets = new Map();
  for (const [key, value] of self) {
    if (/^\((idle|program)\)/.test(key)) continue;
    const bucket = bucketOf(key);
    buckets.set(bucket, (buckets.get(bucket) ?? 0) + value);
  }
  const busy = total - idle;
  console.log(`wall ${wall}ms; sampled ${Math.round(total / 1000)}ms of which busy ${Math.round(busy / 1000)}ms (${Math.round((busy / total) * 100)}%)`);
  console.log("\nby library:");
  for (const [bucket, value] of [...buckets.entries()].sort((a, b) => b[1] - a[1])) console.log(`  ${bucket.padEnd(22)} ${String(Math.round(value / 1000)).padStart(6)}ms  ${Math.round((value / busy) * 100)}%`);
  console.log(`\ntop ${top} functions by self time:`);
  for (const [key, value] of [...self.entries()].filter(([name]) => !/^\((idle|program)\)/.test(name)).sort((a, b) => b[1] - a[1]).slice(0, top)) {
    console.log(`  ${String(Math.round(value / 1000)).padStart(6)}ms  ${key}`);
  }
} finally { await browser.close(); }

function shorten(file) {
  return String(file || "").replace(/^https?:\/\/[^/]+/, "").replace(/\?.*$/, "").replace(/^\/node_modules\/\.vite\/deps\//, "dep:").replace(/^\/src\//, "");
}
function bucketOf(key) {
  if (/react-dom|react\.js|scheduler/.test(key)) return "react";
  if (/react-markdown|remark|rehype|micromark|mdast|unified|hast|vfile|unist|property-information|space-separated|comma-separated|decode-named|character-entities|trim-lines|html-url|ccount|markdown-table|devlop|estree/.test(key)) return "markdown pipeline";
  if (/katex|lowlight|highlight/.test(key)) return "katex/highlight";
  if (/lucide/.test(key)) return "icons";
  if (/^\((garbage collector)\)/.test(key)) return "gc";
  if (/dep:/.test(key)) return "other deps";
  if (/\(root\)|\(program\)/.test(key)) return "native";
  return "app code";
}
