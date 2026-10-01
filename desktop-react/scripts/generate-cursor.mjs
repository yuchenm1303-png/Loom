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
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    // Separate the original marker from the decoration before laying them out.
    const characterLayer = document.createElement("canvas");
    characterLayer.width = decoration.naturalWidth;
    characterLayer.height = decoration.naturalHeight;
    const characterContext = characterLayer.getContext("2d");
    characterContext.drawImage(decoration, 0, 0);
    characterContext.clearRect(0, 0, 10, 9);
    const pixels = characterContext.getImageData(0, 0, characterLayer.width, characterLayer.height).data;
    let right = 0;
    let bottom = 0;
    for (let y = 0; y < characterLayer.height; y++) {
      for (let x = 0; x < characterLayer.width; x++) {
        if (pixels[(y * characterLayer.width + x) * 4 + 3]) {
          right = Math.max(right, x + 1);
          bottom = Math.max(bottom, y + 1);
        }
      }
    }
    // Equal X/Y offsets put the pointer diagonally upper-left of the character,
    // not directly above it. Trim unused padding to keep the native cursor compact.
    const offset = 18;
    const size = Math.max(offset + right, offset + bottom, pointer.naturalWidth, pointer.naturalHeight) + 2;
    canvas.width = size;
    canvas.height = size;
    ctx.drawImage(characterLayer, offset, offset);
    // The SVG defines the enlarged arm lengths, retaining the original stroke thickness.
    // Draw at its native size and keep the click hotspot at (0, 0).
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(pointer, 0, 0);
    return canvas.toDataURL("image/png").split(",")[1];
  }, {
    character: `data:image/png;base64,${character.toString("base64")}`,
    arrow: `data:image/svg+xml;base64,${Buffer.from(arrow.toString().replace('fill="#000"', `fill="${color}"`)).toString("base64")}`,
  });
  await writeFile(new URL(filename, assets), Buffer.from(png, "base64"));
  }
} finally { await browser.close(); }
