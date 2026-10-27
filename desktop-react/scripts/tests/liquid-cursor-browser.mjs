import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const shots = process.env.LOOM_CURSOR_SCREENSHOT_DIR;
if (shots) await mkdir(shots, { recursive: true });

try {
  for (const deviceScaleFactor of [1, 2]) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => {
      if (/Loom liquid cursor.*(?:failed|shader)/i.test(message.text())) errors.push(message.text());
    });
    await page.addInitScript(() => {
      // Observe the real shader inputs and pixels without adding debug APIs to
      // production. readPixels is synchronous here, before the buffer clears.
      window.lensProbe = { uniforms: {}, draws: 0, pixel: [] };
      const locations = new WeakMap();
      const proto = WebGLRenderingContext.prototype;
      const location = proto.getUniformLocation;
      proto.getUniformLocation = function(program, name) {
        const result = location.call(this, program, name);
        if (result) locations.set(result, name);
        return result;
      };
      for (const method of ["uniform1f", "uniform1i", "uniform2f", "uniform4f", "uniform2fv", "uniform4fv"]) {
        const original = proto[method];
        proto[method] = function(location, ...values) {
          const name = locations.get(location);
          if (name) window.lensProbe.uniforms[name] = ArrayBuffer.isView(values[0]) ? Array.from(values[0]) : values;
          return original.call(this, location, ...values);
        };
      }
      const draw = proto.drawArrays;
      proto.drawArrays = function(...args) {
        draw.apply(this, args);
        const probe = window.lensProbe;
        probe.draws++;
        const u = probe.uniforms;
        const position = u["u_positions[0]"];
        const resolution = u.u_resolution;
        const center = position && resolution ? [position[0] * resolution[0], (1 - position[1]) * resolution[1]] : null;
        if (center) {
          u.u_lensCenter = center;
          u.u_lensSize = u["u_lensSizes[0]"];
          u.u_dpr = [window.devicePixelRatio || 1];
          u.u_wobble = [u["u_lensExtra[0]"][0]];
          u.u_zoom = [u["u_lensExtra[0]"][2]];
          u.u_tint = [u["u_tintColors[0]"][3]];
          u.u_strength = [u["u_lensSettings[0]"][1]];
        }
        if (center) {
          const pixel = new Uint8Array(4);
          this.readPixels(Math.floor(position[0] * this.drawingBufferWidth), Math.floor(position[1] * this.drawingBufferHeight), 1, 1, this.RGBA, this.UNSIGNED_BYTE, pixel);
          probe.pixel = Array.from(pixel);
        }
      };
    });
    await page.goto(`${origin}/scripts/fixtures/liquid-cursor.html`);
    await page.waitForFunction(() => document.documentElement.classList.contains("loom-liquid-cursor-active"));
    const captureCheck = await page.evaluate(async () => {
      const { rasterizePortal } = await import("/src/components/portalLiquidCapture.ts");
      const sample = document.createElement("div");
      sample.style.cssText = "position:fixed;left:0;top:0;width:200px;height:200px;background:white;color:black;font:18px sans-serif;";
      sample.innerHTML = '<div style="width:19px;line-height:24px;overflow-wrap:anywhere">检查项目</div><div style="position:absolute;top:100px;width:100px;height:20px;border-bottom:3px solid red"></div>';
      const img = document.createElement("img");
      img.crossOrigin = "anonymous";
      img.style.cssText = "position:absolute;left:100px;top:0;width:100px;height:100px;object-fit:contain";
      img.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="100" height="20"><path fill="blue" d="M0 0h100v20H0z"/></svg>';
      sample.append(img);
      document.body.append(sample);
      await img.decode();
      const canvas = document.createElement("canvas");
      const scratch = document.createElement("canvas");
      canvas.width = scratch.width = canvas.height = scratch.height = 200;
      const ctx = canvas.getContext("2d");
      const texts = [];
      const fill = ctx.fillText;
      ctx.fillText = function(text, ...args) { texts.push(text); return fill.call(this, text, ...args); };
      rasterizePortal(sample, canvas, scratch, null, 0, 0, 200, 200, 1);
      const pixel = (x, y) => Array.from(ctx.getImageData(x, y, 1, 1).data);
      const result = { texts, imageMargin: pixel(150, 10), imageCentre: pixel(150, 50), border: pixel(50, 121) };
      sample.remove();
      return result;
    });
    assert.deepEqual(captureCheck.texts, ["检", "查", "项", "目"], "wrapped CJK words must paint each character once");
    assert.deepEqual(captureCheck.imageMargin, [255, 255, 255, 255], "contain keeps letterboxed margins");
    assert.deepEqual(captureCheck.imageCentre, [0, 0, 255, 255]);
    assert.deepEqual(captureCheck.border, [255, 0, 0, 255], "a bottom-only border survives sampling");
    const probe = () => page.evaluate(() => window.lensProbe);
    const settle = async () => {
      await page.evaluate(() => {
        window.lastLensDraw = window.lensProbe.draws;
        window.lastLensDrawTime = performance.now();
      });
      await page.waitForFunction(() => {
        const current = window.lensProbe.draws;
        if (window.lastLensDraw !== current) {
          window.lastLensDraw = current;
          window.lastLensDrawTime = performance.now();
        }
        return current > 0 && performance.now() - window.lastLensDrawTime > 180;
      }, null, { timeout: 7000 });
    };
    const shot = async name => { if (shots) await page.screenshot({ path: resolve(shots, `${name}-${deviceScaleFactor}x.png`) }); };
    const lensBox = () => page.evaluate(() => {
      const { uniforms: u } = window.lensProbe;
      const canvas = document.querySelector(".loom-liquid-cursor-canvas").getBoundingClientRect();
      const dpr = u.u_dpr[0];
      return { x: canvas.left + u.u_lensCenter[0] / dpr, y: canvas.top + u.u_lensCenter[1] / dpr,
        width: u.u_lensSize[0] / dpr, height: u.u_lensSize[1] / dpr,
        roi: { left: canvas.left, top: canvas.top, right: canvas.right, bottom: canvas.bottom } };
    });
    await page.mouse.move(720, 300);
    await settle();
    let state = await probe();
    assert.equal(state.pixel[3], 255, "the shader must draw a real opaque lens centre");
    assert.equal(state.uniforms.u_wobble[0], 0);
    assert.equal(state.uniforms.u_zoom[0], 1);
    assert.ok(Math.abs(state.uniforms.u_tint[0] - 0.05) < 0.0001);
    assert.ok(Math.abs(state.uniforms.u_strength[0] - 0.95) < 0.0001, "restore the original reference strength");
    let box = await lensBox();
    assert.ok(Math.abs(box.x - 720) < 0.2 && Math.abs(box.y - 268) < 0.2);
    assert.ok(Math.abs(box.width - 80) < 0.2 && Math.abs(box.height - 54) < 0.2);
    await shot("free");
    const idleDraws = state.draws;
    await page.waitForTimeout(200);
    assert.equal((await probe()).draws, idleDraws, "idle lenses must sleep");
    await page.mouse.move(860, 284);
    await settle();
    await shot("over-heading");

    for (const selector of ["#first", "#second", "#wide", "#tall", "#first"]) {
      await page.locator(selector).hover();
      await settle();
      box = await lensBox();
      const target = await page.locator(selector).boundingBox();
      assert.ok(Math.abs(box.x - (target.x + target.width / 2)) < 0.4, `${selector}: anchor X ${box.x} vs ${target.x + target.width / 2}`);
      assert.ok(Math.abs(box.y - (target.y + target.height / 2)) < 0.4, `${selector}: anchor Y`);
      assert.ok(Math.abs(box.width - target.width - 20) < 0.4, `${selector}: matched width`);
      assert.ok(Math.abs(box.height - target.height - 20) < 0.4, `${selector}: matched height`);
      assert.ok(box.x - box.width / 2 >= box.roi.left - 1 && box.x + box.width / 2 <= box.roi.right + 1, "capture contains the entire lens");
      assert.equal((await probe()).pixel[3], 255);
      await shot(selector.slice(1));
    }

    // A near-target click outside the native hit box forwards exactly once.
    const first = await page.locator("#first").boundingBox();
    await page.mouse.move(first.x + first.width + 10, first.y + first.height / 2);
    await settle();
    const moved = await page.locator("#first").boundingBox();
    await page.mouse.click(moved.x + moved.width + 4, moved.y + moved.height / 2);
    await settle();
    assert.equal(await page.locator("#first").getAttribute("data-clicks"), "1");
    // Native keyboard clicks keep native semantics.
    await page.locator("#first").focus();
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("#first").getAttribute("data-clicks"), "2");

    await page.locator("#wide").fill("检查项目，保留原生输入交互。");
    await page.locator("#wide").hover();
    await settle();
    assert.equal(await page.locator("#wide").inputValue(), "检查项目，保留原生输入交互。");
    await page.mouse.move(1180, 120, { steps: 6 });
    await settle();
    box = await lensBox();
    assert.ok(Math.abs(box.width - 80) < 0.2);

    await page.locator("#theme").click();
    await settle();
    assert.equal(await page.locator("main").getAttribute("data-loom-site-theme"), "light");
    await shot("light-switch");
    await page.locator("main").evaluate(el => { el.scrollTop = 500; });
    await page.locator("#below").hover();
    await settle();
    const below = await page.locator("#below").boundingBox();
    box = await lensBox();
    assert.ok(Math.abs(box.y - below.y - below.height / 2) < 0.4, "nested scroll updates anchor and capture");
    await shot("scrolled");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.waitForFunction(() => !document.documentElement.classList.contains("loom-liquid-cursor-active"));
    assert.notEqual(await page.locator("#below").evaluate(el => getComputedStyle(el).cursor), "none");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.waitForFunction(() => document.documentElement.classList.contains("loom-liquid-cursor-active"));
    await page.mouse.move(1150, 150);
    await settle();
    await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
    await page.waitForFunction(() => !document.documentElement.classList.contains("loom-liquid-cursor-active"));
    assert.deepEqual(errors, []);
    console.log(`PASS ${deviceScaleFactor}x: WebGL pixels, free/snap/release, wide/tall targets, sleep/wake, click/keyboard/input, theme, nested scroll, reduced motion`);
    await context.close();
  }

  // Exercise the rebuilt standalone homepage bundle, not just Vite's modules.
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const standaloneErrors = [];
  page.on("pageerror", error => standaloneErrors.push(error.message));
  await page.route("**/standalone/**", async route => {
    const relative = new URL(route.request().url()).pathname.replace("/standalone/", "");
    if (!/^(?:index\.html|assets\/[\w.-]+)$/.test(relative)) return route.abort();
    try {
      const body = await readFile(new URL(`../../../artifacts/loom-site/${relative}`, import.meta.url));
      const contentType = relative.endsWith(".js") ? "text/javascript" : relative.endsWith(".html") ? "text/html" : relative.endsWith(".png") ? "image/png" : "application/octet-stream";
      await route.fulfill({ body, contentType });
    } catch { await route.fulfill({ status: 404, body: "Missing test asset" }); }
  });
  await page.goto(`${origin}/standalone/index.html`);
  await page.waitForFunction(() => document.documentElement.classList.contains("loom-liquid-cursor-active"));
  await page.locator("#authButton").hover();
  await page.waitForFunction(() => getComputedStyle(document.querySelector(".loom-liquid-cursor-canvas")).opacity === "1");
  assert.equal(await page.locator(".loom-liquid-cursor-canvas").count(), 1);
  assert.equal(await page.locator("#authButton").evaluate(el => getComputedStyle(el).cursor), "none");
  await page.waitForTimeout(1400);
  if (shots) await page.screenshot({ path: resolve(shots, "standalone-home.png") });
  await page.locator("#themeSwitch").click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  await page.waitForTimeout(650);
  if (shots) await page.screenshot({ path: resolve(shots, "standalone-light.png") });
  assert.deepEqual(standaloneErrors, []);
  console.log("PASS standalone homepage: shared production bundle, native cursor replacement, theme switch");
  await page.close();
} finally {
  await browser.close();
}
