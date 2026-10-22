// The app's tooltip (GlobalTooltip): every `title` is shown in the app's own material instead of the system's box.
// Hovered, a control names itself after a short wait (a glyph sooner than words already on screen), under itself and
// inside the window, with its `title` held so the system has nothing to show, and given back when the pointer leaves;
// moving on to the next control while one shows, the next name comes at once and the tooltip glides there; a press
// sends it away for good while the pointer stays; keyboard focus shows it too, leaving the attribute alone; the quick
// tool dock keeps its own names.
import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?lang=en&theme=dark`);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  await page.waitForTimeout(600);
  const tip = () => page.evaluate(() => {
    const element = document.querySelector(".loom-tooltip");
    const box = element.getBoundingClientRect();
    return { on: element.dataset.on, text: element.textContent, glide: element.dataset.glide, side: element.dataset.side, top: box.top, bottom: box.bottom, left: box.left, right: box.right };
  });
  const center = async (selector) => {
    const box = await page.locator(selector).first().boundingBox();
    return { x: box.x + box.width / 2, y: box.y + box.height / 2, box };
  };
  const SEARCH = ".sidebar-controls .compact-search-toggle";
  const QUILL = ".sidebar-controls .sidebar-new-conversation";

  // A glyph names itself after a short wait, under itself, with its title held.
  await page.mouse.move(700, 500);
  const search = await center(SEARCH);
  await page.mouse.move(search.x, search.y);
  await page.waitForTimeout(200);
  assert.equal((await tip()).on, "false", "not at once: passing over controls does not flash names");
  assert.equal(await page.locator(SEARCH).getAttribute("title"), null, "the title is already held, before the system could show its own tooltip");
  await page.waitForFunction(() => document.querySelector(".loom-tooltip")?.dataset.on === "true", null, { timeout: 1500 });
  const shown = await tip();
  assert.equal(shown.text, "Search conversationsCtrl K", "the name, then the shortcut set apart");
  assert.equal(await page.locator(".loom-tooltip .loom-tooltip-keys").textContent(), "Ctrl K");
  assert.ok(shown.top >= search.box.y + search.box.height, `under the control (${shown.top} vs ${search.box.y + search.box.height})`);
  assert.ok(shown.left >= 8 && shown.right <= 1280 - 8, "inside the window");
  assert.deepEqual(await page.locator(SEARCH).evaluate((el) => [el.getAttribute("title"), el.dataset.loomTitle]), [null, "Search conversations · Ctrl K"], "the title is held, so the system shows nothing");

  // Moving on to the next control, its name comes at once and the tooltip glides there.
  const quill = await center(QUILL);
  await page.mouse.move(quill.x, quill.y);
  await page.waitForFunction(() => document.querySelector(".loom-tooltip")?.textContent?.startsWith("New conversation"), null, { timeout: 300 });
  assert.equal((await tip()).glide, "true");
  assert.equal(await page.locator(SEARCH).getAttribute("title"), "Search conversations · Ctrl K", "the previous control has its title back");

  // Leaving: gone, the title back.
  await page.mouse.move(700, 500);
  await page.waitForFunction(() => document.querySelector(".loom-tooltip")?.dataset.on === "false");
  assert.equal(await page.locator(QUILL).getAttribute("title"), "New conversation · Ctrl N");

  // A press sends it away, and it does not come back while the pointer stays.
  await page.waitForTimeout(800);
  const toggle = await center(".sidebar-controls .sidebar-toggle");
  await page.mouse.move(toggle.x, toggle.y);
  await page.waitForFunction(() => document.querySelector(".loom-tooltip")?.dataset.on === "true", null, { timeout: 1500 });
  await page.mouse.down();
  assert.equal((await tip()).on, "false");
  await page.mouse.up();
  await page.waitForTimeout(700);
  assert.equal((await tip()).on, "false", "no name again under a pointer that just pressed");
  await page.mouse.move(700, 500);
  await page.waitForTimeout(300);

  // The quick tool dock names its tools itself.
  const tool = await center('.loom-tool-button[data-tool-id="settings"]');
  await page.mouse.move(tool.x, tool.y);
  await page.waitForTimeout(900);
  assert.equal((await tip()).on, "false", "the dock's tools are named by the dock");
  await page.mouse.move(700, 500);

  // Keyboard focus shows it and leaves the attribute alone.
  await page.waitForTimeout(800);
  await page.evaluate(() => {
    const button = document.querySelector(".sidebar-controls .compact-search-toggle");
    button.blur();
  });
  await page.locator(".sidebar-controls .sidebar-toggle").focus();
  await page.keyboard.press("Tab");
  await page.waitForFunction(() => document.querySelector(".loom-tooltip")?.dataset.on === "true" && document.activeElement?.matches(".sidebar-controls .compact-search-toggle"), null, { timeout: 1500 });
  assert.equal(await page.locator(SEARCH).getAttribute("title"), "Search conversations · Ctrl K", "focus does not take the title");
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.querySelector(".loom-tooltip")?.dataset.on === "false");

  assert.deepEqual(errors, []);
  console.log("Tooltip passed: delayed then shown under the control in the app's material, title held and given back, glides on to the next control, a press sends it away, the dock keeps its own names, keyboard focus shows it.");
} finally {
  await browser.close();
}
