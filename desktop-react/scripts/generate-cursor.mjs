// Run with the same Playwright/Chromium environment as scripts/tests/cursor-browser.mjs.
// Output is committed: cursor movement never requires JavaScript or canvas at runtime.
import { readFile, writeFile } from "node:fs/promises";
import { assertIntactPng } from "./png-integrity.mjs";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const assets = new URL("../src/assets/", import.meta.url);
const character = await readFile(new URL("yukino-mouse.png", assets));
const arrow = await readFile(new URL("cursor-arrow.svg", assets));
assertIntactPng("yukino-mouse.png", character);

// Geometry in CSS pixels. Chromium accepts custom cursors up to 128 DIP, but
// shows those above 32 DIP only while they fit inside the viewport; styles.css
// lists the pointer-only image as the fallback near the right/bottom edges.
const CURSOR_SIZE = 66;
// Equal X/Y offsets keep the pointer diagonally upper-left of the character.
const CHARACTER_OFFSET = 12;
const CHARACTER_BOX = 52;
const FALLBACK_MAX = 32;
// styles.css offers these through image-set(), so Chromium picks a bitmap drawn
// for the display scale instead of stretching the 1x image (blurry at 150%).
const SCALES = [1, 1.5, 2, 3];

const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  for (const [tone, color] of [["", "#000"], ["-white", "#fff"]]) {
    for (const scale of SCALES) {
      const png = await page.evaluate(async ({ character, arrow, scale, cursorSize, characterOffset, characterBox, fallbackMax }) => {
        const load = async (url) => {
          const image = new Image();
          image.src = url;
          await image.decode();
          return image;
        };
        const [decoration, pointer] = await Promise.all([load(character), load(arrow)]);
        if (pointer.naturalWidth > fallbackMax || pointer.naturalHeight > fallbackMax) {
          throw new Error(`the pointer-only fallback must stay within ${fallbackMax} DIP`);
        }

        // Crop transparent padding from the high-resolution character source,
        // then downsample it only once into each device-scale bitmap.
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
            if (!pixels[(y * sourceLayer.width + x) * 4 + 3]) continue;
            left = Math.min(left, x);
            top = Math.min(top, y);
            right = Math.max(right, x + 1);
            bottom = Math.max(bottom, y + 1);
          }
        }
        if (right <= left || bottom <= top) throw new Error("cursor character source is empty");

        const cropWidth = right - left;
        const cropHeight = bottom - top;
        const box = characterBox * scale;
        const fit = Math.min(box / cropWidth, box / cropHeight);
        const characterWidth = Math.round(cropWidth * fit);
        const characterHeight = Math.round(cropHeight * fit);

        const canvas = document.createElement("canvas");
        canvas.width = cursorSize * scale;
        canvas.height = cursorSize * scale;
        const ctx = canvas.getContext("2d");
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        // Centre the character in its square box so its centre stays on the
        // pointer's diagonal.
        ctx.drawImage(
          sourceLayer,
          left,
          top,
          cropWidth,
          cropHeight,
          characterOffset * scale + Math.round((box - characterWidth) / 2),
          characterOffset * scale + Math.round((box - characterHeight) / 2),
          characterWidth,
          characterHeight,
        );

        // Keep the click hotspot and pointer geometry independent from the
        // decoration. The SVG owns the pointer shape and is rasterized per scale.
        const pointerWidth = pointer.naturalWidth * scale;
        const pointerHeight = pointer.naturalHeight * scale;
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(pointer, 0, 0, pointerWidth, pointerHeight);

        const edgeCanvas = document.createElement("canvas");
        edgeCanvas.width = pointerWidth;
        edgeCanvas.height = pointerHeight;
        const edgeContext = edgeCanvas.getContext("2d");
        edgeContext.imageSmoothingEnabled = false;
        edgeContext.drawImage(pointer, 0, 0, pointerWidth, pointerHeight);
        return {
          full: canvas.toDataURL("image/png").split(",")[1],
          edge: edgeCanvas.toDataURL("image/png").split(",")[1],
        };
      }, {
        character: `data:image/png;base64,${character.toString("base64")}`,
        arrow: `data:image/svg+xml;base64,${Buffer.from(arrow.toString().replace('fill="#000"', `fill="${color}"`)).toString("base64")}`,
        scale,
        cursorSize: CURSOR_SIZE,
        characterOffset: CHARACTER_OFFSET,
        characterBox: CHARACTER_BOX,
        fallbackMax: FALLBACK_MAX,
      });
      const suffix = scale === 1 ? "" : `@${scale}x`;
      await writeFile(new URL(`yukino-cursor${tone}${suffix}.png`, assets), Buffer.from(png.full, "base64"));
      await writeFile(new URL(`yukino-pointer${tone}${suffix}.png`, assets), Buffer.from(png.edge, "base64"));
    }
  }
} finally {
  await browser.close();
}
