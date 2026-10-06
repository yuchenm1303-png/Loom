import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/cursor.html`);
  const pointerAsset = await page.evaluate(async () => {
    const load = async (src) => {
      const image = new Image();
      image.src = src;
      await image.decode();
      return image;
    };
    const cursor = getComputedStyle(document.body).cursor;
    const full = await load(cursor.match(/url\("?([^"\)]+)"?\)/)[1]);
    const blackFull = await load("/src/assets/yukino-cursor.png");
    const pointer = await load("/src/assets/yukino-pointer-white.png");

    const fullCanvas = document.createElement("canvas");
    fullCanvas.width = full.naturalWidth;
    fullCanvas.height = full.naturalHeight;
    const fullContext = fullCanvas.getContext("2d");
    fullContext.drawImage(full, 0, 0);
    const composite = fullContext.getImageData(0, 0, fullCanvas.width, fullCanvas.height).data;
    let decorationPixels = 0;
    for (let y = 0; y < fullCanvas.height; y++) {
      for (let x = 0; x < fullCanvas.width; x++) {
        if (x < pointer.naturalWidth && y < pointer.naturalHeight) continue;
        if (composite[(y * fullCanvas.width + x) * 4 + 3]) decorationPixels++;
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
      blackWidth: blackFull.naturalWidth,
      blackHeight: blackFull.naturalHeight,
      pointerWidth: pointer.naturalWidth,
      pointerHeight: pointer.naturalHeight,
      whiteFill: red(2, 2) === 255,
      extendedArms: [[15, 2], [2, 13]].every(([x, y]) => red(x, y) === 255 && alpha(x, y) > 180),
      openCorner: [[6, 5], [10, 10]].every(([x, y]) => alpha(x, y) === 0),
      decorationPixels,
    };
  });
  assert.deepEqual(
    [pointerAsset.width, pointerAsset.height, pointerAsset.blackWidth, pointerAsset.blackHeight],
    [32, 32, 32, 32],
    "full native cursor assets must stay within the Windows/Chromium 32x32 surface",
  );
  assert.deepEqual([pointerAsset.pointerWidth, pointerAsset.pointerHeight], [16, 14]);
  assert.ok(pointerAsset.whiteFill, "dark surfaces must use a white pointer");
  assert.ok(pointerAsset.extendedArms, "both pointer arms must keep the 16x14 SVG bounds");
  assert.ok(pointerAsset.openCorner, "the pointer must retain its open corner");
  assert.ok(pointerAsset.decorationPixels > 40, "the full cursor must contain visible character pixels outside the pointer");

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
  console.log("PASS: native cursor stays within 32x32 and retains the full character decoration");
  console.log("PASS: contrast switching, viewport edges, click passthrough and reduced motion");
} finally { await browser.close(); }
