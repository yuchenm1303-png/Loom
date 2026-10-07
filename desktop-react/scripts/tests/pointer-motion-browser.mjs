import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { assertIntactPng } from "../png-integrity.mjs";

const assets = new URL("../../src/assets/cursor-motion/", import.meta.url);
const names = (await readdir(assets)).filter(name => name.endsWith(".png"));
for (const name of names) assertIntactPng(name, await readFile(new URL(name, assets)));
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage();
  await page.goto(`${origin}/scripts/fixtures/cursor.html`);
  const pixelCheck = await page.evaluate(async names => {
    const pixels = async src => {
      const img = new Image(); img.src = src; await img.decode();
      const canvas = document.createElement("canvas");
      canvas.width = img.width; canvas.height = img.height;
      const ctx = canvas.getContext("2d"); ctx.drawImage(img, 0, 0);
      return { data: ctx.getImageData(0, 0, img.width, img.height).data, width: img.width, height: img.height };
    };
    let checked = 0;
    for (const name of names.filter(name => name.includes("yukino-cursor"))) {
      const baseName = name.slice(name.indexOf("yukino-cursor"));
      const scale = Number(/@([\d.]+)x/.exec(name)?.[1] ?? 1);
      const base = await pixels(`/src/assets/${baseName}`);
      const frame = await pixels(`/src/assets/cursor-motion/${name}`);
      if (base.width !== frame.width || base.height !== frame.height) throw Error(`${name}: cursor size changed`);
      let pointerChanged = false;
      for (let y = 0; y < base.height; y++) for (let x = 0; x < base.width; x++) {
        const index = (y * base.width + x) * 4;
        for (let c = 0; c < 4; c++) if (base.data[index + c] !== frame.data[index + c]) {
          if (x >= 16 * scale || y >= 20 * scale) throw Error(`${name}: character pixel changed at ${x},${y}`);
          pointerChanged = true;
        }
      }
      if (!pointerChanged) throw Error(`${name}: pointer did not change`);
      // Anti-alias the sharp tip while keeping the native hotspot fixed.
      if (frame.data[3] === 0) throw Error(`${name}: hotspot lost`);
      checked++;
    }
    return checked;
  }, names);
  assert.equal(pixelCheck, 96);
  await page.evaluate(async () => { await import("/src/pointerMotion.ts"); });
  await page.locator("#portal button").hover();
  await page.waitForFunction(() => document.documentElement.dataset.loomCursorFrame === "scale-14");
  assert.match(await page.locator("#portal button").evaluate(el => getComputedStyle(el).cursor), /cursor-motion\/scale-14-yukino-cursor-white.*0 0/);
  await page.evaluate(() => {
    window.cursorTransitions = [];
    new MutationObserver(() => window.cursorTransitions.push(document.documentElement.dataset.loomCursorFrame))
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-loom-cursor-frame"] });
  });
  await page.mouse.down();
  await page.waitForFunction(() => document.documentElement.dataset.loomCursorFrame === "scale-2");
  await page.mouse.up();
  await page.waitForFunction(() => /^scale-(?:[3-9]|1[0-3])$/.test(document.documentElement.dataset.loomCursorFrame || ""));
  await page.waitForFunction(() => document.documentElement.dataset.loomCursorFrame === "scale-14");
  assert.ok(await page.evaluate(() => new Set(window.cursorTransitions.filter(Boolean)).size >= 6), "click should traverse intermediate frames rather than jump between two bitmaps");
  await page.mouse.move(300, 300);
  await page.mouse.move(500, 300);
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-loom-cursor-frame"));
  await page.mouse.move(300, 300);
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-loom-cursor-frame"));
  await page.locator("#portal button").hover();
  await page.waitForFunction(() => document.documentElement.dataset.loomCursorFrame === "scale-14");
  await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-loom-cursor-frame"));
  await page.mouse.down(); await page.mouse.up();
  await page.waitForTimeout(220);
  assert.equal(await page.evaluate(() => document.documentElement.dataset.loomCursorFrame), undefined);
  await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "false"; });
  await page.waitForFunction(() => document.documentElement.dataset.loomCursorFrame === "scale-14");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-loom-cursor-frame"));
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.waitForFunction(() => document.documentElement.dataset.loomCursorFrame === "scale-14");
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-loom-cursor-frame"));
  await page.mouse.move(310, 310);
  await page.mouse.down();
  await page.waitForFunction(() => document.documentElement.dataset.loomCursorFrame === "scale-2");
  await page.evaluate(() => window.dispatchEvent(new PointerEvent("pointercancel", { pointerType: "mouse" })));
  await page.waitForFunction(() => document.documentElement.dataset.loomCursorFrame !== "scale-2");
  await page.mouse.up();
  await page.evaluate(() => document.documentElement.classList.add("loom-liquid-cursor-active"));
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-loom-cursor-frame"));
  console.log("PASS: 96 native animation bitmaps preserve character pixels and hotspot; stable movement, eased hover/press/release, reduced motion, blur and cancellation");
  // The portal retains its separate liquid-glass cursor. It must hide the
  // native character cursor and suspend native animation while mounted.
  await page.goto(origin, { waitUntil: "domcontentloaded" });
  await page.locator(".loom-portal-page").waitFor();
  await page.waitForFunction(() => document.documentElement.classList.contains("loom-liquid-cursor-active"));
  assert.equal(await page.locator(".loom-liquid-cursor-canvas").count(), 1);
  await page.locator(".loom-portal-brand").hover();
  assert.equal(await page.locator(".loom-portal-brand").evaluate(el => getComputedStyle(el).cursor), "none");
  await page.waitForFunction(() => getComputedStyle(document.querySelector(".loom-liquid-cursor-canvas")).opacity === "1");
  const before = await page.locator(".loom-liquid-cursor-canvas").evaluate(el => el.style.transform);
  await page.mouse.move(800, 50);
  await page.mouse.move(900, 50);
  await page.waitForFunction(before => document.querySelector(".loom-liquid-cursor-canvas").style.transform !== before, before);
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-loom-cursor-frame"));
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-loom-cursor-frame"));
  assert.equal(await page.locator(".loom-pointer-click-layer").count(), 0, "the refined pointer should not emit a large click ripple");
  console.log("PASS: login/connection page restores moving liquid glass, hides the native pointer and suspends native animation");
} finally { await browser.close(); }
