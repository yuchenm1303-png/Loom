// Contact sheets of the icon lab.
//   node scripts/motion-lab/icon-sheet.mjs [--theme dark] [--only trash,copy] [--size 28] [--dsf 2] [--out file.png]
// Settles each transition (default 900 ms) so the sheet shows end poses.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const theme = args.theme || "dark";
const dsf = Number(args.dsf || 3);
const out = args.out || path.join(os.tmpdir(), "loom-motion-lab", `icon-sheet-${theme}.png`);
fs.mkdirSync(path.dirname(out), { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const ctx = await browser.newContext({ viewport: { width: 760, height: 900 }, deviceScaleFactor: dsf });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
const q = new URLSearchParams({ theme, ...(args.only ? { only: args.only } : {}), ...(args.size ? { size: args.size } : {}) });
await page.goto(`${origin}/scripts/fixtures/icon-lab.html?${q}`);
await page.waitForSelector("[data-lab-cell]", { timeout: 60000 });
await page.waitForTimeout(Number(args.wait || 900));
const box = await page.evaluate(() => ({ x: 0, y: 0, width: Math.ceil(document.documentElement.scrollWidth), height: Math.ceil(document.documentElement.scrollHeight) }));
await page.setViewportSize({ width: Math.max(760, box.width), height: Math.min(box.height, 6000) });
await page.waitForTimeout(100);
await page.screenshot({ path: out, clip: { x: 0, y: 0, width: box.width, height: Math.min(box.height, 6000) } });
console.log(out, box, errors.length ? "ERRORS: " + errors.slice(0, 5).join(" | ") : "no page errors");
await browser.close();
