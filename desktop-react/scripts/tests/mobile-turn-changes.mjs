/**
 * The changed-files card on the phone (MobileTurnChanges.tsx): the desktop's dead Preview/Review are gone, a tap opens
 * the file's diff in place, targets are thumb-sized, figures share one column, nothing overflows.
 *
 *   npx vite --port 5199 --strictPort
 *   node scripts/tests/mobile-turn-changes.mjs [outDir]
 */
import assert from "node:assert/strict";
import fs from "node:fs";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const out = process.argv[2];
if (out) fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });

async function open(query, width) {
  const context = await browser.newContext({ viewport: { width, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true, colorScheme: "dark" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error" && !/404/.test(message.text())) errors.push(message.text().slice(0, 200)); });
  await page.goto(`${origin}/scripts/fixtures/mobile.html${query}`);
  await page.waitForTimeout(1200);
  await page.locator(".m-nav button").nth(1).click();
  await page.waitForTimeout(400);
  await page.getByText("升级 Electron 44").first().click();
  const card = page.locator(".m-changes").first();
  await card.waitFor();
  await card.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  return { page, card, errors, context };
}

const shot = async (card, name) => { if (out) await card.screenshot({ path: `${out}/${name}.png` }); };

function geometry(card) {
  return card.evaluate((root) => {
    const heads = [...root.querySelectorAll(".m-disclosure-head, .m-changes-static")];
    const visible = (element) => element.getClientRects().length && getComputedStyle(element).visibility !== "hidden";
    const box = root.getBoundingClientRect();
    return {
      heights: heads.filter(visible).map((element) => element.getBoundingClientRect().height),
      stats: [...root.querySelectorAll(".m-changes-stats")].filter(visible).map((element) => Math.round((box.right - element.getBoundingClientRect().right) * 2) / 2),
      overflow: root.scrollWidth - root.clientWidth,
      page: document.documentElement.scrollWidth - innerWidth,
      smallest: Math.min(...[...root.querySelectorAll(".m-changes-name, .m-changes-dir, .m-changes-stats, .m-changes-summary")].filter(visible).map((element) => parseFloat(getComputedStyle(element).fontSize))),
    };
  });
}

try {
  for (const width of [320, 390, 430]) {
    const two = await open("", width);
    assert.equal(await two.page.locator(".tc").count(), 0, "the desktop card is not on the phone");
    assert.equal(await two.card.getByRole("button", { name: /Preview|Review|预览|审查/ }).count(), 0, "no action that leads nowhere");
    const base = await geometry(two.card);
    assert.equal(base.page, 0); assert.equal(base.overflow, 0);
    assert.ok(base.heights.every((height) => height >= 44), JSON.stringify(base));
    assert.equal(new Set(base.stats).size, 1, `figures end in one column: ${base.stats}`);
    assert.ok(base.smallest >= 13, JSON.stringify(base));
    await shot(two.card, `two-${width}`);

    const head = two.card.locator(".m-disclosure-head").first();
    assert.equal(await head.getAttribute("aria-expanded"), "false");
    await head.click();
    await two.page.waitForTimeout(650);
    assert.equal(await head.getAttribute("aria-expanded"), "true");
    assert.ok(await two.card.locator(".wv-diff-line.is-add").first().isVisible(), "a tap shows the diff");
    assert.equal(await two.card.locator(".wv-diff-file").count(), 0, "the row already names the file");
    const code = await two.card.locator(".wv-sheet-output").first().evaluate((element) => ({ scroll: element.scrollHeight - element.clientHeight, sideways: element.scrollWidth - element.clientWidth, max: getComputedStyle(element).maxHeight }));
    assert.deepEqual(code, { scroll: 0, sideways: 0, max: "none" }, "the diff has no scroller of its own");
    assert.equal((await geometry(two.card)).page, 0);
    await shot(two.card, `two-open-${width}`);
    await head.click();
    await two.page.waitForTimeout(650);
    assert.equal(await head.getAttribute("aria-expanded"), "false");
    assert.equal(await two.card.locator(".wv-diff-line").first().isVisible(), false, "a second tap folds it away");
    assert.deepEqual(two.errors, []);
    await two.context.close();

    const one = await open("?changes=one", width);
    assert.equal(await one.card.locator(".m-changes-head").count(), 0, "one file needs no summary row above it");
    assert.equal(await one.card.locator(".m-changes-name").innerText(), "AccountDevicesPanel.tsx");
    await one.card.locator(".m-disclosure-head").click();
    await one.page.waitForTimeout(650);
    assert.equal(await one.card.locator(".wv-diff-line.is-add").count(), 3);
    assert.equal((await geometry(one.card)).page, 0);
    await shot(one.card, `one-open-${width}`);
    assert.deepEqual(one.errors, []);
    await one.context.close();

    const many = await open("?changes=many", width);
    const folded = await geometry(many.card);
    assert.equal(folded.page, 0); assert.equal(folded.overflow, 0);
    assert.equal(new Set(folded.stats).size, 1, `figures end in one column: ${folded.stats}`);
    assert.equal(await many.card.locator(".m-changes-name:visible").count(), 6, "the list folds past six");
    const more = many.card.locator(".m-changes-more > .m-disclosure-head");
    assert.match(await more.innerText(), /3/);
    assert.ok((await more.boundingBox()).height >= 44);
    await shot(many.card, `many-${width}`);
    await more.click();
    await many.page.waitForTimeout(650);
    assert.equal(await many.card.locator(".m-changes-name:visible").count(), 9);
    assert.equal(await many.card.locator(".m-changes-static").count(), 1, "a file with no recorded hunks has nothing to open");
    assert.equal((await geometry(many.card)).page, 0);
    await shot(many.card, `many-open-${width}`);
    assert.deepEqual(many.errors, []);
    await many.context.close();
  }
  console.log("mobile turn changes: all checks passed");
} finally {
  await browser.close();
}
