import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const open = async () => {
    await page.goto(`${origin}/scripts/fixtures/quill-interaction.html`);
    await page.locator(".sidebar-new-conversation").waitFor();
  };
  const row = page.locator(".sidebar-new-conversation");
  const mark = () => row.getAttribute("data-written");
  await open();
  // A passing pointer and a fast click must not start a delayed stroke.
  await row.dispatchEvent("pointerover", { pointerType: "mouse" });
  await row.dispatchEvent("pointerout", { pointerType: "mouse" });
  await page.waitForTimeout(170);
  assert.equal(await mark(), null);
  await row.click();
  await page.waitForTimeout(170);
  assert.equal(await mark(), null);
  assert.equal(await page.locator("output").textContent(), "1");

  await page.mouse.move(1, 1);
  await row.hover();
  await page.waitForTimeout(160);
  assert.equal(await mark(), "odd");
  // Save the actual animation object: pressing must preserve its identity and timeline.
  await row.evaluate((node) => { window.stroke = node.getAnimations({ subtree: true }).find((a) => a.animationName === "li-write-odd"); });
  await page.mouse.down();
  assert.equal(await row.evaluate((node) => node.getAnimations({ subtree: true }).includes(window.stroke)), true);
  await page.mouse.up();
  assert.equal(await mark(), "odd");
  assert.equal(await page.locator("output").textContent(), "2");
  await page.waitForTimeout(250);
  assert.equal(await row.evaluate((node) => node.getAnimations({ subtree: true }).some((a) => /^li-(write|flick)-/.test(a.animationName))), false);
  await page.waitForTimeout(600);
  assert.equal(await mark(), "odd", "remaining hovered must not loop");
  await page.mouse.move(1, 1);
  await row.hover();
  await page.waitForTimeout(150);
  assert.equal(await mark(), "even", "a fresh hover writes again");
  await row.evaluate((node) => { window.stroke = node.getAnimations({ subtree: true }).find((a) => a.animationName === "li-write-even"); });
  await page.mouse.move(1, 1);
  await row.hover();
  assert.equal(await mark(), "even", "reentry must let the previous stroke settle first");
  await page.waitForTimeout(220);
  assert.equal(await mark(), "odd");
  await page.mouse.move(1, 1);
  await page.waitForTimeout(220);
  assert.equal(await row.locator(".li-ink").evaluate((node) => getComputedStyle(node).opacity), "0");

  // Keyboard focus writes once; Enter creates immediately without a second stroke.
  await open();
  await page.locator("#before").focus();
  await page.keyboard.press("Tab");
  await page.waitForTimeout(150);
  assert.equal(await mark(), "odd");
  await row.hover();
  await page.waitForTimeout(150);
  assert.equal(await mark(), "odd", "focus and hover share one engagement");
  await page.keyboard.press("Enter");
  assert.equal(await mark(), "odd");
  assert.equal(await page.locator("output").textContent(), "1");
  await page.keyboard.press("Space");
  assert.equal(await page.locator("output").textContent(), "2");
  assert.equal(await mark(), "odd");
  await page.locator("#shortcut").click();
  assert.equal(await row.getAttribute("data-confirmed"), "odd");
  assert.equal(await mark(), "odd", "shortcut must not restart writing");
  // A shortcut while idle must not consume the next hover.
  await open();
  await page.locator("#shortcut").click();
  await row.hover();
  await page.waitForTimeout(150);
  assert.equal(await mark(), "odd");

  for (const reduced of ["system", "app"]) {
    await open();
    if (reduced === "system") await page.emulateMedia({ reducedMotion: "reduce" });
    else {
      await page.emulateMedia({ reducedMotion: "no-preference" });
      await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
    }
    await row.hover();
    await page.waitForTimeout(150);
    assert.equal(await row.locator(".li-scribe").evaluate((node) => getComputedStyle(node).animationName), "none");
    await page.locator("#shortcut").click();
    assert.equal(await row.locator("svg").evaluate((node) => getComputedStyle(node).animationName), "none");
  }
  await open();
  await row.dispatchEvent("pointerover", { pointerType: "touch" });
  await page.waitForTimeout(150);
  assert.equal(await mark(), null, "touch must not simulate a hover stroke");
  await row.dispatchEvent("pointerover", { pointerType: "mouse" });
  await page.locator("#unmount").dispatchEvent("click");
  await page.waitForTimeout(200);
  assert.deepEqual(errors, []);
  console.log("quill interaction: ok");
} finally {
  await browser.close();
}
