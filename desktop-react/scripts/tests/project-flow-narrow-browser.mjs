// A dock panel can take the hub below three columns (project-flow.css, @container pf-stage): the stage reads down
// by scrolling, every card keeps its place in that one column, and the composer stays on the first screen.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const measure = page => page.evaluate(() => {
  const stage = document.querySelector(".pf-stage");
  const box = node => { const { top, bottom, height } = node.getBoundingClientRect(); return { top, bottom, height }; };
  return { blocks: [...stage.children].filter(node => node.matches(".pf-col, .pf-loom")).map(box).sort((a, b) => a.top - b.top),
           cards: document.querySelectorAll(".pf-side .pf-card").length, composer: box(document.querySelector(".pf-composer")),
           scrolls: stage.scrollHeight > stage.clientHeight };
});
try {
  const page = await browser.newPage({ viewport: { width: 730, height: 1000 } });
  await page.goto(`${origin}/scripts/fixtures/project-flow.html?chat=1&turn=1`);
  await page.waitForSelector(".pf-composer");
  await page.waitForTimeout(600);
  const short = await measure(page);
  assert.ok(short.cards > 0 && short.scrolls, "the side cards stay, below the hub");
  short.blocks.forEach((block, index) => {
    assert.ok(block.height > 100, `block ${index} keeps its content: ${block.height}`);
    if (index) assert.ok(block.top >= short.blocks[index - 1].bottom - 0.5, `blocks do not overlap: ${JSON.stringify(short.blocks)}`);
  });
  await page.evaluate(() => { const pad = document.createElement("div"); pad.style.height = "1800px"; document.querySelector(".pf-thread").append(pad); });
  await page.waitForTimeout(200);
  const long = await measure(page);
  assert.ok(long.composer.bottom <= 1000, `a long conversation leaves the composer on screen: ${long.composer.bottom}`);
  await page.setViewportSize({ width: 1300, height: 900 });
  await page.waitForTimeout(300);
  assert.equal(await page.locator(".pf-side").first().evaluate(node => getComputedStyle(node).containerType), "size", "three columns keep their scrollers");
  console.log("Narrow hub: one column, nothing lost or overlapping, composer on the first screen.");
} finally { await browser.close(); }
