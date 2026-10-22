// Motion lab: how soon the screen answers the hand, how fast it gets there, and whether a second input turns it
// around smoothly. The live-app fixture in headless Chrome, driven with real mouse and key events.
//   node feel.mjs <origin> [--only a,b] [--theme dark|light] [--json out.json]
// Headless Chrome composites in software and paints slowly: read the shapes and the order of things here, and the
// milliseconds in a real window (feel-electron.mjs). feel-lib.mjs has the interactions and what the columns mean.
import fs from "node:fs";
import { launch } from "./launch.mjs";
import { SAMPLER, SCENARIOS, report } from "./feel-lib.mjs";

const argv = process.argv.slice(2);
const option = (name, fallback = "") => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const origin = argv.find((a) => /^https?:/.test(a)) || process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const only = option("--only") ? new Set(option("--only").split(",")) : null;

const browser = await launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${option("--theme", "dark")}&lang=en`);
await page.locator(".thread-header").waitFor({ timeout: 90000 });
await page.evaluate(SAMPLER);
await page.waitForTimeout(1500);
await page.mouse.move(700, 450);
const results = [];
for (const sc of SCENARIOS) {
  if (only && !only.has(sc.name) && !sc.prep) continue;
  await page.evaluate((subjects) => { const f = window.__feel; f.subjects = subjects; f.frames = []; f.inputs = []; f.on = true; }, sc.subjects);
  await page.waitForTimeout(60);
  for (const [kind, spec, dx, dy] of sc.steps) {
    if (kind === "wait") { await page.waitForTimeout(spec); continue; }
    if (kind === "key") { await page.keyboard.press(spec); continue; }
    const point = await page.evaluate(([s, x, y]) => window.__feel.point(s, x, y), [spec, dx ?? null, dy ?? null]);
    if (!point) { console.log(`! ${sc.name}: not found ${spec}`); break; }
    const button = kind === "rclick" ? "right" : "left";
    await page.mouse.move(point.x, point.y);
    await page.waitForTimeout(90);
    await page.mouse.down({ button });
    await page.waitForTimeout(16);
    await page.mouse.up({ button });
  }
  await page.waitForTimeout(sc.wait);
  const { frames, inputs } = await page.evaluate(() => { const f = window.__feel; f.on = false; return { frames: f.frames, inputs: f.inputs }; });
  if (!inputs.length) { console.log(`! ${sc.name}: no input reached the page`); continue; }
  if (sc.prep && only && !only.has(sc.name)) continue;
  results.push(report(sc, frames, inputs));
}
if (option("--json")) fs.writeFileSync(option("--json"), JSON.stringify(results, null, 1));
if (errors.length) console.log("page errors:", [...new Set(errors)].slice(0, 5));
await browser.close();
