import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("loom.settings.language", "en"));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/image-input.html`, { waitUntil: "domcontentloaded", timeout: 60000 });
  const notice = page.locator(".error-row.is-input-notice");
  await notice.waitFor();
  assert.equal(await notice.getAttribute("role"), "status");
  assert.doesNotMatch(await notice.innerText(), /AITransportError|400|messages\[/);
  const editor = () => page.locator("textarea:visible").last();
  const send = () => page.locator(".send-button:visible:not(.stop)").last();
  const paste = async () => editor().evaluate(element => {
    const data = new DataTransfer();
    data.items.add(new File([new Uint8Array([137,80,78,71])], "fixture.png", { type: "image/png" }));
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true }));
  });
  for (const running of [false, true]) {
    if (running) await page.getByRole("button", { name: "Toggle running", exact: true }).click();
    await page.getByRole("button", { name: "Unknown", exact: true }).click();
    await paste();
    await page.getByText(/Image support for this model has not been confirmed/).waitFor();
    await page.getByRole("button", { name: "Text only", exact: true }).click();
    await editor().fill("Read this screenshot");
    assert.equal(await send().isDisabled(), true);
    await editor().press("Enter");
    assert.equal(await page.evaluate(() => window.imageFixture.sends.length), running ? 1 : 0);
    await page.getByRole("button", { name: "Vision", exact: true }).click();
    await send().click();
    await page.waitForFunction(n => window.imageFixture.sends.length === n, running ? 2 : 1);
    assert.equal(await page.evaluate(() => window.imageFixture.sends.at(-1).attachments.length), 1);
  }
  assert.deepEqual(errors, []);
  console.log("image input: neutral upstream notice, unknown warning, blocked new images and preserved attachments passed");
} finally { await browser.close(); }
