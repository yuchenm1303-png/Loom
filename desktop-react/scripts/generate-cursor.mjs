// Run with the same Playwright/Chromium environment as scripts/tests/cursor-browser.mjs.
// Output is committed: cursor movement never requires JavaScript or canvas at runtime.
import { readFile, writeFile } from "node:fs/promises";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const assets = new URL("../src/assets/", import.meta.url);
const character = await readFile(new URL("yukino-mouse.png", assets));
const arrow = await readFile(new URL("cursor-arrow.svg", assets));
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  for (const [filename, color] of [["yukino-cursor.png", "#000"], ["yukino-cursor-white.png", "#fff"]]) {
    const png = await page.evaluate(async ({ character, arrow }) => {
      const load = async (url) => {
        const image = new Image();
        image.src = url;
        await image.decode();
        return image;
      };
      const [decoration, pointer] = await Promise.all([load(character), load(arrow)]);

      // yukino-mouse.png is now a pure, high-resolution character source. Crop
      // transparent padding first, then downsample once into the native cursor.
      // Keeping the source larger than the final 53px decoration preserves much
      // more face/hair detail than repeatedly rescaling an already tiny bitmap.
      const sourceLayer = document.createElement("canvas");
      sourceLayer.width = decoration.naturalWidth;
      sourceLayer.height = decoration.naturalHeight;
      const sourceContext = sourceLayer.getContext("2d");
      sourceContext.drawImage(decoration, 0, 0);
      const pixels = sourceContext.getImageData(0, 0, sourceLayer.width, sourceLayer.height).data;
      let left = sourceLayer.width;
      let top = sourceLayer.height;
      let right = 0;
      let bottom = 0;
      for (let y = 0; y < sourceLayer.height; y++) {
        for (let x = 0; x < sourceLayer.width; x++) {
          if (pixels[(y * sourceLayer.width + x) * 4 + 3]) {
            left = Math.min(left, x);
            top = Math.min(top, y);
            right = Math.max(right, x + 1);
            bottom = Math.max(bottom, y + 1);
          }
        }
      }
      if (right <= left || bottom <= top) throw new Error("cursor character source is empty");

      const characterMax = 53;
      const cropWidth = right - left;
      const cropHeight = bottom - top;
      const scale = Math.min(characterMax / cropWidth, characterMax / cropHeight);
      const characterWidth = Math.max(1, Math.round(cropWidth * scale));
      const characterHeight = Math.max(1, Math.round(cropHeight * scale));
      const offset = 12;
      const size = Math.max(
        offset + characterWidth,
        offset + characterHeight,
        pointer.naturalWidth,
        pointer.naturalHeight,
      ) + 2;

      const canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext("2d");
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(
        sourceLayer,
        left,
        top,
        cropWidth,
        cropHeight,
        offset,
        offset,
        characterWidth,
        characterHeight,
      );

      // The pointer stays independent from the character source. Draw the SVG at
      // native size with no smoothing so its shape/thickness and (0, 0) hotspot
      // remain exactly controllable from cursor-arrow.svg.
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(pointer, 0, 0);

      // Chromium skips oversized native cursors near viewport edges. A pointer-only
      // second CSS candidate stays below 32px and retains the exact same hotspot.
      const edgeCanvas = document.createElement("canvas");
      edgeCanvas.width = pointer.naturalWidth;
      edgeCanvas.height = pointer.naturalHeight;
      edgeCanvas.getContext("2d").drawImage(pointer, 0, 0);
      return {
        full: canvas.toDataURL("image/png").split(",")[1],
        edge: edgeCanvas.toDataURL("image/png").split(",")[1],
      };
    }, {
      character: `data:image/png;base64,${character.toString("base64")}`,
      arrow: `data:image/svg+xml;base64,${Buffer.from(arrow.toString().replace('fill="#000"', `fill="${color}"`)).toString("base64")}`,
    });
    await writeFile(new URL(filename, assets), Buffer.from(png.full, "base64"));
    await writeFile(new URL(filename.replace("yukino-cursor", "yukino-pointer"), assets), Buffer.from(png.edge, "base64"));
  }
} finally {
  await browser.close();
}
