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
  const png = await page.evaluate(async ({ character, arrow }) => {
    const load = async (url) => {
      const image = new Image();
      image.src = url;
      await image.decode();
      return image;
    };
    const [decoration, pointer] = await Promise.all([load(character), load(arrow)]);
    const canvas = document.createElement("canvas");
    canvas.width = decoration.naturalWidth;
    canvas.height = decoration.naturalHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(decoration, 0, 0);
    // Keep the character intact; replace the original tiny black arrow area only.
    ctx.clearRect(0, 0, 12, 14);
    ctx.drawImage(pointer, 0, 0);
    return canvas.toDataURL("image/png").split(",")[1];
  }, {
    character: `data:image/png;base64,${character.toString("base64")}`,
    arrow: `data:image/svg+xml;base64,${arrow.toString("base64")}`,
  });
  await writeFile(new URL("yukino-cursor.png", assets), Buffer.from(png, "base64"));
} finally { await browser.close(); }
