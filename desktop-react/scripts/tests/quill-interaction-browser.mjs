// The new-conversation quill (NewConversationRow) on its own: hovering only leans it (nothing plays), each new
// conversation is one stroke (a click, Enter, Space, the shortcut), a stroke still running is left to finish instead of
// starting over, onNew is never delayed, touch does not pretend to hover, and reduced motion plays nothing.
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
  const mark = () => row.getAttribute("data-stroke");
  const strokes = () => row.evaluate((node) => node.getAnimations({ subtree: true }).filter((a) => /^li-(stroke|strike)-/.test(a.animationName)).map((a) => a.animationName).sort().join(","));
  const count = () => page.locator("output").textContent();
  await open();
  assert.equal(await row.getAttribute("aria-label"), "New conversation");

  // Hovering leans the pen in and plays nothing of its own, however long the pointer stays.
  await row.hover();
  await page.waitForTimeout(400);
  assert.equal(await mark(), null);
  assert.equal(await strokes(), "");

  // A click is a new conversation at once, and one stroke.
  await row.click();
  assert.equal(await count(), "1");
  assert.equal(await mark(), "odd");
  assert.equal(await strokes(), "li-stroke-odd,li-strike-odd".split(",").sort().join(","));
  // Another click while the stroke runs starts the next conversation but leaves the stroke to finish.
  await row.evaluate((node) => { window.stroke = node.getAnimations({ subtree: true }).find((a) => a.animationName === "li-stroke-odd"); });
  await row.click();
  assert.equal(await count(), "2");
  assert.equal(await mark(), "odd", "a running stroke is not started over");
  assert.equal(await row.evaluate((node) => node.getAnimations({ subtree: true }).includes(window.stroke)), true);
  await page.waitForTimeout(520);
  assert.equal(await strokes(), "", "the stroke ends and holds nothing");
  await row.click();
  assert.equal(await mark(), "even", "the next stroke plays again");
  assert.equal(await count(), "3");

  // Keyboard: focus alone plays nothing; Enter and Space each start a conversation with a stroke.
  await open();
  await page.locator("#before").focus();
  await page.keyboard.press("Tab");
  await page.waitForTimeout(300);
  assert.equal(await mark(), null, "focus leans the pen in and plays nothing");
  await page.keyboard.press("Enter");
  assert.equal(await count(), "1");
  assert.equal(await mark(), "odd");
  await page.waitForTimeout(520);
  await page.keyboard.press("Space");
  assert.equal(await count(), "2");
  assert.equal(await mark(), "even");

  // The shortcut's handle strokes as well (the row's onNew is the app's, which the shortcut calls itself).
  await open();
  await page.locator("#shortcut").click();
  assert.equal(await mark(), "odd");
  assert.equal(await count(), "1");

  // Touch does not pretend to hover.
  await open();
  await row.dispatchEvent("pointerover", { pointerType: "touch" });
  await page.waitForTimeout(150);
  assert.equal(await mark(), null);

  for (const reduced of ["system", "app"]) {
    await open();
    if (reduced === "system") await page.emulateMedia({ reducedMotion: "reduce" });
    else {
      await page.emulateMedia({ reducedMotion: "no-preference" });
      await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "true"; });
    }
    await row.click();
    assert.equal(await count(), "1", "reduced motion never holds back the conversation");
    assert.equal(await row.locator(".li-scribe").evaluate((node) => getComputedStyle(node).animationName), "none");
    assert.equal(await row.locator(".li-barb").evaluate((node) => getComputedStyle(node).animationName), "none");
  }
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await open();
  await row.click();
  await page.locator("#unmount").dispatchEvent("click");
  await page.waitForTimeout(200);
  assert.deepEqual(errors, []);
  console.log("quill interaction: ok");
} finally {
  await browser.close();
}
