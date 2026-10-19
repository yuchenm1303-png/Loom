// The quick-tool dock arriving, slowed down: the page is reloaded with animations at a fraction of their speed (CDP) and
// photographed every `every` ms of real time, so each frame is a few ms of animation time; the frames are laid out in one
// contact sheet. `--scene carry` photographs a tool being picked up, carried down `--slots` places and put down instead.
//   node scripts/motion-lab/dock-film.mjs [--scene arrive|carry] [--rate .1] [--every 350] [--frames 14] [--slots 3] [--tool review]
//        [--theme dark] [--zoom .62] [--out dock-film.png]
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, arg, index, all) => (arg.startsWith("--") ? [...acc, [arg.slice(2), all[index + 1]]] : acc), []));
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const scene = args.scene || "arrive";
const rate = Number(args.rate || 0.1);
const every = Number(args.every || 350);
const count = Number(args.frames || 14);
const out = args.out || path.join(os.tmpdir(), "loom-motion-lab", "dock-film.png");
fs.mkdirSync(path.dirname(out), { recursive: true });

async function sheet(browser, frames) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  const zoom = Number(args.zoom || 0.62);
  const cells = frames.map((frame) => `<figure><img src="data:image/png;base64,${frame.png.toString("base64")}" style="zoom:${zoom}"><figcaption>${frame.label}</figcaption></figure>`).join("");
  await page.setContent(`<style>body{margin:0;padding:10px;background:#0b0c0f;display:grid;grid-template-columns:repeat(${Math.min(count, 7)},max-content);gap:8px;align-items:start;font:11px ui-monospace,monospace;color:#9aa3b2}figure{margin:0}figcaption{padding-top:3px;text-align:center}img{display:block}</style>${cells}`);
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }));
  await page.setViewportSize({ width: Math.min(size.w, 4000), height: Math.min(size.h, 4000) });
  await page.screenshot({ path: out, fullPage: true });
  await page.close();
}

const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1333, height: 853 }, deviceScaleFactor: 1.5 });
  const page = await context.newPage();
  const client = await context.newCDPSession(page);
  await client.send("Animation.enable");
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${args.theme || "dark"}&lang=en`);
  await page.locator(".loom-tool-dock").waitFor({ timeout: 60000 });
  await page.waitForTimeout(1500);
  const frames = [];
  if (scene === "arrive") {
    await client.send("Animation.setPlaybackRate", { playbackRate: rate });
    const started = Date.now();
    await page.reload({ waitUntil: "commit" });
    let clip = null;
    for (let index = 0; index < count; index++) {
      const wait = started + index * every - Date.now();
      if (wait > 0) await page.waitForTimeout(wait);
      const box = await page.locator(".loom-tool-stage").boundingBox().catch(() => null);
      if (box) clip ??= { x: box.x - 24, y: box.y - 16, width: box.width + 48, height: box.height + 32 };
      frames.push({ label: `${Math.round((Date.now() - started) * rate)} ms`, png: await page.screenshot({ clip: clip ?? { x: 1100, y: 40, width: 233, height: 520 } }) });
    }
  } else {
    const tool = args.tool || "review";
    const slots = Number(args.slots || 3);
    const box = await page.locator(`.loom-tool-button[data-tool-id="${tool}"]`).boundingBox();
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    const dock = await page.locator(".loom-tool-dock").boundingBox();
    const clip = { x: dock.x - 20, y: dock.y - 12, width: dock.width + 40, height: dock.height + 24 };
    await page.mouse.move(x, y);
    await page.waitForTimeout(300);
    await page.mouse.down();
    const steps = Math.max(2, count - 5);
    for (let index = 1; index <= steps; index++) {
      await page.mouse.move(x, y + (slots * 34 * index) / steps);
      await page.waitForTimeout(40);
      frames.push({ label: `carry ${index}/${steps}`, png: await page.screenshot({ clip }) });
    }
    await page.mouse.up();
    for (const wait of [30, 60, 90, 120, 200]) {
      await page.waitForTimeout(wait);
      frames.push({ label: `set down +${wait} ms`, png: await page.screenshot({ clip }) });
    }
  }
  await sheet(browser, frames);
  console.log("saved", out);
} finally {
  await browser.close();
}
