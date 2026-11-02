import assert from "node:assert/strict";
import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&draft=1&theme=${theme}`);
    const settings = page.locator(".pf-settings");
    await settings.waitFor();
    assert.ok(await settings.locator(".pf-chip-button").nth(0).isVisible(), "access control is visible before a proposal");
    assert.equal(await settings.locator(".pf-chip-value").textContent(), "8M");
    assert.equal(await settings.locator(".pf-stepper-value").textContent(), "2");
    await settings.locator(".pf-chip-button").nth(0).click();
    await settings.locator(".pf-chip-button").nth(1).click();
    await settings.locator(".pf-stepper button").first().click();
    await page.locator(".pf-input textarea").fill("Keep this unsent request");
    await page.evaluate(() => window.projectControlsFixture.propose());
    await page.locator(".pf-proposal").waitFor();
    assert.equal(await settings.locator('[aria-pressed]').getAttribute("aria-pressed"), "true");
    assert.equal(await settings.locator(".pf-chip-value").textContent(), "16M");
    assert.equal(await settings.locator(".pf-stepper-value").textContent(), "1");
    assert.equal(await page.locator(".pf-input textarea").inputValue(), "Keep this unsent request");
    await page.locator(".pf-proposal-start").click();
    assert.deepEqual(await page.evaluate(() => [window.started[1], window.started[2], window.started[4]]), [1, 16000000, true]);
    await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&draft=1&theme=${theme}`);
    await settings.waitFor();
    await page.evaluate(() => window.projectControlsFixture.propose());
    await page.waitForFunction(() => document.querySelector(".pf-chip-value")?.textContent === "4M");
    assert.equal(await settings.locator('[aria-pressed]').getAttribute("aria-pressed"), "false", "untouched access defaults to sandbox");
    await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&theme=${theme}`);
    await settings.waitFor();
    assert.equal(await settings.locator("button").count(), 0, "live run settings stay read-only");
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Draft controls are always visible, choices survive proposals and reach Start; live settings stay read-only in both themes");
} finally { await browser.close(); }
