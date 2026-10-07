import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { assertIntactPng } from "../png-integrity.mjs";

// A damaged PNG renders as a blank or partial character without failing decode().
const assets = new URL("../../src/assets/", import.meta.url);
for (const name of (await readdir(assets)).filter((name) => /^yukino-.*\.png$/.test(name))) {
  assertIntactPng(name, await readFile(new URL(name, assets)));
}

const CURSOR_SIZE = 66;
const SCALES = [1, 1.5, 2, 3];
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${origin}/scripts/fixtures/cursor.html`);
  const pointerAsset = await page.evaluate(async (scales) => {
    const load = async (src) => {
      const image = new Image();
      image.src = src;
      await image.decode();
      return image;
    };
    const cursor = getComputedStyle(document.body).cursor;
    const full = await load(cursor.match(/url\("?([^"\)]+)"?\)/)[1]);
    const pointer = await load("/src/assets/yukino-pointer-white.png");
    const sizes = [];
    for (const scale of scales) {
      const suffix = scale === 1 ? "" : `@${scale}x`;
      const images = await Promise.all(["yukino-cursor", "yukino-cursor-white", "yukino-pointer", "yukino-pointer-white"]
        .map((name) => load(`/src/assets/${name}${suffix}.png`)));
      sizes.push(images.map((image) => [image.naturalWidth, image.naturalHeight]));
    }

    const fullCanvas = document.createElement("canvas");
    fullCanvas.width = full.naturalWidth;
    fullCanvas.height = full.naturalHeight;
    const fullContext = fullCanvas.getContext("2d");
    fullContext.drawImage(full, 0, 0);
    const composite = fullContext.getImageData(0, 0, fullCanvas.width, fullCanvas.height).data;
    let decorationPixels = 0;
    const decoration = [fullCanvas.width, fullCanvas.height, 0, 0];
    for (let y = 0; y < fullCanvas.height; y++) {
      for (let x = 0; x < fullCanvas.width; x++) {
        if (x < pointer.naturalWidth && y < pointer.naturalHeight) continue;
        if (!composite[(y * fullCanvas.width + x) * 4 + 3]) continue;
        decorationPixels++;
        decoration[0] = Math.min(decoration[0], x);
        decoration[1] = Math.min(decoration[1], y);
        decoration[2] = Math.max(decoration[2], x + 1);
        decoration[3] = Math.max(decoration[3], y + 1);
      }
    }

    const pointerCanvas = document.createElement("canvas");
    pointerCanvas.width = pointer.naturalWidth;
    pointerCanvas.height = pointer.naturalHeight;
    const ctx = pointerCanvas.getContext("2d");
    ctx.drawImage(pointer, 0, 0);
    const pixels = ctx.getImageData(0, 0, pointerCanvas.width, pointerCanvas.height).data;
    const alpha = (x, y) => pixels[(y * pointerCanvas.width + x) * 4 + 3];
    const red = (x, y) => pixels[(y * pointerCanvas.width + x) * 4];
    return {
      width: full.naturalWidth,
      height: full.naturalHeight,
      sizes,
      pointerWidth: pointer.naturalWidth,
      pointerHeight: pointer.naturalHeight,
      whiteFill: red(3, 7) === 255,
      slenderArrow: alpha(2, 7) > 180 && alpha(8, 10) > 180 && alpha(8, 16) === 0,
      cleanOutside: [[14, 2], [12, 17]].every(([x, y]) => alpha(x, y) === 0),
      decorationPixels,
      decoration,
    };
  }, SCALES);
  assert.deepEqual([pointerAsset.width, pointerAsset.height], [CURSOR_SIZE, CURSOR_SIZE]);
  pointerAsset.sizes.forEach((sizes, index) => {
    const scale = SCALES[index];
    const full = CURSOR_SIZE * scale;
    const edge = [16 * scale, 20 * scale];
    assert.deepEqual(sizes, [[full, full], [full, full], edge, edge], `${scale}x cursor and pointer bitmaps`);
  });
  assert.deepEqual([pointerAsset.pointerWidth, pointerAsset.pointerHeight], [16, 20]);
  assert.ok(pointerAsset.whiteFill, "dark surfaces must use a white pointer");
  assert.ok(pointerAsset.slenderArrow, "the triangular silhouette must stay filled and have no stem");
  assert.ok(pointerAsset.cleanOutside, "the arrow must have a clean silhouette without detached decoration");
  const [left, top, right, bottom] = pointerAsset.decoration;
  assert.ok(pointerAsset.decorationPixels > 1200 && right - left >= 44 && bottom - top >= 50,
    `the whole character must be visible, got ${right - left}x${bottom - top} (${pointerAsset.decorationPixels} px)`);
  assert.ok(left >= 12 && top >= 12, "the character must stay clear of the pointer");
  assert.ok(Math.abs(left + right - top - bottom) <= 2, "the pointer must sit diagonally upper-left of the character");

  // image-set() must pick the bitmap drawn for the display (the lowest
  // resolution >= devicePixelRatio) and pointerContrast must warm exactly that
  // resolution for both tones; any other variant showing up means a mismatch.
  for (const [deviceScaleFactor, suffix] of [[1, ""], [1.25, "@1.5x"], [1.5, "@1.5x"], [2, "@2x"]]) {
    const context = await browser.newContext({ deviceScaleFactor });
    const scaled = await context.newPage();
    await scaled.goto(`${origin}/scripts/fixtures/cursor.html`);
    await scaled.mouse.move(300, 300);
    const loaded = await (await scaled.waitForFunction(() => {
      // Vite dev also fetches every glob-matched asset as a JS module (?import).
      const names = [...new Set(performance.getEntriesByType("resource")
        .filter((entry) => entry.initiatorType === "img" || entry.initiatorType === "css")
        .map((entry) => decodeURIComponent(new URL(entry.name).pathname.split("/").pop()))
        .filter((name) => /^yukino-(cursor|pointer)/.test(name)))];
      return names.length >= 4 && names.sort();
    }, null, { timeout: 5000 })).jsonValue();
    assert.deepEqual(loaded, ["yukino-cursor", "yukino-cursor-white", "yukino-pointer", "yukino-pointer-white"]
      .map((name) => `${name}${suffix}.png`).sort(), `devicePixelRatio ${deviceScaleFactor}`);
    await context.close();
  }

  await page.evaluate(() => {
    const surface = document.createElement("div");
    surface.id = "contrast-surface";
    surface.style.cssText = "position:fixed;left:100px;top:100px;width:100px;height:100px;background:white";
    surface.innerHTML = '<span id="contrast-child">Transparent child</span>';
    document.body.append(surface);
  });
  await page.locator("#contrast-child").hover();
  await page.waitForFunction(() => document.documentElement.dataset.loomPointerTone === "black");
  assert.match(await page.locator("#contrast-child").evaluate(el => getComputedStyle(el).cursor), /yukino-cursor\.png.*0 0/);
  await page.evaluate(() => { document.querySelector("#contrast-surface").style.background = "black"; });
  await page.mouse.move(400, 400);
  await page.locator("#contrast-child").hover();
  await page.waitForFunction(() => document.documentElement.dataset.loomPointerTone === "white");
  assert.match(await page.locator("#contrast-child").evaluate(el => getComputedStyle(el).cursor), /yukino-cursor-white.*0 0/);
  await page.evaluate(() => document.querySelector("#contrast-surface").remove());

  for (const selector of [".stop", ".stop svg", ".stop rect", "input", "#portal button"]) {
    await page.locator(selector).hover();
    const cursor = await page.locator(selector).evaluate((el) => getComputedStyle(el).cursor);
    assert.match(cursor, /yukino-cursor.*0 0/);
  }
  const pseudo = await page.locator(".stop").evaluate((el) => getComputedStyle(el, "::after").cursor);
  assert.match(pseudo, /yukino-cursor.*0 0/);
  await page.locator(".stop").evaluate((el) => { el.disabled = true; });
  assert.match(await page.locator(".stop").evaluate((el) => getComputedStyle(el).cursor), /yukino-cursor.*0 0/);
  for (const [x, y] of [[990, 200], [999, 749], [500, 749], [500, 200]]) {
    await page.setViewportSize({ width: 1000, height: 750 });
    await page.mouse.move(x, y);
    assert.equal(await page.locator(".loom-custom-pointer-image").count(), 0, "pointer must not depend on a moving DOM image");
    assert.match(await page.locator("body").evaluate((el) => getComputedStyle(el).cursor), /yukino-cursor.*0 0/);
  }
  await page.evaluate(() => { document.documentElement.style.zoom = "1.3"; });
  await page.mouse.move(450, 300);
  assert.match(await page.locator("body").evaluate((el) => getComputedStyle(el).cursor), /yukino-cursor.*0 0/);
  await page.evaluate(() => { document.documentElement.style.removeProperty("zoom"); });
  await page.evaluate(() => {
    const end = performance.now() + 400;
    while (performance.now() < end) { /* simulate a heavy synchronous render */ }
  });
  assert.equal(await page.locator(".loom-custom-pointer-layer").count(), 0);
  await page.evaluate(() => {
    window.clicks = 0;
    document.querySelector("#portal button").addEventListener("click", () => window.clicks++);
  });
  await page.locator("#portal button").click();
  assert.equal(await page.evaluate(() => window.clicks), 1, "ripple must not intercept the button click");
  assert.equal(await page.locator(".loom-pointer-click-ripple").count(), 1);
  assert.equal(await page.locator(".loom-pointer-click-layer").evaluate((el) => getComputedStyle(el).pointerEvents), "none");
  await page.waitForFunction(() => !document.querySelector(".loom-pointer-click-ripple"));
  await page.evaluate(() => {
    for (let index = 0; index < 30; index++) document.dispatchEvent(new PointerEvent("pointerdown", {
      button: 0, isPrimary: true, pointerType: "mouse", clientX: 100, clientY: 100,
    }));
  });
  assert.ok(await page.locator(".loom-pointer-click-ripple").count() <= 8);
  await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
  await page.waitForFunction(() => !document.querySelector(".loom-pointer-click-ripple"));
  await page.mouse.click(150, 150);
  assert.equal(await page.locator(".loom-pointer-click-ripple").count(), 0);
  await page.evaluate(() => { delete document.documentElement.dataset.loomReducedMotion; });
  await page.mouse.click(150, 150, { button: "right" });
  assert.equal(await page.locator(".loom-pointer-click-ripple").count(), 0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.mouse.click(150, 150);
  assert.equal(await page.locator(".loom-pointer-click-ripple").count(), 0);
  console.log("PASS: intact assets, full 66px character, per-scale image-set bitmaps and matching preloads");
  console.log("PASS: contrast switching, viewport edges, click passthrough and reduced motion");
} finally { await browser.close(); }
