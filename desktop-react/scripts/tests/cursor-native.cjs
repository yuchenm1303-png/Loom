// Verify Chromium's chosen native cursor, not merely the CSS declaration.
const assert = require("node:assert/strict");
const { app, BrowserWindow } = require("electron");
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const timeout = setTimeout(() => { console.error("Native cursor test timed out"); app.exit(1); }, 20000);
// Offscreen windows otherwise inherit the host display scale; LOOM_CURSOR_DSF=1.5
// reproduces Windows 150% display scaling.
const deviceScaleFactor = Number(process.env.LOOM_CURSOR_DSF || 1);
app.commandLine.appendSwitch("force-device-scale-factor", String(deviceScaleFactor));
// Resolutions listed by the image-set() candidates in styles.css.
const imageSetScales = [1, 1.5, 2, 3];
app.whenReady().then(async () => {
  const window = new BrowserWindow({ show: false, width: 1000, height: 750, useContentSize: true,
    webPreferences: { offscreen: true } });
  let cursor;
  window.webContents.on("cursor-changed", (_event, type, image, scale, size, hotspot) => {
    const bitmap = type === "custom" ? image.toBitmap() : null;
    const fill = bitmap ? bitmap[(Math.floor(6 * scale) * image.getSize().width + Math.floor(4 * scale)) * 4] : null;
    cursor = { type, scale, size, hotspot, fill };
  });
  try {
    await window.loadURL(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/cursor.html`);
    // The window is clamped to the screen at large display scales; probe its real edges.
    const [width, height] = window.getContentSize();
    await window.webContents.executeJavaScript(`Promise.all([...getComputedStyle(document.body).cursor.matchAll(/url\\("?([^"\\)]+)"?\\)/g)].map(async match => {
      const image = new Image(); image.src = match[1]; await image.decode();
    }))`);
    // Reproduce the original failure before testing the two-candidate fix.
    await window.webContents.executeJavaScript(`{
      const full = getComputedStyle(document.body).cursor.match(/url\\("?([^"\\)]+)"?\\)/)[1];
      document.documentElement.style.setProperty("--loom-native-cursor", 'url("' + full + '") 0 0');
    }`);
    window.webContents.sendInputEvent({ type: "mouseMove", x: 400, y: 400 });
    await delay(150);
    assert.equal(cursor?.type, "custom");
    window.webContents.sendInputEvent({ type: "mouseMove", x: width - 1, y: 300 });
    await delay(150);
    assert.ok(cursor && cursor.type !== "custom", "single large cursor must reproduce the edge fallback");
    await window.webContents.executeJavaScript('document.documentElement.style.removeProperty("--loom-native-cursor")');
    for (const theme of ["dark", "light"]) {
    await window.webContents.executeJavaScript(`
      document.documentElement.dataset.loomTheme = ${JSON.stringify(theme)};
      document.documentElement.style.setProperty("--bg", ${JSON.stringify(theme === "dark" ? "black" : "white")});
      document.body.style.backgroundColor = ${JSON.stringify(theme === "dark" ? "black" : "white")};
      window.dispatchEvent(new CustomEvent("loom-theme-changed"));
    `);
    await delay(400);
    for (const zoom of [1, 1.3]) {
      window.webContents.setZoomFactor(zoom);
      await delay(100);
      const ratio = await window.webContents.executeJavaScript("devicePixelRatio");
      if (zoom === 1) assert.equal(ratio, deviceScaleFactor, "the display scale override must apply");
      const bitmapScale = imageSetScales.find(scale => scale >= ratio) ?? imageSetScales.at(-1);
      const move = async (x, y) => {
        window.webContents.sendInputEvent({ type: "mouseMove", x, y });
        await delay(100);
        assert.equal(cursor?.type, "custom", `native cursor at ${x},${y}, zoom ${zoom}: ${JSON.stringify(cursor)}`);
        assert.deepEqual(cursor.hotspot, { x: 0, y: 0 });
        assert.equal(cursor.fill, theme === "dark" ? 255 : 0, "native pointer color must retain surface contrast");
        assert.equal(cursor.scale, bitmapScale, `devicePixelRatio ${ratio} must use the ${bitmapScale}x bitmap`);
      };
      await move(400, 400);
      assert.equal(Math.round(cursor.size.width / cursor.scale), 66, "interior should keep the full character cursor");
      for (const [x, y] of [[width - 10, 200], [width - 1, 300], [400, height - 10], [500, height - 1], [width - 1, height - 1]]) {
        await move(x, y);
        assert.ok(cursor.size.width / cursor.scale <= 32 && cursor.size.height / cursor.scale <= 32,
          "edge must use the compact custom pointer");
      }
      await move(400, 400);
      assert.equal(Math.round(cursor.size.width / cursor.scale), 66, "returning inward should restore the character");
    }
    }
    // Also inspect Chromium's actual animated native cursors, including the
    // compact frame selected at the viewport edge and the click/release states.
    window.webContents.setZoomFactor(1);
    await window.webContents.executeJavaScript('import("/src/pointerMotion.ts").then(() => true)');
    await delay(400);
    window.webContents.sendInputEvent({ type: "mouseMove", x: 400, y: 400 });
    await delay(400);
    window.webContents.sendInputEvent({ type: "mouseDown", x: 400, y: 400, button: "left", clickCount: 1 });
    await delay(150);
    assert.equal(await window.webContents.executeJavaScript('document.documentElement.dataset.loomCursorFrame'), "scale-2");
    assert.equal(cursor?.type, "custom");
    assert.deepEqual(cursor.hotspot, { x: 0, y: 0 });
    assert.equal(Math.round(cursor.size.width / cursor.scale), 66);
    window.webContents.sendInputEvent({ type: "mouseUp", x: 400, y: 400, button: "left", clickCount: 1 });
    await delay(35);
    assert.match(await window.webContents.executeJavaScript('document.documentElement.dataset.loomCursorFrame'), /^scale-[3-9]$/);
    assert.deepEqual(cursor.hotspot, { x: 0, y: 0 });
    window.webContents.sendInputEvent({ type: "mouseMove", x: width - 1, y: 300 });
    window.webContents.sendInputEvent({ type: "mouseDown", x: width - 1, y: 300, button: "left", clickCount: 1 });
    await delay(50);
    assert.equal(cursor?.type, "custom");
    assert.deepEqual(cursor.hotspot, { x: 0, y: 0 });
    assert.equal(Math.round(cursor.size.width / cursor.scale), 16, "animated edge should retain the compact custom pointer");
    window.webContents.sendInputEvent({ type: "mouseUp", x: width - 1, y: 300, button: "left", clickCount: 1 });
    console.log(`PASS (display scale ${deviceScaleFactor}): native cursor on right/bottom/corner, dark/light surfaces, 100%/130% zoom, per-scale bitmap, hotspot and character restoration`);
    console.log("PASS: Chromium native press/release and animated edge fallback retain the exact hotspot");
    clearTimeout(timeout);
    app.exit(0);
  } catch (error) {
    console.error(error);
    clearTimeout(timeout);
    app.exit(1);
  }
}).catch(error => { console.error(error); app.exit(1); });
