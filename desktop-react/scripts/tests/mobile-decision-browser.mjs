// A choice card on the phone answers like the composer would: tapping an option sends it.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
try {
  for (const width of [320, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 844 }, hasTouch: true, isMobile: true });
    page.setDefaultTimeout(15000);
    const errors = []; page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/mobile.html?decision=1`);
    await page.getByText("修复 Host git 挂起", { exact: true }).click();
    const option = page.locator(".decision-option").first();
    await option.waitFor();
    assert.equal(await option.isEnabled(), true, "the options take a tap");
    await option.tap();
    await page.waitForFunction(() => window.__shell.calls.some((c) => c.operation === "call" && c.args[0] === "turn/start"));
    const sent = await page.evaluate(() => window.__shell.calls.filter((c) => c.operation === "call" && c.args[0] === "turn/start").map((c) => c.args[1]));
    assert.equal(sent.length, 1);
    assert.equal(sent[0].threadId, "thread-long");
    assert.match(String(sent[0].input), /深挖神经接收机/, "the chosen option is the message");
    await page.locator(".decision-inline-receipt").waitFor();
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("A phone choice card sends the tapped option as the reply");
} finally { await browser.close(); }
