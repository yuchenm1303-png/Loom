// Verify Chromium's chosen native cursor, not merely the CSS declaration.
const assert = require("node:assert/strict");
const { app, BrowserWindow } = require("electron");
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const timeout = setTimeout(() => { console.error("Native cursor test timed out"); app.exit(1); }, 20000);
app.whenReady().then(async () => {
  const window = new BrowserWindow({ show: false, width: 1000, height: 750, useContentSize: true,
    webPreferences: { offscreen: true } });
  let cursor;
  window.webContents.on("cursor-changed", (_event, type, image, scale, size, hotspot) => {
    const bitmap = type === "custom" ? image.toBitmap() : null;
    const fill = bitmap ? bitmap[(2 * image.getSize().width + 2) * 4] : null;
    cursor = { type, scale, size, hotspot, fill };
  });
  try {
    await window.loadURL(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/cursor.html`);
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
    window.webContents.sendInputEvent({ type: "mouseMove", x: 999, y: 300 });
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
      const move = async (x, y) => {
        window.webContents.sendInputEvent({ type: "mouseMove", x, y });
        await delay(100);
        assert.equal(cursor?.type, "custom", `native cursor at ${x},${y}, zoom ${zoom}: ${JSON.stringify(cursor)}`);
        assert.deepEqual(cursor.hotspot, { x: 0, y: 0 });
        assert.equal(cursor.fill, theme === "dark" ? 255 : 0, "native pointer color must retain surface contrast");
      };
      await move(400, 400);
      assert.ok(cursor.size.width / cursor.scale > 32, "interior should keep the character cursor");
      for (const [x, y] of [[990, 200], [999, 300], [400, 740], [500, 749], [999, 749]]) {
        await move(x, y);
        assert.ok(cursor.size.width / cursor.scale <= 32 && cursor.size.height / cursor.scale <= 32,
          "edge must use the compact custom pointer");
      }
      await move(400, 400);
      assert.ok(cursor.size.width / cursor.scale > 32, "returning inward should restore the character");
    }
    }
    console.log("PASS: native cursor on right/bottom/corner, dark/light surfaces, 100%/130% zoom, hotspot and character restoration");
    clearTimeout(timeout);
    app.exit(0);
  } catch (error) {
    console.error(error);
    clearTimeout(timeout);
    app.exit(1);
  }
}).catch(error => { console.error(error); app.exit(1); });
